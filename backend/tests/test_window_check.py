"""Окна из данных: что уносит строку, что остаётся замечанием и почему настоящие файлы проверка не трогает.

Правило одно: показываем, но не подменяем. Окно выгрузки — источник правды, поэтому проверка либо уносит
строку, в окне которой работать нельзя вообще, либо оставляет заявку в дне вместе с замечанием диспетчеру.
"""

import json
from pathlib import Path

import pytest

from app.ingest.beeline_csv import parse_beeline_csv
from app.ingest.window_check import check_windows
from app.settings import BACKEND_DIR, REPO_ROOT
from app.synth.config import SynthConfig
from tests.api_helpers import make_client, sample_bundle, upload
from tests.planning_helpers import OFFICE

CONFIG = Path(BACKEND_DIR) / "config" / "synth_config.yaml"
RAW_DIR = REPO_ROOT / "data" / "raw"
HEADER = "Заявка;Тип заявки BK;Тип заявки HD;Начало;Окончание;Район;Адрес;Гигабитное подключение\r\n"
ADDRESS = "Город Москва, ул.Таганская, д. 1"

# Сколько заявок читается из настоящих файлов сегодня: проверка окон не имеет права съесть ни одну.
REAL_ROWS = {"east": 66, "north_west": 72, "south_center": 56, "south_east": 83}

# Выгрузка со всеми видами чепухи разом. Номер строки в файле на единицу больше номера здесь: первая — заголовок.
JUNK = [
    ("N1", "Локальная заявка", "10:00", "12:00"),  # строка 2: слот сетки, всё в порядке
    ("N2", "Локальная заявка", "12:00", "10:00"),  # строка 3: конец раньше начала
    ("N3", "Локальная заявка", "14:00", "14:00"),  # строка 4: окно нулевой длины
    ("N4", "Локальная заявка", "02:00", "04:00"),  # строка 5: ночь, рабочий день 10:00–22:00
    ("N5", "Подключение", "10:00", "10:10"),  # строка 6: 10 минут на работы по нормативу 70
    ("N6", "Локальная заявка", "11:30", "13:30"),  # строка 7: окно не по сетке
    ("N7", "Глобальная проблема", "0:01", "23:59"),  # строка 8: «весь день» аварии — это не ошибка
]

EMPTY_WINDOWS = [
    "строка 3: у заявки N2 окно 12:00–10:00 кончается не позже начала, строка пропущена",
    "строка 4: у заявки N3 окно 14:00–14:00 кончается не позже начала, строка пропущена",
]
FINDINGS = [
    "строка 5: у заявки N4 окно 02:00–04:00 вне рабочего дня 10:00–22:00 — приехать в него некому",
    "строка 6: у заявки N5 окно приезда 10:00–10:10 — всего 10 мин, бригада должна попасть ровно в них, "
    "а работ по типу «Подключение» на 70 мин",
    "окна не по сетке: 3 из 5 (в том числе названные выше), например строка 7 — заявка N6, 11:30–13:30. "
    "Слоты сетки: 10:00–12:00, 12:00–14:00, 14:00–16:00, 16:00–18:00, 18:00–20:00, 20:00–22:00",
]


@pytest.fixture(scope="module")
def cfg():
    return SynthConfig.load(CONFIG)


def csv_of(rows, office=OFFICE.address):
    """Выгрузка Билайна из строк (номер заявки, тип BK, начало окна, конец окна)."""
    lines = [HEADER]
    for request_id, type_bk, start, end in rows:
        lines.append(
            f"{request_id};{type_bk};Нет линка;17.08.2026 {start};17.08.2026 {end};Таганский;{ADDRESS};Нет\r\n"
        )
    if office:
        lines.append(f"Адрес Офиса;{office};;;;;;\r\n")
    return "".join(lines).encode("cp1251")


def test_an_empty_window_drops_the_row_and_the_rest_of_the_nonsense_is_only_reported(cfg):
    """Строка, в окне которой работать нельзя, выпадает; остальные окна остаются как есть, со словами о них."""
    checked = check_windows(cfg, parse_beeline_csv(csv_of(JUNK)))

    assert [row.request_id for row in checked.rows] == ["N1", "N4", "N5", "N6", "N7"]
    # Номера строк, оставшихся в дне, идут подряд: заявки дальше живут по ним.
    assert [row.row_index for row in checked.rows] == [0, 1, 2, 3, 4]
    assert checked.skipped == EMPTY_WINDOWS
    assert checked.window_warnings == FINDINGS
    # Ни одно окно не поправлено: 11:30–13:30 осталось собой, «весь день» аварии — тоже.
    windows = {row.request_id: (row.window_start, row.window_end) for row in checked.rows}
    assert windows["N6"] == (690, 810) and windows["N7"] == (1, 1439)


def test_a_clean_file_says_nothing_at_all(cfg):
    """Чистый день выглядит ровно как раньше: ни пропущенных строк, ни замечаний."""
    clean = [("N1", "Локальная заявка", "10:00", "12:00"), ("N2", "Подключение", "14:00", "16:00")]

    checked = check_windows(cfg, parse_beeline_csv(csv_of(clean)))

    assert (checked.skipped, checked.window_warnings) == ([], [])
    assert len(checked.rows) == 2


def test_a_window_exactly_as_long_as_the_norm_is_not_short_enough_to_report(cfg):
    """Норматив — не меньше, а «короче»: окно ровно в норматив претензии не вызывает, а на минуту меньше — да.

    Замечание говорит про окно ПРИЕЗДА: работы по нашей же модели спокойно кончаются позже окна
    (app/solvers/simulate.py считает опоздание по началу визита), и правила «окно не короче работ» нет.
    """
    exact = check_windows(cfg, parse_beeline_csv(csv_of([("N1", "Подключение", "10:00", "11:10")])))
    assert [w for w in exact.window_warnings if "окно приезда" in w] == []

    short = check_windows(cfg, parse_beeline_csv(csv_of([("N1", "Подключение", "10:00", "11:09")])))
    assert short.window_warnings[0] == (
        "строка 2: у заявки N1 окно приезда 10:00–11:09 — всего 69 мин, бригада должна попасть ровно "
        "в них, а работ по типу «Подключение» на 70 мин"
    )


def test_a_window_past_the_planning_horizon_drops_the_row_instead_of_dropping_the_day(cfg):
    """Окно дальше двух суток роняло весь расчёт: теперь выпадает одна строка, а день остаётся.

    Время в модели OR-Tools меряется в пределах двух суток, и заявка с окном дальше не просто бесполезна —
    из-за неё диспетчер терял весь файл. Работать в таком окне нельзя вообще, значит строке место в пропущенных.
    """
    rows = [("N1", "Локальная заявка", "55:00", "55:30"), ("N2", "Локальная заявка", "10:00", "12:00")]

    checked = check_windows(cfg, parse_beeline_csv(csv_of(rows)))

    assert [row.request_id for row in checked.rows] == ["N2"]
    assert checked.skipped == [
        "строка 2: у заявки N1 окно 55:00–55:30 дальше горизонта планирования (до 48:00), строка пропущена"
    ]
    assert checked.window_warnings == []


def test_the_whole_day_passes_silently_only_as_the_emergency_mark_of_the_data(cfg):
    """«Весь день» — это ровно 00:01–23:59 у аварии: любое другое окно на всю смену клиенту не назовёшь."""
    emergency = check_windows(
        cfg, parse_beeline_csv(csv_of([("N1", "Глобальная проблема", "0:01", "23:59")]))
    )
    assert emergency.window_warnings == []

    for type_bk, start, end in (
        ("Локальная заявка", "0:01", "23:59"),  # то же окно, но заявка не аварийная
        ("Глобальная проблема", "10:00", "22:00"),  # окно ровно в смену — не пометка данных
        ("Глобальная проблема", "9:00", "23:00"),  # смена целиком внутри окна
    ):
        checked = check_windows(cfg, parse_beeline_csv(csv_of([("N1", type_bk, start, end)])))
        assert [row.request_id for row in checked.rows] == ["N1"], (type_bk, start, end)
        assert len(checked.window_warnings) == 1, (type_bk, start, end)
        assert checked.window_warnings[0].startswith("окна не по сетке: 1 из 1")


@pytest.mark.parametrize("path", sorted(RAW_DIR.glob("*.csv")), ids=lambda path: path.stem)
def test_the_real_files_lose_no_rows_and_get_no_findings(cfg, path):
    """Проверка стала строже, а настоящие данные не тронуты: ни одной выпавшей строки, ни одного замечания.

    Здесь и четыре контрольных файла, и четыре синтетических: окна у них одни и те же. Если этот тест упал,
    значит проверка съела настоящий день — чинить надо проверку, а не данные.
    """
    raw = parse_beeline_csv(path.read_bytes())
    checked = check_windows(cfg, raw)

    region = path.stem.removesuffix("_control").removesuffix("_synthetic")
    assert len(raw.rows) == REAL_ROWS[region]
    assert [row.request_id for row in checked.rows] == [row.request_id for row in raw.rows]
    assert checked.skipped == raw.skipped == []
    assert checked.window_warnings == []


def test_the_upload_report_shows_the_findings_and_the_day_is_planned_without_the_dropped_rows(tmp_path):
    """Диспетчер видит то же самое в отчёте разбора, а причина неназначения называет те же числа."""
    client, _ = make_client(tmp_path)

    dataset_id = upload(client, "junk.csv", csv_of(JUNK))

    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "ready", status
    report = status["report"]
    assert report["requests"] == 5
    assert report["skipped_rows"] == EMPTY_WINDOWS
    assert report["window_warnings"] == FINDINGS

    state = client.post(f"/api/datasets/{dataset_id}/plan").json()
    assert {request["id"] for request in state["requests"]} == {"N1", "N4", "N5", "N6", "N7"}
    # Окна в дне остались теми же, что в файле: план построен по данным, а не по подправленным окнам.
    windows = {
        request["id"]: (request["window_start"], request["window_end"]) for request in state["requests"]
    }
    assert windows["N4"] == ("02:00", "04:00") and windows["N6"] == ("11:30", "13:30")
    # Ночная заявка — та самая, что раньше тихо оставалась без инженера: теперь о ней сказано ещё в отчёте.
    reason = next(item for item in state["plan"]["unassigned"] if item["request_id"] == "N4")
    assert "02:00–04:00" in reason["reason_text"]


def test_a_repeated_row_is_dropped_before_its_window_is_judged(tmp_path):
    """Про окно строки, которой в дне не будет, диспетчеру не говорят: у заявки такого окна нет.

    Раньше повторы выбрасывались после проверки окон, и диспетчер шёл искать у заявки ночное окно, которого
    в дне нет, а знаменатель «столько-то из скольких» считал выброшенные строки.
    """
    client, _ = make_client(tmp_path)
    rows = [
        ("N1", "Локальная заявка", "10:00", "12:00"),
        ("N1", "Подключение", "02:00", "02:30"),  # повтор номера, да ещё и с ночным окном
        ("N2", "Локальная заявка", "12:00", "14:00"),
    ]

    dataset_id = upload(client, "dup.csv", csv_of(rows))

    report = client.get(f"/api/datasets/{dataset_id}").json()["report"]
    assert report["skipped_rows"] == ["строка 3: номер заявки N1 повторяется, строка пропущена"]
    assert report["window_warnings"] == []
    assert report["requests"] == 2


def test_a_file_eaten_by_the_check_says_why_instead_of_looking_empty(tmp_path):
    """Не осталось ни одной заявки — диспетчер слышит, что дело в окнах: отчёта у неудавшейся загрузки нет."""
    client, _ = make_client(tmp_path)
    rows = [("N1", "Локальная заявка", "12:00", "10:00"), ("N2", "Локальная заявка", "14:00", "14:00")]

    dataset_id = upload(client, "all_bad.csv", csv_of(rows))

    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "failed"
    assert status["error"] == (
        "В файле нет ни одной заявки: пропущены все строки (2). "
        "строка 2: у заявки N1 окно 12:00–10:00 кончается не позже начала, строка пропущена; "
        "строка 3: у заявки N2 окно 14:00–14:00 кончается не позже начала, строка пропущена"
    )


def test_an_edited_window_is_planned_from_the_file_and_not_from_the_prepared_bundle(tmp_path):
    """Правленое в Excel окно номера заявки не меняет — и день всё равно строится по файлу, а не по бандлу.

    Ярлык на подготовленный бандл региона (он же даёт распределение диспетчеров) срабатывает только на той самой
    выгрузке, из которой бандл собран: совпасть должны и номера заявок, и окна. Иначе отчёт показывал бы замечания
    к окнам файла, а план строился бы по другим окнам — ровно та тихая подмена данных, которой мы не делаем.
    """
    client, _ = make_client(tmp_path)
    # Номера — как в бандле, окно первой заявки подправлено: 10:00–12:00 стало 11:30–13:30.
    rows = [
        ("R1", "Локальная заявка", "11:30", "13:30"),
        ("R2", "Локальная заявка", "14:00", "16:00"),
        ("R3", "Локальная заявка", "15:00", "17:00"),
    ]

    dataset_id = upload(client, "edited.csv", csv_of(rows))

    report = client.get(f"/api/datasets/{dataset_id}").json()["report"]
    assert report["requests"] == 3
    state = client.post(f"/api/datasets/{dataset_id}/plan").json()
    windows = {r["id"]: (r["window_start"], r["window_end"]) for r in state["requests"]}
    assert windows["R1"] == ("11:30", "13:30")
    # Заявки взяты из файла целиком, а не из бандла: адрес тоже из выгрузки.
    assert {r["address"] for r in state["requests"]} == {ADDRESS}


def test_the_untouched_export_still_goes_straight_to_the_prepared_bundle(tmp_path):
    """Нетронутый файл остаётся на быстром пути: окна совпали с бандлом, значит подменять нечего."""
    client, _ = make_client(tmp_path)
    rows = [
        ("R1", "Локальная заявка", "10:00", "12:00"),
        ("R2", "Локальная заявка", "14:00", "16:00"),
        ("R3", "Локальная заявка", "15:00", "17:00"),
    ]

    dataset_id = upload(client, "same.csv", csv_of(rows))

    state = client.post(f"/api/datasets/{dataset_id}/plan").json()
    assert {r["address"] for r in state["requests"]} == {"адрес R1", "адрес R2", "адрес R3"}


def test_a_bundle_with_a_zero_window_is_not_loaded_either(tmp_path):
    """Нулевое окно уносит строку выгрузки — и JSON-бандл с ним тоже не загрузится: пути не расходятся."""
    client, _ = make_client(tmp_path)
    bundle = json.loads(sample_bundle().model_dump_json())
    bundle["requests"][0]["window_end"] = bundle["requests"][0]["window_start"]

    dataset_id = upload(client, "bundle.json", json.dumps(bundle, ensure_ascii=False).encode())

    status = client.get(f"/api/datasets/{dataset_id}").json()
    assert status["status"] == "failed"
    assert status["error"] == "JSON не соответствует схеме бандла: временное окно нулевой длины у заявок: R1"


def test_a_clean_upload_keeps_the_report_as_it_was(tmp_path):
    """Чистый файл — чистый отчёт: новый раздел пуст, и диспетчер видит ровно то же, что и раньше."""
    client, _ = make_client(tmp_path)
    rows = [("N1", "Локальная заявка", "10:00", "12:00"), ("N2", "Подключение", "14:00", "16:00")]

    dataset_id = upload(client, "clean.csv", csv_of(rows))

    report = client.get(f"/api/datasets/{dataset_id}").json()["report"]
    assert (report["skipped_rows"], report["window_warnings"]) == ([], [])
    assert report["requests"] == 2
