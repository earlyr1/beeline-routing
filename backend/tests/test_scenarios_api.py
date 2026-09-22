"""Подготовленные регионы: список для кнопок экрана загрузки и день региона без выбора файла."""

from app.api.registry import MAX_DATASETS, DatasetRegistry
from app.ingest.bundle import save_bundle
from tests.api_helpers import csv_bytes, memory_only, prepared_bundle, sample_bundle, upload


@memory_only
def test_scenarios_list_titles_counts_and_the_generated_region(api, tmp_path):
    save_bundle(
        prepared_bundle("north_west", "Северо-Запад"), tmp_path / "bundles" / "north_west" / "bundle.json"
    )
    client, _ = api(bundle=prepared_bundle("east", "Восток"))

    scenarios = client.get("/api/scenarios").json()

    # Порядок конфига: настоящий регион Билайна раньше сгенерированного нами.
    assert scenarios == [
        {"region": "east", "title": "Восток", "requests": 3, "engineers": 2, "generated": False},
        {"region": "north_west", "title": "Северо-Запад", "requests": 3, "engineers": 2, "generated": True},
    ]


@memory_only
def test_bundle_of_an_unknown_region_is_not_a_scenario(api):
    # Бандл региона «t» лежит в каталоге, но в synth_config.yaml его нет: откуда его данные, сервис не знает.
    client, _ = api(bundle=sample_bundle())

    assert client.get("/api/scenarios").json() == []
    assert client.post("/api/scenarios/t").status_code == 404


def test_scenario_starts_the_same_day_as_an_upload(api):
    client, _ = api(bundle=prepared_bundle("east", "Восток"))

    response = client.post("/api/scenarios/east")

    assert response.status_code == 202
    dataset_id = response.json()["dataset_id"]
    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "ready" and status["stage"] == "ready"
    report = status["report"]
    assert report["source"] == "scenario" and report["region"] == "east"
    assert report["region_title"] == "Восток" and report["requests"] == 3 and report["engineers"] == 2
    assert report["skipped_rows"] == [] and report["not_found"] == []
    # Настоящий регион Билайна: пометки «сгенерирован нами» нигде нет.
    assert report["generated"] is False

    state = client.post(f"/api/datasets/{dataset_id}/plan").json()
    assert state["version"] == 1 and state["region"] == "east" and state["now"] == "00:00"
    assert state["generated"] is False
    assert [visit["request_id"] for route in state["plan"]["routes"] for visit in route["visits"]]


def test_generated_region_is_marked_in_the_report_and_in_the_plan(api):
    """Честность не заканчивается на кнопке: день сгенерированного региона помечен и в отчёте, и в состоянии."""
    client, _ = api(bundle=prepared_bundle("north_west", "Северо-Запад"))

    dataset_id = client.post("/api/scenarios/north_west").json()["dataset_id"]

    assert client.get(f"/api/datasets/{dataset_id}").json()["report"]["generated"] is True
    # По этому полю вкладка «Сравнение» пишет, что «диспетчеры» региона — наша эвристика, а не решения людей.
    assert client.post(f"/api/datasets/{dataset_id}/plan").json()["generated"] is True


def test_uploaded_csv_of_a_generated_region_is_marked_too(api):
    """Тот же регион, загруженный файлом: риск выдать наши данные за билайновские ровно такой же."""
    client, _ = api(bundle=prepared_bundle("north_west", "Северо-Запад"))
    rows = [(r.id, "10:00", "12:00", r.address) for r in sample_bundle().requests]

    dataset_id = upload(client, "north_west_synthetic.csv", csv_bytes(rows))

    report = client.get(f"/api/datasets/{dataset_id}").json()["report"]
    assert report["source"] == "beeline_csv" and report["generated"] is True


def test_a_broken_bundle_hides_only_its_own_region(api, tmp_path):
    """Битый бандл одного региона не должен ронять ни список кнопок, ни день соседнего региона."""
    broken_path = tmp_path / "bundles" / "south_east" / "bundle.json"
    broken_path.parent.mkdir(parents=True)
    broken_path.write_text('{"region": "south_east"}', encoding="utf-8")
    client, _ = api(bundle=prepared_bundle("east", "Восток"))

    assert [scenario["region"] for scenario in client.get("/api/scenarios").json()] == ["east"]
    assert client.post("/api/scenarios/east").status_code == 202

    broken = client.post("/api/scenarios/south_east")
    assert broken.status_code == 404
    assert broken.json()["detail"] == (
        "Регион «Юго-восток» не читается: бандл data/bundles/south_east повреждён."
    )


def test_registry_keeps_only_the_last_datasets():
    """Каждое нажатие кнопки региона — новый набор данных: за репетицию их накопятся десятки."""
    registry = DatasetRegistry()

    # Первые два никто не держит в руках: уходя из реестра, они уходят совсем.
    forgotten = [registry.create().dataset_id for _ in range(2)]
    records = [registry.create() for _ in range(MAX_DATASETS)]

    assert registry.get(forgotten[0]) is None
    assert registry.get(forgotten[1]) is None
    assert registry.get(records[0].dataset_id) is records[0]
    assert registry.get(records[-1].dataset_id) is records[-1]


def test_a_day_someone_still_holds_stays_the_only_copy():
    """День, забытый реестром посреди запроса, не поднимается вторым экземпляром.

    С Postgres забытый день не пропадает: следующее обращение подняло бы его из базы заново, и рядом
    с прежним объектом, который запрос ещё держит, оказался бы второй — со своей нумерацией событий и
    своей ревизией шкалы. Тогда следующая правка диспетчера упёрлась бы в «tl_1 уже есть».
    """
    registry = DatasetRegistry()
    held = registry.create()

    for _ in range(MAX_DATASETS + 2):
        registry.create()

    assert registry.get(held.dataset_id) is held


@memory_only
def test_unknown_region_and_region_without_a_bundle_are_russian_404(api):
    client, _ = api(bundle=prepared_bundle("east", "Восток"))

    unknown = client.post("/api/scenarios/mars")
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "Регион mars не найден: такого подготовленного региона нет."

    # Регион конфига, бандл которого не собран: кнопки у него на экране нет, но запрос должен объяснить почему.
    missing = client.post("/api/scenarios/south_east")
    assert missing.status_code == 404
    assert missing.json()["detail"] == (
        "Регион «Юго-восток» не подготовлен: нет бандла в data/bundles/south_east."
    )
