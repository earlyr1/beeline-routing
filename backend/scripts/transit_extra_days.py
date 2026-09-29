"""Матрицы 2ГИС для дополнительных дней организаторов (Восток, Юго-восток и Югоцентр, дни 2 и 3).

Запуск из каталога backend, без VPN (2ГИС режет зарубежные адреса):
  .venv/bin/python -m scripts.transit_extra_days --dry-run     # план: сколько запросов и минут
  .venv/bin/python -m scripts.transit_extra_days               # считать

Точки дней лежат в data/transit_extra_points.json: заявки дней, геокодированные сервисом. Из них заранее убраны
точки, которые совпали бы с точками дня 17.08: с ними новые матрицы подменили бы минуты 17.08, и ночные планы
четырёх регионов перестали бы подходить по отпечатку задачи. Файлы матриц называются x_<регион>_<день>.json:
при равном смещении точек сервис берёт матрицу, которая идёт раньше по имени, то есть исходную.

Лимиты демо-ключа: 10 запросов в минуту и 1000 в месяц. Пауза между запросами 7.5 с, как у scripts.transit_matrix.
Каждый ответ сразу пишется в data/transit/.partial/<имя>.json: если ключ упрётся в лимит или связь оборвётся,
повторный запуск продолжит с того же запроса и не потратит сделанные запросы второй раз. --budget ограничивает
число запросов за запуск. Ключ берётся из TWOGIS_API_KEY или из строки TWOGIS_API_KEY= в .env корня репозитория
и никуда не печатается.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from app.geo.transit import (
    DEFAULT_PAUSE_S,
    KEY_ENV,
    TransitClient,
    TransitError,
    build_transit_matrix,
    request_pairs,
    save_transit_matrix,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
POINTS_FILE = REPO_ROOT / "data" / "transit_extra_points.json"
TRANSIT_DIR = REPO_ROOT / "data" / "transit"
PARTIAL_DIR = TRANSIT_DIR / ".partial"
DEPARTURE = "13:00"  # как у матриц 17.08
# Югоцентр первым: одна матрица на два дня.
ORDER = ["x_south_center_days23", "x_east_day2", "x_east_day3", "x_south_east_day2", "x_south_east_day3"]


def read_key() -> str | None:
    key = os.environ.get(KEY_ENV, "").strip()
    if key:
        return key
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{KEY_ENV}="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return None


def load_partial(name: str, points: list[list[float]]) -> tuple[list[list[int | None]], set[int]]:
    path = PARTIAL_DIR / f"{name}.json"
    size = len(points)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("points") == points:
            return data["minutes"], set(data["done"])
    return [[None] * size for _ in range(size)], set()


def save_partial(
    name: str, points: list[list[float]], minutes: list[list[int | None]], done: set[int]
) -> None:
    PARTIAL_DIR.mkdir(parents=True, exist_ok=True)
    path = PARTIAL_DIR / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"points": points, "minutes": minutes, "done": sorted(done)}), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="только план запросов, без обращения к 2ГИС")
    parser.add_argument("--budget", type=int, default=300, help="не больше стольких запросов за запуск")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE_S, help="пауза между запросами, с")
    args = parser.parse_args()

    jobs = json.loads(POINTS_FILE.read_text(encoding="utf-8"))
    plan = []
    for name in ORDER:
        points = jobs[name]["points"]
        pairs = request_pairs([tuple(p) for p in points])
        final = TRANSIT_DIR / f"{name}.json"
        _, done = load_partial(name, points)
        left = 0 if final.exists() else len(pairs) - len(done)
        plan.append((name, points, pairs, final, left))
        state = "готова" if final.exists() else f"осталось {left} из {len(pairs)}"
        print(f"{name}: {len(points)} точек, запросов {len(pairs)} — {state}")
    total = sum(item[4] for item in plan)
    print(
        f"Всего осталось запросов: {total}, это ≈ {round(total * args.pause / 60)} мин. Бюджет запуска: {args.budget}."
    )
    if args.dry_run or total == 0:
        return 0

    key = read_key()
    if not key:
        print(f"Нет ключа: задайте {KEY_ENV} в окружении или в .env корня репозитория.", file=sys.stderr)
        return 1
    client = TransitClient(key, pause_s=args.pause)
    spent = 0
    for name, points, pairs, final, left in plan:
        if left == 0:
            continue
        minutes, done = load_partial(name, points)
        tuples = [tuple(p) for p in points]
        for index, (sources, targets) in enumerate(pairs):
            if index in done:
                continue
            if spent >= args.budget:
                print(f"Бюджет {args.budget} запросов исчерпан, прогресс сохранён. Запустите ещё раз позже.")
                return 0
            if spent > 0:
                time.sleep(args.pause)
            try:
                client._fill(minutes, tuples, sources, targets, DEPARTURE)
            except TransitError as error:
                print(f"\n2ГИС отказал на {name}, запрос {index + 1}/{len(pairs)}: {error}", file=sys.stderr)
                print(
                    "Прогресс сохранён. Если это лимит — запустите позже, продолжит с этого места.",
                    file=sys.stderr,
                )
                return 2
            except Exception as error:  # сеть и прочее: прогресс тоже не теряем
                print(f"\nСбой на {name}, запрос {index + 1}/{len(pairs)}: {error!r}", file=sys.stderr)
                return 3
            spent += 1
            done.add(index)
            save_partial(name, points, minutes, done)
            print(f"\r{name}: {len(done)}/{len(pairs)} (за запуск {spent})", end="", flush=True)
        save_transit_matrix(build_transit_matrix(tuples, minutes, DEPARTURE, region=name), final)
        filled = sum(
            1 for i, row in enumerate(minutes) for j, v in enumerate(row) if i != j and v is not None
        )
        size = len(points)
        print(
            f"\n{name}: готово, минуты 2ГИС у {filled} из {size * (size - 1)} пар → data/transit/{final.name}"
        )
        (PARTIAL_DIR / f"{name}.json").unlink(missing_ok=True)
    print(f"Готово. Потрачено запросов: {spent}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
