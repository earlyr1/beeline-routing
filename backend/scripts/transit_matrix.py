"""Матрицы времени на общественном транспорте от 2ГИС: один регион или все за один запуск.

Запуск из каталога backend:
  python -m scripts.transit_matrix --region all --dry-run
  TWOGIS_API_KEY=... python -m scripts.transit_matrix --region all --departure 13:00
  TWOGIS_API_KEY=... python -m scripts.transit_matrix --region east

Точки берутся из бандла региона в том порядке, в каком их строит make_problem: сначала стартовые точки
инженеров, затем заявки с координатами. У каждого региона свой файл data/transit/<регион>.json (каталог
переопределяется TRANSIT_MATRIX_DIR), и сервис при следующем старте берёт оттуда минуты общественного транспорта
для пар точек, которые есть в файле.

Регионы считаются от самого дешёвого к самому дорогому: если ключ упрётся в лимит, потеряно будет меньше.
Посчитанный регион переживает обрыв — его файл остаётся на диске, и следующий запуск такой регион пропускает
(--force считает заново, --keep-going не останавливается на упавшем регионе).

Посчитанные матрицы лежат в репозитории (data/transit) и копируются в образ backend: скрипт их перезаписывает,
и чтобы сервис считал по новым, образ надо пересобрать. Ключ читается только из TWOGIS_API_KEY и никуда
не печатается.
"""

from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.domain.enums import Transport
from app.domain.models import Bundle
from app.domain.timeutil import fmt_hhmm, parse_hhmm
from app.geo.matrix import TrafficProfile, TravelModel, TravelTimes, build_base_matrix
from app.geo.osrm import LatLon
from app.geo.transit import (
    DEFAULT_PAUSE_S,
    DEMO_MAX_DISTANCE_KM,
    ELEMENTS_PER_MINUTE,
    KEY_ENV,
    REQUESTS_PER_MINUTE,
    REQUESTS_PER_MONTH,
    TransitClient,
    TransitError,
    build_transit_matrix,
    distance_groups,
    load_transit_matrix,
    request_pairs,
    save_transit_matrix,
    transit_matrix_path,
)
from app.ingest.bundle import load_bundle
from app.settings import Settings
from app.solvers.problem import problem_points

ALL_REGIONS = "all"
NO_KEY = (
    f"Нужен ключ 2ГИС в переменной окружения {KEY_ENV}.\n"
    "Демо-ключ на Distance Matrix API запрашивается в кабинете 2ГИС: https://dev.2gis.ru/order\n"
    "Сначала посчитайте расход и время флагом --dry-run: у демо-ключа месячный лимит запросов."
)
WHERE_FILES_GO = (
    "Матрицы записаны в data/transit/ — каталог лежит в репозитории и копируется в образ backend. "
    "Чтобы сервис считал по пересчитанным: docker compose up -d --build backend."
)


class RegionsNotFound(LookupError):
    """Считать нечего: такого региона нет в data/bundles или бандлов нет вовсе."""


def matrix_points(bundle: Bundle) -> list[LatLon]:
    """Точки задачи в порядке make_problem: старты инженеров, затем заявки с координатами."""
    return problem_points(bundle.engineers, bundle.requests)


def cost(points: Sequence[LatLon]) -> tuple[int, int]:
    """Сколько запросов и элементов матрицы стоит расчёт: блоки не больше 10 точек внутри групп близких точек."""
    pairs = request_pairs(points)
    return len(pairs), sum(len(sources) * len(targets) for sources, targets in pairs)


def far_pairs(points: Sequence[LatLon]) -> int:
    """Сколько упорядоченных пар точек лежит в разных группах: их демо-ключ не считает."""
    sizes = [len(group) for group in distance_groups(points)]
    return len(points) ** 2 - sum(size * size for size in sizes)


@dataclass(frozen=True)
class RegionJob:
    """Регион запуска: его точки, файл матрицы и признак «уже посчитан»."""

    region: str
    points: list[LatLon]
    path: Path
    done: bool

    @property
    def requests(self) -> int:
        return cost(self.points)[0]

    @property
    def elements(self) -> int:
        return cost(self.points)[1]

    def minutes(self, pause: float) -> int:
        """Сколько займёт расчёт: паузы между запросами, перед первым запросом паузы нет."""
        return math.ceil(max(self.requests - 1, 0) * pause / 60)


def _already_computed(path: Path, points: Sequence[LatLon]) -> bool:
    """Файл региона уже посчитан на этих же точках: заново считать нечего."""
    saved = load_transit_matrix(path)
    return saved is not None and saved.matches(points)


def _known_regions(settings: Settings) -> list[str]:
    return sorted(path.parent.name for path in settings.bundles_dir.glob("*/bundle.json"))


def plan_jobs(settings: Settings, region: str, force: bool) -> list[RegionJob]:
    """Регионы запуска от самого дешёвого к самому дорогому: ранний обрыв обходится дешевле."""
    known = _known_regions(settings)
    if region == ALL_REGIONS:
        if not known:
            raise RegionsNotFound(f"В {settings.bundles_dir} нет ни одного бандла региона.")
        names = known
    else:
        bundle_path = settings.bundles_dir / region / "bundle.json"
        if not bundle_path.exists():
            raise RegionsNotFound(
                f"Нет бандла региона «{region}»: {bundle_path}. "
                f"Готовые регионы: {', '.join(known) or 'ни одного'}"
            )
        names = [region]
    jobs = []
    for name in names:
        points = matrix_points(load_bundle(settings.bundles_dir / name / "bundle.json"))
        path = transit_matrix_path(settings.transit_dir, name)
        jobs.append(RegionJob(name, points, path, done=not force and _already_computed(path, points)))
    return sorted(jobs, key=lambda job: (len(job.points), job.region))


def plan_lines(jobs: Sequence[RegionJob], departure: str, pause: float) -> list[str]:
    """План расхода до первого запроса: сколько стоит каждый регион и сколько уйдёт всего."""
    lines = [f"выезд {departure}, регионов: {len(jobs)}"]
    for job in jobs:
        already = ", уже посчитан" if job.done else ""
        far = far_pairs(job.points)
        apart = (
            f", пар дальше {DEMO_MAX_DISTANCE_KM:.0f} км без 2ГИС: {far} (там встроенная модель)"
            if far
            else ""
        )
        lines.append(
            f"регион {job.region}: точек: {len(job.points)}, запросов: {job.requests}, "
            f"элементов: {job.elements}, минут: {job.minutes(pause)}{apart}{already}"
        )
    todo = [job for job in jobs if not job.done]
    requests = sum(job.requests for job in todo)
    minutes = sum(job.minutes(pause) for job in todo)
    skipped = len(jobs) - len(todo)
    already = f", уже посчитано регионов: {skipped}" if skipped else ""
    share = 100.0 * requests / REQUESTS_PER_MONTH
    lines.append(
        f"итого: запросов: {requests} из {REQUESTS_PER_MONTH} месячного лимита ({share:.1f}%), "
        f"минут: {minutes}{already}"
    )
    return lines


def dry_run_lines(pause: float) -> list[str]:
    return [
        f"лимиты демо-ключа: {REQUESTS_PER_MINUTE} запросов и {ELEMENTS_PER_MINUTE} элементов в минуту, "
        f"{REQUESTS_PER_MONTH} запросов в месяц",
        f"темп: пауза {pause:.0f} с между запросами",
        "Пробный расчёт: в сеть не ходили, файлы не записаны.",
    ]


def model_minutes(points: Sequence[LatLon], pairs: Sequence[tuple[int, int]]) -> list[int]:
    """Минуты встроенной модели на тех же парах: быстрее из «пешком» и поездки по расстоянию по прямой, без OSRM."""
    model = TravelModel()
    travel = TravelTimes(build_base_matrix(points, model), model, TrafficProfile({}))
    return [travel.minutes(i, j, Transport.PUBLIC, 0) for i, j in pairs]


def _spread(values: Sequence[int]) -> str:
    return f"мин {min(values)}, медиана {round(statistics.median(values))}, макс {max(values)}"


def summary_lines(points: Sequence[LatLon], minutes: Sequence[Sequence[int | None]]) -> list[str]:
    """Что получилось и насколько далека была наша оценка: одни и те же пары у 2ГИС и у встроенной модели."""
    size = len(points)
    group_of = {point: number for number, group in enumerate(distance_groups(points)) for point in group}
    pairs = [(i, j) for i in range(size) for j in range(size) if i != j]
    near = [(i, j) for i, j in pairs if group_of[i] == group_of[j]]
    known = [(i, j) for i, j in near if isinstance(minutes[i][j], int | float)]
    far = len(pairs) - len(near)
    apart = f", дальше {DEMO_MAX_DISTANCE_KM:.0f} км (не считали): {far}" if far else ""
    lines = [f"точек: {size}, пар: {len(pairs)}, без маршрута: {len(near) - len(known)}{apart}"]
    if not known:
        return [
            *lines,
            "2ГИС не нашёл ни одного маршрута: матрица пустая, сервис будет считать встроенной моделью",
        ]
    # В known только пары с числом: условие на None здесь для mypy, он не видит проверку isinstance выше.
    found = [int(value) for i, j in known if (value := minutes[i][j]) is not None]
    return [
        *lines,
        f"2ГИС, минуты: {_spread(found)}",
        f"встроенная модель на тех же парах: {_spread(model_minutes(points, known))}",
    ]


def _progress(done: int, total: int) -> None:
    print(f"блок {done}/{total}", flush=True)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Считает матрицы времени на общественном транспорте через Distance Matrix API 2ГИС"
    )
    parser.add_argument(
        "--region", required=True, help=f"регион из data/bundles, например east, или {ALL_REGIONS} — все"
    )
    parser.add_argument("--departure", default="13:00", help="время выезда HH:MM, по умолчанию 13:00")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE_S, help="пауза между запросами, секунды")
    parser.add_argument("--force", action="store_true", help="считать заново и те регионы, что уже посчитаны")
    parser.add_argument(
        "--keep-going", action="store_true", help="не останавливаться на регионе, который не посчитался"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="напечатать план расхода запросов, не выходя в сеть"
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

    try:
        jobs = plan_jobs(settings, args.region, args.force)
    except RegionsNotFound as error:
        print(str(error), file=sys.stderr)
        return 2

    for line in plan_lines(jobs, departure, args.pause):
        print(line)
    if args.dry_run:
        for line in dry_run_lines(args.pause):
            print(line)
        return 0

    client = None
    if any(not job.done for job in jobs):
        key = (env.get(KEY_ENV) or "").strip()
        if not key:
            print(NO_KEY, file=sys.stderr)
            return 2
        client = TransitClient(key, pause_s=args.pause)

    computed, skipped, failed, spent, untouched = 0, 0, 0, 0, 0
    for index, job in enumerate(jobs):
        if job.done:
            skipped += 1
            print(f"регион {job.region}: уже посчитан, пропущен — {job.path}")
            continue
        print(f"регион {job.region}: считаем, запросов: {job.requests}")
        assert client is not None  # клиента нет, только когда посчитаны все регионы
        try:
            minutes = client.matrix(job.points, departure, progress=_progress)
        except TransitError as error:
            failed += 1
            print(f"регион {job.region}: 2ГИС не дал матрицу: {error}", file=sys.stderr)
            if not args.keep_going:
                untouched = len(jobs) - index - 1
                break
            continue
        spent += job.requests
        save_transit_matrix(build_transit_matrix(job.points, minutes, departure, job.region), job.path)
        computed += 1
        for line in summary_lines(job.points, minutes):
            print(line)
        print(f"матрица записана: {job.path}")

    unknown = " (у региона с ошибкой часть запросов тоже ушла)" if failed else ""
    print(
        f"посчитано регионов: {computed}, пропущено: {skipped}, с ошибкой: {failed}, "
        f"запросов потрачено: {spent}{unknown}"
    )
    if untouched:
        print(f"не тронуто регионов: {untouched} — посчитанное осталось на диске, запустите ещё раз")
    print(WHERE_FILES_GO)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
