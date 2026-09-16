"""scripts/transit_matrix.py: пробный расчёт, отказ без ключа и запись файла с заглушкой клиента. Без сети."""

from app.domain.models import Bundle
from app.geo.transit import load_transit_matrix
from app.ingest.bundle import save_bundle
from scripts import transit_matrix as cli
from tests.planning_helpers import OFFICE, day_engineers, day_requests


class NoNetwork:
    """Заглушка вместо клиента 2ГИС: любой вызов сети в тесте — ошибка."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("клиент 2ГИС не должен создаваться")


def save_region(tmp_path, region: str = "t") -> Bundle:
    bundle = Bundle(region=region, office=OFFICE, requests=day_requests(), engineers=day_engineers())
    save_bundle(bundle, tmp_path / "bundles" / region / "bundle.json")
    return bundle


def test_dry_run_prints_the_cost_and_does_not_touch_the_network(tmp_path, monkeypatch, capsys):
    save_region(tmp_path)
    monkeypatch.setattr(cli, "TransitClient", NoNetwork)

    code = cli.main(["--region", "t", "--departure", "13:00", "--dry-run"], env={"DATA_DIR": str(tmp_path)})

    out = capsys.readouterr().out
    assert code == 0
    # 2 инженера и 3 заявки: 5 точек, один блок, 25 элементов матрицы.
    assert "точек: 5" in out and "запросов: 1" in out and "элементов: 25" in out
    assert "1000" in out
    assert not (tmp_path / "transit_matrix.json").exists()


def test_run_without_the_key_refuses_in_russian(tmp_path, monkeypatch, capsys):
    save_region(tmp_path)
    monkeypatch.setattr(cli, "TransitClient", NoNetwork)

    code = cli.main(["--region", "t"], env={"DATA_DIR": str(tmp_path)})

    err = capsys.readouterr().err
    assert code != 0
    assert "TWOGIS_API_KEY" in err and "демо" in err.lower()
    assert not (tmp_path / "transit_matrix.json").exists()


def test_unknown_region_is_reported_in_russian(tmp_path, monkeypatch, capsys):
    save_region(tmp_path)
    monkeypatch.setattr(cli, "TransitClient", NoNetwork)

    code = cli.main(["--region", "нет-такого", "--dry-run"], env={"DATA_DIR": str(tmp_path)})

    assert code != 0 and "регион" in capsys.readouterr().err.lower()


def test_run_writes_the_matrix_and_compares_it_with_the_built_in_model(tmp_path, monkeypatch, capsys):
    save_region(tmp_path)
    seen = {}

    class StubClient:
        def __init__(self, key, pause_s=0.0, **kwargs):
            seen["key"] = key
            seen["pause_s"] = pause_s

        def matrix(self, points, departure, progress=None):
            seen["points"] = list(points)
            seen["departure"] = departure
            if progress is not None:
                progress(1, 1)
            size = len(points)
            return [
                [0 if i == j else (None if (i, j) == (0, 1) else 7) for j in range(size)] for i in range(size)
            ]

    monkeypatch.setattr(cli, "TransitClient", StubClient)
    env = {"DATA_DIR": str(tmp_path), "TWOGIS_API_KEY": "ключ-из-кабинета"}

    code = cli.main(["--region", "t", "--departure", "13:00", "--pause", "0.3"], env=env)

    out = capsys.readouterr().out
    assert code == 0
    assert seen["key"] == "ключ-из-кабинета" and seen["departure"] == "13:00" and seen["pause_s"] == 0.3
    assert len(seen["points"]) == 5
    saved = load_transit_matrix(tmp_path / "transit_matrix.json")
    assert saved.departure == "13:00" and saved.minutes[0][1] is None and saved.minutes[0][2] == 7
    assert saved.matches(seen["points"])
    assert "блок 1/1" in out
    assert "пар: 20" in out and "без маршрута: 1" in out
    assert "встроенная модель" in out
    assert "коммит" in out and ".gitignore" in out
    assert "ключ-из-кабинета" not in out


def test_bad_departure_is_reported_in_russian(tmp_path, monkeypatch, capsys):
    save_region(tmp_path)
    monkeypatch.setattr(cli, "TransitClient", NoNetwork)

    code = cli.main(["--region", "t", "--departure", "полдень", "--dry-run"], env={"DATA_DIR": str(tmp_path)})

    assert code != 0 and "HH:MM" in capsys.readouterr().err
