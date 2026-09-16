"""scripts/transit_matrix.py: план расхода, расчёт всех регионов за один запуск, докат и отказы. Без сети."""

from app.domain.models import Bundle
from app.geo.transit import TransitError, load_transit_matrix, transit_matrix_path
from app.ingest.bundle import save_bundle
from scripts import transit_matrix as cli
from tests.helpers import req
from tests.planning_helpers import OFFICE, day_engineers, day_requests

KEY = {"TWOGIS_API_KEY": "ключ-из-кабинета"}


class NoNetwork:
    """Заглушка вместо клиента 2ГИС: любой вызов сети в тесте — ошибка."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("клиент 2ГИС не должен создаваться")


def save_region(tmp_path, region: str = "t", requests=None) -> Bundle:
    bundle = Bundle(
        region=region,
        office=OFFICE,
        requests=day_requests() if requests is None else requests,
        engineers=day_engineers(),
    )
    save_bundle(bundle, tmp_path / "bundles" / region / "bundle.json")
    return bundle


def save_three_regions(tmp_path) -> None:
    """Три региона разного размера: 3, 5 и 26 точек (2 инженера плюс заявки)."""
    save_region(tmp_path, "средний")
    save_region(tmp_path, "малый", requests=day_requests()[:1])
    save_region(
        tmp_path, "большой", requests=[req(f"R{k}", 1 + k / 10, 0, "10:00", "18:00") for k in range(24)]
    )


def stub_client(seen: dict, fail_sizes: tuple[int, ...] = ()):
    """Клиент 2ГИС без сети: матрица из семёрок, регион с таким числом точек падает TransitError."""

    class StubClient:
        def __init__(self, key, pause_s=0.0, **kwargs):
            seen["key"] = key
            seen["pause_s"] = pause_s
            seen.setdefault("clients", 0)
            seen["clients"] += 1

        def matrix(self, points, departure, progress=None):
            points = list(points)
            seen.setdefault("calls", []).append(len(points))
            seen["departure"] = departure
            if len(points) in fail_sizes:
                raise TransitError("2ГИС ответил 429: Превышен лимит запросов.")
            if progress is not None:
                progress(1, 1)
            size = len(points)
            return [
                [0 if i == j else (None if (i, j) == (0, 1) else 7) for j in range(size)] for i in range(size)
            ]

    return StubClient


def matrix_path(tmp_path, region: str):
    return transit_matrix_path(tmp_path / "transit", region)


def plan_regions(out: str) -> list[str]:
    """Регионы в том порядке, в каком их напечатал план."""
    return [line.split()[1].rstrip(":") for line in out.splitlines() if line.startswith("регион ")]


def test_dry_run_prints_the_cost_and_does_not_touch_the_network(tmp_path, monkeypatch, capsys):
    save_region(tmp_path)
    monkeypatch.setattr(cli, "TransitClient", NoNetwork)

    code = cli.main(["--region", "t", "--departure", "13:00", "--dry-run"], env={"DATA_DIR": str(tmp_path)})

    out = capsys.readouterr().out
    assert code == 0
    # 2 инженера и 3 заявки: 5 точек, один блок, 25 элементов матрицы.
    assert "точек: 5" in out and "запросов: 1" in out and "элементов: 25" in out
    assert "1000" in out
    assert plan_regions(out) == ["t"]
    assert not matrix_path(tmp_path, "t").exists()


def test_run_without_the_key_refuses_in_russian(tmp_path, monkeypatch, capsys):
    save_region(tmp_path)
    monkeypatch.setattr(cli, "TransitClient", NoNetwork)

    code = cli.main(["--region", "t"], env={"DATA_DIR": str(tmp_path)})

    err = capsys.readouterr().err
    assert code != 0
    assert "TWOGIS_API_KEY" in err and "демо" in err.lower()
    assert not matrix_path(tmp_path, "t").exists()


def test_unknown_region_is_reported_in_russian(tmp_path, monkeypatch, capsys):
    save_region(tmp_path)
    monkeypatch.setattr(cli, "TransitClient", NoNetwork)

    code = cli.main(["--region", "нет-такого", "--dry-run"], env={"DATA_DIR": str(tmp_path)})

    assert code != 0 and "регион" in capsys.readouterr().err.lower()


def test_run_writes_the_matrix_of_the_region_and_compares_it_with_the_built_in_model(
    tmp_path, monkeypatch, capsys
):
    save_region(tmp_path)
    seen: dict = {}
    monkeypatch.setattr(cli, "TransitClient", stub_client(seen))

    code = cli.main(
        ["--region", "t", "--departure", "13:00", "--pause", "0.3"], env={"DATA_DIR": str(tmp_path), **KEY}
    )

    out = capsys.readouterr().out
    assert code == 0
    assert seen["key"] == "ключ-из-кабинета" and seen["departure"] == "13:00" and seen["pause_s"] == 0.3
    assert seen["calls"] == [5]
    saved = load_transit_matrix(matrix_path(tmp_path, "t"))
    assert saved.departure == "13:00" and saved.region == "t"
    assert saved.minutes[0][1] is None and saved.minutes[0][2] == 7
    assert "блок 1/1" in out
    assert "пар: 20" in out and "без маршрута: 1" in out
    assert "встроенная модель" in out
    assert "посчитано регионов: 1" in out
    assert "коммит" in out and ".gitignore" in out
    assert "ключ-из-кабинета" not in out


def test_bad_departure_is_reported_in_russian(tmp_path, monkeypatch, capsys):
    save_region(tmp_path)
    monkeypatch.setattr(cli, "TransitClient", NoNetwork)

    code = cli.main(["--region", "t", "--departure", "полдень", "--dry-run"], env={"DATA_DIR": str(tmp_path)})

    assert code != 0 and "HH:MM" in capsys.readouterr().err


def test_plan_for_all_regions_is_ordered_by_size_and_counts_the_total(tmp_path, monkeypatch, capsys):
    save_three_regions(tmp_path)
    monkeypatch.setattr(cli, "TransitClient", NoNetwork)

    code = cli.main(["--region", "all", "--dry-run"], env={"DATA_DIR": str(tmp_path), **KEY})

    out = capsys.readouterr().out
    assert code == 0
    # Дешёвый регион первым: если ключ упрётся в лимит, потеряно будет меньше.
    assert plan_regions(out) == ["малый", "средний", "большой"]
    assert "точек: 3" in out and "точек: 5" in out and "точек: 26" in out
    # 3 и 5 точек — один блок и один запрос, 26 точек — четыре блока 25×25.
    total = next(line for line in out.splitlines() if line.startswith("итого"))
    assert "запросов: 6" in total and "1000" in total and "0.6" in total
    assert "минут" in total
    assert not (tmp_path / "transit").exists()


def test_all_regions_are_computed_in_one_run(tmp_path, monkeypatch, capsys):
    save_three_regions(tmp_path)
    seen: dict = {}
    monkeypatch.setattr(cli, "TransitClient", stub_client(seen))

    code = cli.main(["--region", "all"], env={"DATA_DIR": str(tmp_path), **KEY})

    out = capsys.readouterr().out
    assert code == 0
    assert seen["calls"] == [3, 5, 26]
    for region, size in (("малый", 3), ("средний", 5), ("большой", 26)):
        saved = load_transit_matrix(matrix_path(tmp_path, region))
        assert saved.region == region and len(saved.points) == size
    assert "посчитано регионов: 3" in out and "пропущено: 0" in out and "с ошибкой: 0" in out
    assert "запросов" in out and "коммит" in out


def test_a_region_that_is_already_computed_is_skipped_unless_force(tmp_path, monkeypatch, capsys):
    save_three_regions(tmp_path)
    seen: dict = {}
    monkeypatch.setattr(cli, "TransitClient", stub_client(seen))
    cli.main(["--region", "средний"], env={"DATA_DIR": str(tmp_path), **KEY})
    capsys.readouterr()

    code = cli.main(["--region", "all"], env={"DATA_DIR": str(tmp_path), **KEY})

    out = capsys.readouterr().out
    assert code == 0
    assert seen["calls"] == [5, 3, 26]
    assert "уже посчитан" in out and "пропущено: 1" in out and "посчитано регионов: 2" in out

    seen["calls"].clear()
    code = cli.main(["--region", "all", "--force"], env={"DATA_DIR": str(tmp_path), **KEY})
    out = capsys.readouterr().out
    assert code == 0 and seen["calls"] == [3, 5, 26]
    assert "уже посчитан" not in out


def test_a_changed_bundle_makes_the_saved_matrix_stale(tmp_path, monkeypatch, capsys):
    save_region(tmp_path, "средний")
    seen: dict = {}
    monkeypatch.setattr(cli, "TransitClient", stub_client(seen))
    cli.main(["--region", "средний"], env={"DATA_DIR": str(tmp_path), **KEY})
    save_region(tmp_path, "средний", requests=day_requests()[:1])
    capsys.readouterr()

    code = cli.main(["--region", "all"], env={"DATA_DIR": str(tmp_path), **KEY})

    assert code == 0 and seen["calls"] == [5, 3]
    assert "уже посчитан" not in capsys.readouterr().out


def test_a_failing_region_stops_the_run_and_keep_going_continues(tmp_path, monkeypatch, capsys):
    save_three_regions(tmp_path)
    seen: dict = {}
    monkeypatch.setattr(cli, "TransitClient", stub_client(seen, fail_sizes=(5,)))

    code = cli.main(["--region", "all"], env={"DATA_DIR": str(tmp_path), **KEY})

    captured = capsys.readouterr()
    assert code == 1
    # Дошли до среднего региона и встали: большой даже не пробовали.
    assert seen["calls"] == [3, 5]
    assert "средний" in captured.err and "429" in captured.err
    assert "с ошибкой: 1" in captured.out
    # Посчитанное осталось на диске: следующий запуск продолжит с него.
    assert matrix_path(tmp_path, "малый").exists()
    assert not matrix_path(tmp_path, "большой").exists()

    seen["calls"].clear()
    code = cli.main(["--region", "all", "--keep-going"], env={"DATA_DIR": str(tmp_path), **KEY})

    out = capsys.readouterr().out
    assert code == 1
    assert seen["calls"] == [5, 26]
    assert "уже посчитан" in out and "с ошибкой: 1" in out and "посчитано регионов: 1" in out
    assert matrix_path(tmp_path, "большой").exists()


def test_all_without_a_single_bundle_is_reported_in_russian(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "TransitClient", NoNetwork)

    code = cli.main(["--region", "all", "--dry-run"], env={"DATA_DIR": str(tmp_path)})

    assert code != 0 and "бандл" in capsys.readouterr().err.lower()
