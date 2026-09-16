"""scripts/transit_error.py: ошибка встроенной модели общественного транспорта против матриц 2ГИС. Без сети."""

import pytest

from app.domain.models import Bundle
from app.geo.transit import build_transit_matrix, save_transit_matrix, transit_matrix_path
from app.ingest.bundle import save_bundle
from scripts import transit_error as cli
from tests.helpers import at
from tests.planning_helpers import OFFICE, day_engineers, day_requests

# 5 км по прямой: модель сервиса даёт поездку 22.5 + 2.8·5 = 36.5, то есть 37 минут. Третья точка в 60 км:
# 2ГИС её пар не считал.
POINTS = [at(0, 0), at(5, 0), at(60, 0)]
MINUTES = [[0, 30, None], [40, 0, None], [None, None, 0]]


def save(tmp_path, region, points=POINTS, minutes=MINUTES):
    save_transit_matrix(
        build_transit_matrix(points, minutes, "13:00", region=region),
        transit_matrix_path(tmp_path / "transit", region),
    )


def test_pair_errors_use_the_service_model_and_skip_pairs_without_2gis():
    errors, missing = cli.pair_errors(build_transit_matrix(POINTS, MINUTES, "13:00", region="east"))
    assert missing == 4
    assert [(error.transit_min, error.model_min) for error in errors] == [(30, 37), (40, 37)]
    assert [round(error.percent, 1) for error in errors] == [23.3, -7.5]

    summary = cli.stats(errors)
    assert (summary.count, summary.median_min, summary.mae_min) == (2, 2.0, 5.0)
    assert summary.mape_pct == pytest.approx(15.42, abs=0.01)
    assert summary.within_share == 1.0
    assert cli.stats([]) is None
    assert [cli.band_of(km) for km in (0.0, 0.99, 1.0, 5.0, 19.9, 20.0, 80.0)] == [
        "до 1 км",
        "до 1 км",
        "1–2 км",
        "4–7 км",
        "12–20 км",
        "20+ км",
        "20+ км",
    ]


def test_report_by_regions_and_distance(tmp_path, capsys):
    save(tmp_path, "east")
    save(tmp_path, "north_west", points=POINTS[:2], minutes=[[0, 0], [20, 0]])

    code = cli.main([], env={"DATA_DIR": str(tmp_path)})

    out = capsys.readouterr().out
    assert code == 0
    assert "Матриц: 2, пар с минутами 2ГИС: 3, без маршрута 2ГИС: 4." in out
    lines = out.splitlines()
    assert next(line for line in lines if line.startswith("east")).split()[:3] == ["east", "2", "+2.0"]
    # Ноль минут у 2ГИС (одна и та же точка) в ошибку не идёт: у north_west одна пара, 37 против 20.
    assert next(line for line in lines if line.startswith("north_west")).split()[:3] == [
        "north_west",
        "1",
        "+17.0",
    ]
    assert next(line for line in lines if line.startswith("все")).split()[:2] == ["все", "3"]
    assert next(line for line in lines if line.startswith("4–7 км")).split()[:3] == ["4–7", "км", "3"]
    assert "до 1 км" in out and "20+ км" in out


def test_without_matrices_the_script_explains_in_russian(tmp_path, capsys):
    code = cli.main([], env={"DATA_DIR": str(tmp_path)})

    captured = capsys.readouterr()
    assert code != 0 and captured.out == ""
    assert "нет ни одной матрицы 2ГИС" in captured.err and "scripts.transit_matrix" in captured.err


def test_coverage_shows_how_bundle_points_snap_to_the_matrix_of_their_region(tmp_path, capsys):
    requests, engineers = day_requests(), day_engineers()
    bundle = Bundle(region="east", office=OFFICE, requests=requests, engineers=engineers)
    save_bundle(bundle, tmp_path / "bundles" / "east" / "bundle.json")
    points = [(e.start_lat, e.start_lon) for e in engineers] + [(r.lat, r.lon) for r in requests]
    # Последняя заявка в матрице сдвинута на 50 м: привязка находит её, смещение 50 м.
    lat, lon = points[-1]
    shifted = [*points[:-1], (lat + 0.05 / 111.2, lon)]
    size = len(points)
    minutes = [[0 if i == j else 20 for j in range(size)] for i in range(size)]
    save(tmp_path, "east", points=shifted, minutes=minutes)

    code = cli.main([], env={"DATA_DIR": str(tmp_path)})

    out = capsys.readouterr().out
    assert code == 0 and "радиус 150 м" in out
    coverage = out.split("Привязка точек бандлов")[1].splitlines()
    row = next(line for line in coverage if line.startswith("east")).split()
    # Инженеры стартуют из одной точки офиса: пара между ними — одна точка матрицы, её считает формула.
    assert row[:4] == ["east", str(size), str(size), "50"]
