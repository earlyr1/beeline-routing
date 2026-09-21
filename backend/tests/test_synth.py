from collections import Counter
from pathlib import Path

import pytest

from app.domain.enums import EventType, Priority, RequestTier, Skill, Transport
from app.domain.models import Metrics, Office, Plan, Route, Visit, dispatch_order
from app.ingest.beeline_csv import RawFile, RawRequestRow, parse_beeline_csv
from app.ingest.bundle import load_bundle
from app.ingest.geocode import GeoResult
from app.settings import REPO_ROOT
from app.synth.config import ShiftTemplate, SynthConfig
from app.synth.engineers import (
    assign_transports,
    build_engineers,
    choose_shift,
    crew_histories,
    historical_car_crews,
    history_medoid,
    home_start,
    largest_remainder,
)
from app.synth.events import build_demo_events
from app.synth.requests import (
    build_requests,
    check_alignment,
    synth_duration,
    synth_needs_equipment,
    synth_transport_required,
)

CONFIG = Path(__file__).resolve().parents[1] / "config" / "synth_config.yaml"

# Официальные нормативы организаторов: технические работы + документы, без 20 минут дороги
# (дорогу сервис считает сам по матрице OSRM).
NORM_BY_BK = {"Подключение": 70, "Глобальная проблема": 80, "Дозаказ": 20, "Локальная заявка": 30}


@pytest.fixture(scope="module")
def cfg():
    return SynthConfig.load(CONFIG)


def row(
    index,
    rid,
    type_bk="Локальная заявка",
    type_hd="Нет линка",
    ws=600,
    we=720,
    crew="",
    status="",
    address="Город Москва, ул.Тестовая, д. 1",
    connection="",
    district="Таганский",
):
    return RawRequestRow(
        row_index=index,
        request_id=rid,
        type_bk=type_bk,
        type_hd=type_hd,
        window_start=ws,
        window_end=we,
        district=district,
        address=address,
        status_bk=status,
        crew=crew,
        connection=connection,
    )


def test_config_loads_all_regions(cfg):
    assert set(cfg.regions) == {"east", "south_east", "south_center", "north_west"}
    assert cfg.skill_by_bk["Дозаказ"] == Skill.CONNECTION


def test_duration_is_the_official_norm_of_the_bk_type(cfg):
    """Официальные нормативы («Нормативы.xlsx»): технические работы + документы, без дороги и без разброса."""
    assert cfg.duration_jitter == 0
    assert "duration_by_hd" not in type(cfg).model_fields
    for type_bk, norm in NORM_BY_BK.items():
        assert cfg.duration_by_bk[type_bk] == norm
        first = synth_duration(cfg, "74198", type_bk)
        # Норматив — точное число: без округления в большую сторону и без остатка от старого разброса.
        assert first == norm
        # Та же заявка даёт то же число, а разные заявки одного типа BK — одно и то же число.
        assert first == synth_duration(cfg, "74198", type_bk) == synth_duration(cfg, "50104", type_bk)
    # Неизвестный тип BK считаем по нормативу локальной заявки.
    assert cfg.default_duration_min == 50
    assert synth_duration(cfg, "1", "Неизвестный тип") == 50


def test_transport_rules(cfg):
    assert synth_transport_required(cfg, Skill.EMERGENCY, "Авария") == Transport.CAR
    assert (
        synth_transport_required(cfg, Skill.CONNECTION, "Заказ подключения/Дозаказ оборудования")
        == Transport.CAR
    )
    assert synth_transport_required(cfg, Skill.LOCAL, "Нет линка") is None


def test_needs_equipment_by_hd_type_and_by_connection(cfg):
    assert synth_needs_equipment(cfg, "Дозаказ оборудования", "")
    assert synth_needs_equipment(cfg, "Заказ подключения/Дозаказ оборудования", "")
    assert synth_needs_equipment(cfg, "Заявка на подключение", "")
    assert synth_needs_equipment(cfg, "Роутер. Замена техническим специалистом", "")
    assert synth_needs_equipment(cfg, "TVE/ENT. Замена приставки техником", "")
    assert synth_needs_equipment(cfg, "ТВ. Замена приставки техником", "")
    # Тип работ не из списка: оборудование нужно только при непустой колонке «Подключение».
    assert not synth_needs_equipment(cfg, "Нет линка", "")
    assert not synth_needs_equipment(cfg, "Низкая скорость", "")
    assert synth_needs_equipment(cfg, "Нет линка", "FMC")
    assert synth_needs_equipment(cfg, "Низкая скорость", "FTTB")


def test_build_requests_marks_equipment_from_hd_type_and_connection(cfg):
    synthetic = RawFile(
        rows=[
            row(0, "1"),
            row(1, "2", type_hd="Роутер. Замена техническим специалистом"),
            row(2, "3", connection="FMC"),
        ],
        office_address="x",
        is_control=False,
    )
    requests = build_requests(cfg, synthetic, _fake_geo)
    assert [r.needs_equipment for r in requests] == [False, True, True]
    assert build_requests(cfg, synthetic, _fake_geo) == requests


def test_largest_remainder_sums_to_total(cfg):
    counts = largest_remainder(cfg.transport_mix, 12)
    assert sum(counts.values()) == 12


def test_assign_transports_covers_all_types_and_gives_cars_to_emergency(cfg):
    engineers = [(f"E{k:02d}", {Skill.EMERGENCY} if k < 4 else {Skill.LOCAL}) for k in range(12)]
    result = assign_transports(cfg, "east", engineers)
    assert set(result.values()) == set(Transport)
    assert all(result[f"E{k:02d}"] == Transport.CAR for k in range(4))
    assert result == assign_transports(cfg, "east", engineers)


# Транспорт инженеров регионов до того, как пешеход стал общественным транспортом, с foot, уже заменённым на public.
# Жеребьёвка идёт по прежним долям вместе с foot, поэтому у каждого инженера транспорт прежний.
DRAWN_TRANSPORTS = {
    "east": "public car car bike car public public public car car car car",
    "south_east": "public car car car bike public car car car car car public",
    "south_center": "car public car car car car bike public car public public",
    "north_west": "car car public car car public car public bike public car car",
}


@pytest.mark.parametrize("region", sorted(DRAWN_TRANSPORTS))
def test_transport_draw_did_not_move_when_foot_became_public_transport(cfg, region):
    assert "foot" in cfg.transport_mix
    control = parse_beeline_csv((REPO_ROOT / cfg.regions[region].control).read_bytes())
    office = Office(region=region, title="Офис", address="x", lat=55.7, lon=37.6)
    engineers, _ = build_engineers(cfg, region, control, office)
    assert " ".join(engineer.transport.value for engineer in engineers) == DRAWN_TRANSPORTS[region]


def test_choose_shift_prefers_coverage(cfg):
    two_shifts = cfg.model_copy(
        update={
            "shifts": [ShiftTemplate(start="09:00", end="18:00"), ShiftTemplate(start="13:00", end="22:00")]
        }
    )
    evening = [row(0, "a", ws=1080, we=1200), row(1, "b", ws=1200, we=1320)]
    morning = [row(0, "a", ws=600, we=720)]
    chosen = choose_shift(two_shifts, evening)
    assert (chosen.start, chosen.end) == (780, 1320)
    assert choose_shift(two_shifts, morning).start == 540


def test_check_alignment_detects_mismatch():
    synthetic = RawFile(rows=[row(0, "1", ws=600)], office_address="x", is_control=False)
    control = RawFile(rows=[row(0, "305", ws=720)], office_address=None, is_control=True)
    with pytest.raises(ValueError, match="не согласованы"):
        check_alignment(synthetic, control)


def _fake_geo(address, district):
    return GeoResult(55.75, 37.62, "house", address)


def test_build_requests_marks_urgent_only_from_the_work_type(cfg):
    synthetic = RawFile(
        rows=[row(0, "1"), row(1, "2", type_bk="Глобальная проблема", type_hd="Авария", ws=1, we=1439)],
        office_address="x",
        is_control=False,
    )
    requests = build_requests(cfg, synthetic, _fake_geo)
    assert [r.priority for r in requests] == [Priority.NORMAL, Priority.URGENT]
    assert requests[1].skill == Skill.EMERGENCY and requests[1].transport_required == Transport.CAR


def test_overdue_request_keeps_the_priority_and_tier_of_its_work_type(cfg):
    """«Просрочена» в контрольном файле — итог настоящего дня: диспетчеры на заявке опоздали.

    Утром этого никто не знает, поэтому статус не поднимает приоритет. Просроченная заявка остаётся заявкой своего
    типа работ, а авария срочная при любом статусе.
    """
    overdue = []
    for region, region_cfg in cfg.regions.items():
        control = parse_beeline_csv((REPO_ROOT / region_cfg.control).read_bytes())
        synthetic = parse_beeline_csv((REPO_ROOT / region_cfg.synthetic).read_bytes())
        requests = build_requests(cfg, synthetic, _fake_geo)
        for control_row, request in zip(control.rows, requests, strict=True):
            if control_row.status_bk == "Просрочена":
                overdue.append((region, request))
    assert Counter((region, request.source_type_bk) for region, request in overdue) == {
        ("east", "Подключение"): 3,
        ("east", "Дозаказ"): 1,
        ("south_east", "Подключение"): 2,
        ("south_east", "Локальная заявка"): 4,
        ("north_west", "Локальная заявка"): 4,
        ("north_west", "Глобальная проблема"): 1,
    }
    for _, request in overdue:
        urgent = request.source_type_bk in cfg.urgent_bk_types
        assert request.priority == (Priority.URGENT if urgent else Priority.NORMAL), request.id
        assert request.tier == cfg.tier_by_bk[request.source_type_bk], request.id


# Срочных заявок в бандлах репозитория: только аварии («Глобальная проблема») выгрузки дня.
SHIPPED_URGENT = {"east": 8, "south_east": 12, "south_center": 1, "north_west": 9}


@pytest.mark.parametrize("region", sorted(SHIPPED_URGENT))
def test_shipped_bundle_marks_urgent_only_by_the_work_type(cfg, region):
    """Бандлы репозитория — то, что диспетчер видит на экране: заявки из них берёт и загрузка JSON, и сырой CSV
    с теми же номерами. Бандл, собранный по старому правилу «Просрочена → срочная» или возвращённый к прежней
    сборке, падает здесь, даже если build_requests уже правильный.
    """
    bundle = load_bundle(REPO_ROOT / "data" / "bundles" / region / "bundle.json")
    for request in bundle.requests:
        urgent = request.source_type_bk in cfg.urgent_bk_types
        assert request.priority == (Priority.URGENT if urgent else Priority.NORMAL), (region, request.id)
        assert request.tier == cfg.tier_by_bk[request.source_type_bk], (region, request.id)
    assert sum(request.priority == Priority.URGENT for request in bundle.requests) == SHIPPED_URGENT[region]


def test_tier_comes_from_the_bk_type_and_not_from_the_skill(cfg):
    """Ответ организаторов (вопрос 15): авария → подключение → ремонт и дозаказ.

    Ловушка данных: «Дозаказ» и «Подключение» делят навык connection, но дозаказ остаётся на нижнем уровне.
    """
    synthetic = RawFile(
        rows=[
            row(0, "1"),
            row(1, "2", type_bk="Подключение", type_hd="Заявка на подключение"),
            row(2, "3", type_bk="Глобальная проблема", type_hd="Авария", ws=1, we=1439),
            row(3, "4", type_bk="Дозаказ", type_hd="Дозаказ оборудования"),
        ],
        office_address="x",
        is_control=False,
    )
    requests = build_requests(cfg, synthetic, _fake_geo)
    assert [r.tier for r in requests] == [
        RequestTier.ROUTINE,
        RequestTier.CONNECTION,
        RequestTier.EMERGENCY,
        RequestTier.ROUTINE,
    ]
    extra, connection = requests[3], requests[1]
    assert extra.skill == connection.skill == Skill.CONNECTION
    assert dispatch_order(connection) < dispatch_order(extra)


def test_engineers_take_the_daily_equipment_stock_from_the_config(cfg):
    control = RawFile(
        rows=[row(0, "1", crew="Бригада А"), row(1, "2", crew="Бригада Б")],
        office_address=None,
        is_control=True,
    )
    office = Office(region="east", title="Восток", address="x", lat=55.7, lon=37.7)
    engineers, _ = build_engineers(cfg, "east", control, office)
    assert {e.equipment_stock for e in engineers} == {cfg.equipment_stock}


def test_build_requests_puts_the_official_norm_into_the_request(cfg):
    synthetic = RawFile(
        rows=[
            row(0, "1"),
            row(1, "2", type_bk="Подключение", type_hd="Заявка на подключение"),
            row(2, "3", type_bk="Глобальная проблема", type_hd="Информация"),
            row(3, "4", type_bk="Дозаказ", type_hd="Дозаказ оборудования"),
        ],
        office_address="x",
        is_control=False,
    )
    requests = build_requests(cfg, synthetic, _fake_geo)
    # «Информация» стоила 15 минут по типу HD, теперь делит 80 минут норматива своего типа BK.
    assert [request.duration_min for request in requests] == [30, 70, 80, 20]


def test_build_requests_rejects_unknown_bk_type(cfg):
    synthetic = RawFile(rows=[row(0, "1", type_bk="Непонятно")], office_address="x", is_control=False)
    with pytest.raises(ValueError, match="Неизвестный тип"):
        build_requests(cfg, synthetic, _fake_geo)


def test_build_engineers_from_crew_history(cfg):
    control = RawFile(
        rows=[
            row(0, "1", crew="Бригада Б"),
            row(1, "2", type_bk="Подключение", crew="Бригада А"),
            row(2, "3", type_bk="Глобальная проблема", crew="Бригада А"),
            row(3, "4", crew=""),
        ],
        office_address=None,
        is_control=True,
    )
    office = Office(region="east", title="Восток", address="x", lat=55.7, lon=37.7)
    engineers, mapping = build_engineers(cfg, "east", control, office)
    assert mapping == {"Бригада А": "E01", "Бригада Б": "E02"}
    assert engineers[0].skills == [Skill.CONNECTION, Skill.EMERGENCY]
    assert engineers[0].transport == Transport.CAR
    assert (engineers[1].start_lat, engineers[1].start_lon) == (55.7, 37.7)


def test_demo_events_cover_three_event_types(cfg):
    synthetic = RawFile(
        rows=[row(0, "1", ws=900, we=1020), row(1, "2")], office_address="x", is_control=False
    )
    control = RawFile(
        rows=[
            row(0, "305", status="Отменена", crew="Бригада А", ws=900, we=1020),
            row(1, "306", crew="Бригада А"),
        ],
        office_address=None,
        is_control=True,
    )
    requests = build_requests(cfg, synthetic, _fake_geo)
    events = build_demo_events(cfg, "east", requests, control, synthetic, {"Бригада А": "E01"})
    assert [e.type for e in events] == [EventType.CANCEL, EventType.ENGINEER_UNAVAILABLE, EventType.URGENT]
    assert events[0].request_id == "1"
    assert events[2].request.window_start == 780 and events[2].request.priority == Priority.URGENT


def test_transport_from_history_gives_cars_to_crews_that_carried_equipment(cfg):
    control = RawFile(
        rows=[
            row(0, "1", type_bk="Дозаказ", type_hd="Дозаказ оборудования", crew="Бригада А"),
            row(1, "2", crew="Бригада Б"),
            row(2, "3", crew="Бригада В"),
            row(3, "4", crew="Бригада Г"),
            row(4, "5", crew="Бригада Д"),
        ],
        office_address=None,
        is_control=True,
    )
    office = Office(region="east", title="Восток", address="x", lat=55.7, lon=37.7)
    history_cfg = cfg.model_copy(update={"transport_from_history": True})
    assert historical_car_crews(history_cfg, crew_histories(control)) == {"Бригада А"}
    engineers, _ = build_engineers(history_cfg, "east", control, office)
    by_name = {engineer.name: engineer for engineer in engineers}
    assert by_name["Бригада А"].transport == Transport.CAR
    assert set(engineer.transport for engineer in engineers) == set(Transport)


def test_history_medoid_picks_the_most_central_real_address():
    rows = [row(0, "1"), row(1, "2"), row(2, "3")]
    points = {0: (55.70, 37.60), 1: (55.709, 37.60), 2: (55.79, 37.60)}
    assert history_medoid(rows, points) == (55.709, 37.60)
    assert history_medoid(rows, {}) is None


def test_engineer_start_from_history_medoid(cfg):
    control = RawFile(
        rows=[row(0, "1", crew="Бригада А"), row(1, "2", crew="Бригада А")],
        office_address=None,
        is_control=True,
    )
    office = Office(region="east", title="Восток", address="x", lat=55.7, lon=37.7)
    medoid_cfg = cfg.model_copy(update={"engineer_start": "history_medoid"})
    engineers, _ = build_engineers(
        medoid_cfg, "east", control, office, {0: (54.83, 38.15), 1: (54.84, 38.16)}
    )
    assert (engineers[0].start_lat, engineers[0].start_lon) in {(54.83, 38.15), (54.84, 38.16)}
    office_cfg = cfg.model_copy(update={"engineer_start": "office"})
    office_engineers, _ = build_engineers(office_cfg, "east", control, office, {0: (54.83, 38.15)})
    assert (office_engineers[0].start_lat, office_engineers[0].start_lon) == (55.7, 37.7)


def test_shipped_config_starts_brigades_by_the_organisers_answers(cfg):
    """Ответы организаторов: оборудование выдают в офисе (вопрос 4), дом — только в удалённом городе (вопрос 13)."""
    assert cfg.engineer_start == "office"
    assert set(cfg.home_districts) == {"Кашира", "Ступино", "Домодедово"}


# Три заявки в Кашире на одной линии (средняя — медоид), одна в Ступино и две московские.
KASHIRA = {0: (54.83, 38.15), 1: (54.835, 38.155), 2: (54.84, 38.16)}
STUPINO = {5: (54.89, 38.08)}
MOSCOW = {3: (55.74, 37.65), 4: (55.745, 37.655)}
OFFICE = Office(region="south_east", title="Юго-восток", address="x", lat=55.61, lon=37.72)


def _start(cfg, rows, points):
    control = RawFile(rows=rows, office_address=None, is_control=True)
    engineers, _ = build_engineers(cfg, "south_east", control, OFFICE, points)
    return engineers[0].start_lat, engineers[0].start_lon


def test_brigade_mostly_in_a_suburban_district_starts_at_home(cfg):
    """Больше половины истории в Подмосковье: старт — медоид заявок в этих районах, московские не в счёт.

    Московские заявки тянут медоид всей истории к Ступино, ближе к Москве; дом остаётся в Кашире.
    """
    rows = [
        row(0, "0", crew="Бригада А", district="Кашира"),
        row(1, "1", crew="Бригада А", district="Кашира"),
        row(5, "5", crew="Бригада А", district="Ступино"),
        row(3, "3", crew="Бригада А"),
        row(4, "4", crew="Бригада А"),
    ]
    points = {**KASHIRA, **STUPINO, **MOSCOW}
    assert _start(cfg, rows, points) == KASHIRA[1]
    assert history_medoid(rows, points) == STUPINO[5]


def test_brigade_with_half_its_history_in_the_suburbs_starts_at_the_office(cfg):
    rows = [row(k, str(k), crew="Бригада А", district="Кашира") for k in (0, 1)]
    rows += [row(k, str(k), crew="Бригада А") for k in MOSCOW]
    assert _start(cfg, rows, {**KASHIRA, **MOSCOW}) == (OFFICE.lat, OFFICE.lon)


def test_moscow_brigade_starts_at_the_office_even_with_known_addresses(cfg):
    rows = [row(k, str(k), crew="Бригада А") for k in MOSCOW]
    assert _start(cfg, rows, MOSCOW) == (OFFICE.lat, OFFICE.lon)


def test_suburban_brigade_without_found_suburban_addresses_starts_at_the_office(cfg):
    """Большинство в Кашире, но ни один её адрес там не найден геокодером: дома не из чего взять, старт в офисе."""
    rows = [row(k, str(k), crew="Бригада А", district="Кашира") for k in (6, 7, 8)]
    rows.append(row(3, "3", crew="Бригада А"))
    assert _start(cfg, rows, MOSCOW) == (OFFICE.lat, OFFICE.lon)


def test_suburban_majority_counts_every_row_of_the_history_found_or_not(cfg):
    """Две заявки из пяти в Кашире — не большинство, хотя из найденных геокодером адресов других нет."""
    rows = [row(k, str(k), crew="Бригада А", district="Кашира") for k in (0, 1)]
    rows += [row(k, str(k), crew="Бригада А") for k in (6, 7, 8)]
    assert _start(cfg, rows, KASHIRA) == (OFFICE.lat, OFFICE.lon)


def test_brigade_serving_two_suburban_towns_starts_among_them(cfg):
    """Как Козырь: Кашира и Ступино вместе дают большинство, оба района — дом."""
    rows = [
        row(0, "0", crew="Бригада А", district="Кашира"),
        row(1, "1", crew="Бригада А", district="Ступино"),
        row(2, "2", crew="Бригада А", district="Кашира"),
    ]
    assert _start(cfg, rows, KASHIRA) == KASHIRA[1]


# Бригады, чья стартовая точка в бандлах репозитория не совпадает с офисом региона (ответ организаторов, вопрос 13).
SHIPPED_HOME_BRIGADES = {
    "east": set(),
    "south_east": {"Бригада Каушнян", "Бригада Козырь", "Бригада Паршин", "Бригада Саламатин"},
    "south_center": set(),
    "north_west": set(),
}


@pytest.mark.parametrize("region", sorted(SHIPPED_HOME_BRIGADES))
def test_shipped_bundle_starts_only_suburban_brigades_at_home(cfg, region):
    """Бандл, собранный со стартом из медоида истории, здесь падает: там московские бригады начинают у своих
    заявок, а по правилу — в офисе. Дом бригады из Подмосковья — медоид её заявок в home_districts.
    """
    bundle = load_bundle(REPO_ROOT / "data" / "bundles" / region / "bundle.json")
    office = (bundle.office.lat, bundle.office.lon)
    at_home = {e.name for e in bundle.engineers if (e.start_lat, e.start_lon) != office}
    assert at_home == SHIPPED_HOME_BRIGADES[region]

    # Точки заявок берутся из бандла так же, как prepare_region передаёт их в build_engineers при сборке.
    control = parse_beeline_csv((REPO_ROOT / cfg.regions[region].control).read_bytes())
    histories = crew_histories(control)
    points = {
        k: (r.lat, r.lon) for k, r in enumerate(bundle.requests) if r.lat is not None and r.lon is not None
    }
    for engineer in bundle.engineers:
        if engineer.name in at_home:
            home = home_start(cfg.home_districts, histories[engineer.name], points)
            assert (engineer.start_lat, engineer.start_lon) == home


def test_demo_events_follow_the_optimized_plan(cfg):
    synthetic = RawFile(
        rows=[row(0, "1", ws=900, we=1020), row(1, "2", ws=900, we=1020), row(2, "3")],
        office_address="x",
        is_control=False,
    )
    control = RawFile(
        rows=[
            row(0, "305", status="Отменена", crew="Бригада А", ws=900, we=1020),
            row(1, "306", status="Отменена", crew="Бригада А", ws=900, we=1020),
            row(2, "307", crew="Бригада А"),
        ],
        office_address=None,
        is_control=True,
    )
    requests = build_requests(cfg, synthetic, _fake_geo)

    def visit(request_id, start):
        return Visit(request_id=request_id, arrival=start, start=start, end=start + 30, leg_km=1.0, leg_min=5)

    plan = Plan(
        solver="ortools",
        routes=[
            Route(engineer_id="E01", visits=[visit("3", 600)]),
            Route(engineer_id="E02", visits=[visit("2", 900)]),
        ],
        unassigned=[],
        metrics=Metrics(engineers_used=2, km_per_engineer={}, total_km=2.0, assigned=2, unassigned=0),
    )
    events = build_demo_events(cfg, "east", requests, control, synthetic, {"Бригада А": "E01"}, plan)
    assert events[0].request_id == "2"
    assert events[1].engineer_id == "E02"


def test_demo_cancel_prefers_window_starting_after_event_time(cfg):
    """Заявку с окном 12:00–14:00 при другом лимите поиска начнут в 12:00, и API отклонит отмену в 13:00."""
    synthetic = RawFile(
        rows=[row(0, "1", ws=720, we=840), row(1, "2", ws=900, we=1020)], office_address="x", is_control=False
    )
    control = RawFile(
        rows=[
            row(0, "305", status="Отменена", crew="Бригада А", ws=720, we=840),
            row(1, "306", status="Отменена", crew="Бригада А", ws=900, we=1020),
        ],
        office_address=None,
        is_control=True,
    )
    requests = build_requests(cfg, synthetic, _fake_geo)
    visits = [
        Visit(request_id="1", arrival=786, start=786, end=816, leg_km=1.0, leg_min=5),
        Visit(request_id="2", arrival=960, start=960, end=990, leg_km=1.0, leg_min=5),
    ]
    plan = Plan(
        solver="ortools",
        routes=[Route(engineer_id="E01", visits=visits)],
        unassigned=[],
        metrics=Metrics(engineers_used=1, km_per_engineer={}, total_km=2.0, assigned=2, unassigned=0),
    )
    events = build_demo_events(cfg, "east", requests, control, synthetic, {"Бригада А": "E01"}, plan)
    assert events[0].request_id == "2"
