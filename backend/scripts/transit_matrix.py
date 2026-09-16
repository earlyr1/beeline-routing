"""Матрица времени на общественном транспорте от 2ГИС для одного региона.

Запуск из каталога backend:
  python -m scripts.transit_matrix --region east --departure 13:00 --dry-run
  TWOGIS_API_KEY=... python -m scripts.transit_matrix --region east --departure 13:00

Точки берутся из бандла региона ровно в том порядке, в каком их строит make_problem: сначала стартовые точки
инженеров, затем заявки с координатами. Результат ложится в data/transit_matrix.json (путь переопределяется
TRANSIT_MATRIX_PATH), и сервис при следующем старте берёт минуты общественного транспорта оттуда.

Условия 2ГИС запрещают хранить результаты: файл локальный, коммитить его нельзя, после демо его можно удалить.
Ключ читается только из TWOGIS_API_KEY и никуда не печатается.
"""

from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from app.domain.enums import Transport
from app.domain.models import Bundle
from app.domain.timeutil import fmt_hhmm, parse_hhmm
from app.geo.matrix import TrafficProfile, TravelModel, TravelTimes, build_base_matrix
from app.geo.osrm import LatLon
from app.geo.transit import (
    DEFAULT_PAUSE_S,
    ELEMENTS_PER_MINUTE,
    KEY_ENV,
    MAX_BLOCK,
    REQUESTS_PER_MINUTE,
    REQUESTS_PER_MONTH,
    TransitClient,
    TransitError,
    build_transit_matrix,
    save_transit_matrix,
    split_blocks,
)
from app.ingest.bundle import load_bundle
from app.settings import Settings

NO_KEY = (
    f"Нужен ключ 2ГИС в переменной окружения {KEY_ENV}.\n"
    "Демо-ключ на Distance Matrix API запрашивается в кабинете 2ГИС: https://dev.2gis.ru/order\n"
    "Сначала посчитайте расход и время флагом --dry-run: у демо-ключа месячный лимит запросов."
)
DO_NOT_COMMIT = (
    "Файл матрицы коммитить нельзя: условия 2ГИС запрещают хранить результаты. "
    "Он в .gitignore, после демо удалите его."
)


def matrix_points(bundle: Bundle) -> list[LatLon]:
    """Точки задачи в порядке make_problem: старты инженеров, затем заявки с координатами."""
    located = [r for r in bundle.requests if r.lat is not None and r.lon is not None]
    return [(e.start_lat, e.start_lon) for e in bundle.engineers] + [(r.lat, r.lon) for r in located]


def cost(count: int) -> tuple[int, int]:
    """Сколько запросов и элементов матрицы стоит расчёт: блоки не больше 25 точек с каждой стороны."""
    return len(split_blocks(count, MAX_BLOCK)) ** 2, count * count


def model_minutes(points: Sequence[LatLon], pairs: Sequence[tuple[int, int]]) -> list[int]:
    """Минуты встроенной модели на тех же парах: по прямой ×1.3 при 15 км/ч плюс ожидание, без OSRM."""
    model = TravelModel()
    travel = TravelTimes(build_base_matrix(points, model), model, TrafficProfile({}))
    return [travel.minutes(i, j, Transport.PUBLIC, 0) for i, j in pairs]


def _spread(values: Sequence[int]) -> str:
    return f"мин {min(values)}, медиана {round(statistics.median(values))}, макс {max(values)}"


def summary_lines(points: Sequence[LatLon], minutes: Sequence[Sequence[int | None]]) -> list[str]:
    """Что получилось и насколько далека была наша оценка: одни и те же пары у 2ГИС и у встроенной модели."""
    size = len(points)
    pairs = [(i, j) for i in range(size) for j in range(size) if i != j]
    known = [(i, j) for i, j in pairs if isinstance(minutes[i][j], int | float)]
    lines = [f"точек: {size}, пар: {len(pairs)}, без маршрута: {len(pairs) - len(known)}"]
    if not known:
        return [*lines, "2ГИС не нашёл ни одного маршрута: матрица пустая, сервис будет считать по-старому"]
    return [
        *lines,
        f"2ГИС, минуты: {_spread([int(minutes[i][j]) for i, j in known])}",
        f"встроенная модель на тех же парах: {_spread(model_minutes(points, known))}",
    ]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Считает матрицу времени на общественном транспорте через Distance Matrix API 2ГИС"
    )
    parser.add_argument("--region", required=True, help="регион из data/bundles, например east")
    parser.add_argument("--departure", default="13:00", help="время выезда HH:MM, по умолчанию 13:00")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE_S, help="пауза между запросами, секунды")
    parser.add_argument(
        "--dry-run", action="store_true", help="напечатать расход запросов и элементов, не выходя в сеть"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    args = _parse_args(argv)
    settings = Settings.from_env(env)

    try:
        departure = fmt_hhmm(parse_hhmm(args.departure))
    except ValueError:
        print(f"Время выезда задаётся как HH:MM, получено «{args.departure}»", file=sys.stderr)
        return 2

    bundle_path = settings.bundles_dir / args.region / "bundle.json"
    if not bundle_path.exists():
        known = sorted(path.parent.name for path in settings.bundles_dir.glob("*/bundle.json"))
        print(
            f"Нет бандла региона «{args.region}»: {bundle_path}. "
            f"Готовые регионы: {', '.join(known) or 'ни одного'}",
            file=sys.stderr,
        )
        return 2

    points = matrix_points(load_bundle(bundle_path))
    requests, elements = cost(len(points))
    print(
        f"регион {args.region}, выезд {departure}: точек: {len(points)}, запросов: {requests}, элементов: {elements}"
    )
    if args.dry_run:
        print(
            f"лимиты демо-ключа: {REQUESTS_PER_MINUTE} запросов и {ELEMENTS_PER_MINUTE} элементов в минуту, "
            f"{REQUESTS_PER_MONTH} запросов в месяц"
        )
        print(
            f"темп: пауза {args.pause:.0f} с между запросами, расчёт займёт около "
            f"{math.ceil((requests - 1) * args.pause / 60)} мин"
        )
        print(f"месячный лимит: уйдёт {requests} запросов из {REQUESTS_PER_MONTH}")
        print("Пробный расчёт: в сеть не ходили, файл не записан.")
        return 0

    key = (env.get(KEY_ENV) or "").strip()
    if not key:
        print(NO_KEY, file=sys.stderr)
        return 2

    client = TransitClient(key, pause_s=args.pause)
    try:
        minutes = client.matrix(
            points, departure, progress=lambda done, total: print(f"блок {done}/{total}", flush=True)
        )
    except TransitError as error:
        print(f"2ГИС не дал матрицу: {error}", file=sys.stderr)
        return 1

    path = Path(settings.transit_matrix_path)
    save_transit_matrix(build_transit_matrix(points, minutes, departure), path)
    for line in summary_lines(points, minutes):
        print(line)
    print(f"матрица записана: {path}")
    print(DO_NOT_COMMIT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
