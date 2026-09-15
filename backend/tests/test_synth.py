from pathlib import Path

import pytest

from app.domain.enums import EventType, Priority, Skill, Transport
from app.domain.models import Metrics, Office, Plan, Route, Visit
from app.ingest.beeline_csv import RawFile, RawRequestRow
from app.ingest.geocode import GeoResult
from app.synth.config import ShiftTemplate, SynthConfig
from app.synth.engineers import (
    assign_transports,
    build_engineers,
    choose_shift,
    crew_histories,
    historical_car_crews,
    history_medoid,
    largest_remainder,
)
from app.synth.events import build_demo_events
from app.synth.requests import build_requests, check_alignment, synth_duration, synth_transport_required

CONFIG = Path(__file__).resolve().parents[1] / "config" / "synth_config.yaml"


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
):
    return RawRequestRow(
        row_index=index,
        request_id=rid,
        type_bk=type_bk,
        type_hd=type_hd,
        window_start=ws,
        window_end=we,
        district="Таганский",
        address=address,
        status_bk=status,
        crew=crew,
    )


def test_config_loads_all_regions(cfg):
    assert set(cfg.regions) == {"east", "south_east", "south_center"}
    assert cfg.skill_by_bk["Дозаказ"] == Skill.CONNECTION


def test_duration_is_deterministic_rounded_and_within_jitter(cfg):
    base = cfg.duration_by_hd["Авария"]
    first = synth_duration(cfg, "74198", "Авария")
    assert first == synth_duration(cfg, "74198", "Авария")
    assert first % cfg.duration_round_to == 0
    assert (
        base * (1 - cfg.duration_jitter) - cfg.duration_round_to
        <= first
        <= base * (1 + cfg.duration_jitter) + cfg.duration_round_to
    )
    default = synth_duration(cfg, "1", "Неизвестный тип")
    assert (
        abs(default - cfg.default_duration_min)
        <= cfg.default_duration_min * cfg.duration_jitter + cfg.duration_round_to
    )


def test_transport_rules(cfg):
    assert synth_transport_required(cfg, Skill.EMERGENCY, "Авария") == Transport.CAR
    assert (
        synth_transport_required(cfg, Skill.CONNECTION, "Заказ подключения/Дозаказ оборудования")
        == Transport.CAR
    )
    assert synth_transport_required(cfg, Skill.LOCAL, "Нет линка") is None


def test_largest_remainder_sums_to_total(cfg):
    counts = largest_remainder(cfg.transport_mix, 12)
    assert sum(counts.values()) == 12


def test_assign_transports_covers_all_types_and_gives_cars_to_emergency(cfg):
    engineers = [(f"E{k:02d}", {Skill.EMERGENCY} if k < 4 else {Skill.LOCAL}) for k in range(12)]
    result = assign_transports(cfg, "east", engineers)
    assert set(result.values()) == set(Transport)
    assert all(result[f"E{k:02d}"] == Transport.CAR for k in range(4))
    assert result == assign_transports(cfg, "east", engineers)


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


def test_build_requests_marks_urgent_from_type_and_control_status(cfg):
    synthetic = RawFile(
        rows=[row(0, "1"), row(1, "2", type_bk="Глобальная проблема", type_hd="Авария", ws=1, we=1439)],
        office_address="x",
        is_control=False,
    )
    control = RawFile(
        rows=[
            row(0, "305", status="Просрочена"),
            row(1, "306", type_bk="Глобальная проблема", type_hd="Авария", ws=1, we=1439),
        ],
        office_address=None,
        is_control=True,
    )
    requests = build_requests(cfg, synthetic, control, _fake_geo)
    assert [r.priority for r in requests] == [Priority.URGENT, Priority.URGENT]
    assert requests[1].skill == Skill.EMERGENCY and requests[1].transport_required == Transport.CAR


def test_build_requests_rejects_unknown_bk_type(cfg):
    synthetic = RawFile(rows=[row(0, "1", type_bk="Непонятно")], office_address="x", is_control=False)
    with pytest.raises(ValueError, match="Неизвестный тип"):
        build_requests(cfg, synthetic, None, _fake_geo)


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
    requests = build_requests(cfg, synthetic, control, _fake_geo)
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
    requests = build_requests(cfg, synthetic, control, _fake_geo)

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
    requests = build_requests(cfg, synthetic, control, _fake_geo)
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
