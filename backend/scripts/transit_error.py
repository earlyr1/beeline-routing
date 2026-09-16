"""Ошибка встроенной модели общественного транспорта против матриц 2ГИС: по регионам и по расстоянию.

Запуск из каталога backend:
  python -m scripts.transit_error

Сравнивает встроенную модель общественного транспорта с локальными матрицами 2ГИС из data/transit (каталог
переопределяется TRANSIT_MATRIX_DIR) по регионам и расстояниям, считает ошибку в минутах и процентах. Минуты модели
берутся из самого сервиса: TravelTimes и TravelModel без матриц 2ГИС и без OSRM, расстояние по прямой. В сеть скрипт
не ходит. Матрицы локальные и не коммитятся: условия 2ГИС запрещают хранить результаты.

В конце отчёта — привязка точек бандлов из data/bundles к матрицам своих регионов (радиус 150 м): сколько точек
нашлось, самое большое смещение и доля пар дня, которые сервис возьмёт из 2ГИС. На неизменённом бандле привязываются
все точки со смещением 0 м; если нет, матрица посчитана для другой сборки бандла.

Ошибка пары — модель минус 2ГИС: плюс значит, что модель считает дорогу дольше, чем 2ГИС. Пары без маршрута 2ГИС
(в том числе дальше 50 км, их демо-ключ не считает) и пары с нулём минут у 2ГИС (одна и та же точка) не считаются.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.domain.enums import Transport
from app.geo.matrix import TrafficProfile, TravelModel, TravelTimes, build_base_matrix
from app.geo.transit import SNAP_RADIUS_KM, TransitLookup, TransitMatrix, load_transit_matrices
from app.ingest.bundle import load_bundle
from app.settings import Settings

# Полосы расстояния по прямой между точками пары, км: нижняя граница входит, верхняя нет.
DISTANCE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("до 1 км", 0.0, 1.0),
    ("1–2 км", 1.0, 2.0),
    ("2–4 км", 2.0, 4.0),
    ("4–7 км", 4.0, 7.0),
    ("7–12 км", 7.0, 12.0),
    ("12–20 км", 12.0, 20.0),
    ("20+ км", 20.0, math.inf),
)
# Пара «попала», если модель ошиблась не больше чем на столько процентов от минут 2ГИС.
WITHIN_PCT = 25.0
ALL = "все"
NO_MATRICES = (
    "В {directory} нет ни одной матрицы 2ГИС: сравнивать не с чем.\n"
    "Сначала посчитайте матрицы: python -m scripts.transit_matrix --region all "
    "(нужен ключ в TWOGIS_API_KEY, план расхода — флаг --dry-run)."
)


@dataclass(frozen=True)
class PairError:
    """Одна пара точек матрицы: расстояние по прямой, минуты 2ГИС и минуты встроенной модели."""

    region: str
    km: float
    transit_min: float
    model_min: int

    @property
    def minutes(self) -> float:
        return self.model_min - self.transit_min

    @property
    def percent(self) -> float:
        return self.minutes / self.transit_min * 100.0


@dataclass(frozen=True)
class Stats:
    count: int
    median_min: float
    p10_min: float
    p90_min: float
    median_pct: float
    p10_pct: float
    p90_pct: float
    mae_min: float  # средняя ошибка по модулю, минуты
    mape_pct: float  # средняя ошибка по модулю, проценты от минут 2ГИС
    within_share: float  # доля пар с ошибкой не больше WITHIN_PCT, от 0 до 1


def pair_errors(matrix: TransitMatrix, model: TravelModel | None = None) -> tuple[list[PairError], int]:
    """Пары матрицы с минутами 2ГИС и минуты сервиса на них; второе значение — сколько пар без маршрута 2ГИС."""
    model = model or TravelModel()
    points = [(lat, lon) for lat, lon in matrix.points]
    base = build_base_matrix(points, model)
    travel = TravelTimes(base, model, TrafficProfile({}))
    region = matrix.region or "без региона"
    errors: list[PairError] = []
    missing = 0
    for i in range(len(points)):
        for j in range(len(points)):
            if i == j:
                continue
            transit = matrix.minutes_at(i, j)
            if transit is None:
                missing += 1
                continue
            if transit <= 0:
                continue
            model_min = travel.minutes(i, j, Transport.PUBLIC, 0)
            errors.append(PairError(region, base.straight_km[i][j], transit, model_min))
    return errors, missing


def _deciles(values: Sequence[float]) -> tuple[float, float]:
    """10-й и 90-й процентили; у одной пары оба равны её значению."""
    if len(values) == 1:
        return values[0], values[0]
    cuts = statistics.quantiles(values, n=10, method="inclusive")
    return cuts[0], cuts[-1]


def stats(errors: Sequence[PairError]) -> Stats | None:
    if not errors:
        return None
    minutes = [error.minutes for error in errors]
    percents = [error.percent for error in errors]
    p10_min, p90_min = _deciles(minutes)
    p10_pct, p90_pct = _deciles(percents)
    return Stats(
        count=len(errors),
        median_min=statistics.median(minutes),
        p10_min=p10_min,
        p90_min=p90_min,
        median_pct=statistics.median(percents),
        p10_pct=p10_pct,
        p90_pct=p90_pct,
        mae_min=statistics.fmean(abs(value) for value in minutes),
        mape_pct=statistics.fmean(abs(value) for value in percents),
        within_share=sum(abs(value) <= WITHIN_PCT for value in percents) / len(percents),
    )


def band_of(km: float) -> str:
    return next(name for name, low, high in DISTANCE_BANDS if low <= km < high)


HEADER = (
    "группа",
    "пар",
    "медиана, мин",
    "p10…p90, мин",
    "медиана, %",
    "p10…p90, %",
    "MAE, мин",
    "MAPE, %",
    f"в ±{WITHIN_PCT:.0f}%",
)


def _row(name: str, item: Stats | None) -> tuple[str, ...]:
    if item is None:
        return (name, "0", *["—"] * (len(HEADER) - 2))
    return (
        name,
        str(item.count),
        f"{item.median_min:+.1f}",
        f"{item.p10_min:+.0f}…{item.p90_min:+.0f}",
        f"{item.median_pct:+.0f}",
        f"{item.p10_pct:+.0f}…{item.p90_pct:+.0f}",
        f"{item.mae_min:.1f}",
        f"{item.mape_pct:.1f}",
        f"{item.within_share * 100:.0f}%",
    )


def table(rows: Sequence[tuple[str, ...]]) -> list[str]:
    """Строки таблицы: первая колонка по левому краю, остальные по правому."""
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return [
        "  ".join(
            cell.ljust(width) if column == 0 else cell.rjust(width)
            for column, (cell, width) in enumerate(zip(row, widths, strict=True))
        )
        for row in rows
    ]


def _median_cell(errors: Sequence[PairError]) -> str:
    if not errors:
        return "—"
    return f"{statistics.median(error.minutes for error in errors):+.0f}"


def report_lines(matrices: Sequence[TransitMatrix], model: TravelModel | None = None) -> list[str]:
    """Отчёт: как считает модель, сколько пар, таблицы по регионам, по расстоянию и медианы региона × расстояния."""
    model = model or TravelModel()
    errors: list[PairError] = []
    missing = 0
    for matrix in matrices:
        found, without = pair_errors(matrix, model)
        errors += found
        missing += without
    regions = sorted({error.region for error in errors})
    bands = [name for name, _, _ in DISTANCE_BANDS]
    by_cell: dict[tuple[str, str], list[PairError]] = {}
    for error in errors:
        by_cell.setdefault((error.region, band_of(error.km)), []).append(error)

    def of_region(region: str) -> list[PairError]:
        return [error for band in bands for error in by_cell.get((region, band), [])]

    def of_band(band: str) -> list[PairError]:
        return [error for region in regions for error in by_cell.get((region, band), [])]

    return [
        f"Встроенная модель: быстрее из «пешком» (d ×{model.detour_factor:g} при {model.walk_speed_kmh:g} км/ч) "
        f"и поездки {model.public_ride_base_min:g} + {model.public_ride_min_per_km:g}·d минут, d — км по прямой.",
        f"Матриц: {len(matrices)}, пар с минутами 2ГИС: {len(errors)}, без маршрута 2ГИС: {missing}.",
        "Ошибка — модель минус 2ГИС: плюс значит, что модель считает дорогу дольше.",
        "",
        "По регионам:",
        *table(
            [
                HEADER,
                *(_row(region, stats(of_region(region))) for region in regions),
                _row(ALL, stats(errors)),
            ]
        ),
        "",
        "По расстоянию по прямой, все регионы:",
        *table([HEADER, *(_row(band, stats(of_band(band))) for band in bands)]),
        "",
        "Медиана ошибки в минутах по регионам и расстоянию:",
        *table(
            [
                ("регион", *bands),
                *(
                    (region, *(_median_cell(by_cell.get((region, band), [])) for band in bands))
                    for region in regions
                ),
            ]
        ),
    ]


def coverage_lines(settings: Settings, matrices: Sequence[TransitMatrix]) -> list[str]:
    """Привязка точек каждого бандла к матрицам его региона: точки, смещение и доля пар из 2ГИС."""
    rows: list[tuple[str, ...]] = [("регион", "точек", "привязано", "смещение, м", "пар из 2ГИС")]
    for path in sorted(settings.bundles_dir.glob("*/bundle.json")):
        region = path.parent.name
        own = [matrix for matrix in matrices if matrix.region == region]
        if not own:
            continue
        bundle = load_bundle(path)
        located = [r for r in bundle.requests if r.lat is not None and r.lon is not None]
        points = [(e.start_lat, e.start_lon) for e in bundle.engineers] + [(r.lat, r.lon) for r in located]
        lookup = TransitLookup(points, own)
        pairs = len(points) * (len(points) - 1)
        share = 100.0 * lookup.covered / pairs if pairs else 0.0
        rows.append(
            (
                region,
                str(len(points)),
                str(lookup.snapped),
                f"{lookup.max_offset_km * 1000:.0f}",
                f"{lookup.covered} ({share:.0f}%)",
            )
        )
    if len(rows) == 1:
        return []
    return [
        "",
        f"Привязка точек бандлов к матрицам своего региона, радиус {SNAP_RADIUS_KM * 1000:.0f} м:",
        *table(rows),
    ]


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    argparse.ArgumentParser(
        description="Сравнивает встроенную модель общественного транспорта с локальными матрицами 2ГИС"
    ).parse_args(argv)
    settings = Settings.from_env(env)
    matrices = load_transit_matrices(settings.transit_dir)
    if not matrices:
        print(NO_MATRICES.format(directory=settings.transit_dir), file=sys.stderr)
        return 2
    for line in [*report_lines(matrices), *coverage_lines(settings, matrices)]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
