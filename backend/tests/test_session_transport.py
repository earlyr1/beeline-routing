"""Смена транспорта инженера посреди дня."""

import pytest

from app.domain.enums import EventType, ReasonCode, Skill, Transport
from app.domain.models import Event
from app.planning.session import EventRejected, apply_event, check_event
from tests.helpers import eng, req
from tests.planning_helpers import (
    EXACT_TRAVEL_LEVEL,
    IN_TRANSIT_TO_B,
    busy_engineer,
    context,
    new_session,
    other_engineer,
    routes,
    transit_session,
    visit_times,
)


def change(engineer_id, transport, time="13:00", **extra):
    return Event(
        type=EventType.ENGINEER_TRANSPORT_CHANGED,
        time=time,
        engineer_id=engineer_id,
        transport=transport,
        **extra,
    )


def downgrade_requests():
    """R1 утром до события, R2 после события только на автомобиле, R3 после события без требований."""
    return [
        req("R1", 1, 0, "10:00", "12:00"),
        req("R2", 1.2, 0, "14:00", "16:00", skill=Skill.CONNECTION, transport=Transport.CAR),
        req("R3", -1, 0, "15:00", "17:00"),
    ]


def route_of(plan, engineer_id):
    return next(route for route in plan.routes if route.engineer_id == engineer_id)


def visit_of(plan, engineer_id, request_id):
    return next(v for v in route_of(plan, engineer_id).visits if v.request_id == request_id)


def test_downgrade_to_bike_moves_car_only_work_and_recomputes_later_legs():
    ctx = context()
    # E2 умеет только подключения: утром выгоднее отдать всё E1.
    engineers = [eng("E1"), eng("E2", skills=[Skill.CONNECTION])]
    # Минуты участков сверяются прямо с матрицей, поэтому дорога без запаса.
    session = new_session(
        ctx=ctx, requests=downgrade_requests(), engineers=engineers, workload_level=EXACT_TRAVEL_LEVEL
    )
    assert routes(session.plan) == {"E1": ["R1", "R2", "R3"], "E2": []}
    morning = visit_of(session.plan, "E1", "R1")

    updated = apply_event(session, change("E1", "bike", previous_transport="public"), ctx)

    assert routes(updated.plan) == {"E1": ["R1", "R3"], "E2": ["R2"]}
    assert "R2" not in routes(updated.baseline)["E1"]
    started = visit_of(updated.plan, "E1", "R1")
    assert (started.start, started.leg_km, started.leg_min, started.pinned) == (
        morning.start,
        morning.leg_km,
        morning.leg_min,
        True,
    )
    # После события E1 едет от R1 к R3 на велосипеде: расстояние по дорогам то же, скорость ниже.
    travel = updated.problem.travel
    r1, r3 = updated.problem.request_node("R1"), updated.problem.request_node("R3")
    after = visit_of(updated.plan, "E1", "R3")
    assert after.leg_min == travel.minutes(r1, r3, Transport.BIKE, 900)
    assert after.leg_min != travel.minutes(r1, r3, Transport.CAR, 900)
    assert after.leg_km == round(travel.km(r1, r3, Transport.BIKE), 2)
    assert (after.arrival, after.late_min) == (780 + after.leg_min, 0)

    [applied] = updated.events
    assert (applied.event.type, applied.event.previous_transport, applied.event.transport) == (
        EventType.ENGINEER_TRANSPORT_CHANGED,
        Transport.CAR,
        Transport.BIKE,
    )
    assert (applied.id, applied.version, updated.version, updated.now) == ("ev_1", 2, 2, 780)
    assert updated.engineer("E1").transport == Transport.BIKE
    assert session.engineer("E1").transport == Transport.CAR  # исходная сессия не изменилась
    assert [(m.request_id, m.to_engineer_id) for m in updated.last_diff.moved] == [("R2", "E2")]


def test_downgrade_without_other_car_engineer_leaves_car_work_unassigned():
    ctx = context()
    session = new_session(ctx=ctx, requests=downgrade_requests(), engineers=[eng("E1")])
    updated = apply_event(session, change("E1", "bike"), ctx)
    assert routes(updated.plan) == {"E1": ["R1", "R3"]}
    [lost] = updated.plan.unassigned
    assert (lost.request_id, lost.reason_code) == ("R2", ReasonCode.NO_TRANSPORT)
    assert (
        lost.reason_text
        == "Нет инженера с навыком «Работы на подключение и дозаказы» и транспортом «Автомобиль»."
    )
    assert [(r.request_id, r.engineer_id) for r in updated.last_diff.removed] == [("R2", "E1")]


def test_upgrade_to_car_lets_idle_engineer_take_car_only_request():
    ctx = context()
    requests = [req("CR", 2, 0, "14:00", "16:00", transport=Transport.CAR)]
    engineers = [eng("E1", shift=("09:00", "12:00")), eng("E2", transport=Transport.PUBLIC)]
    session = new_session(ctx=ctx, requests=requests, engineers=engineers, workload_level=EXACT_TRAVEL_LEVEL)
    [waiting] = session.plan.unassigned
    assert (waiting.request_id, waiting.reason_code) == ("CR", ReasonCode.DOES_NOT_FIT)

    updated = apply_event(session, change("E2", "car"), ctx)

    assert routes(updated.plan) == {"E1": [], "E2": ["CR"]}
    assert updated.plan.unassigned == []
    assert routes(updated.baseline)["E2"] == ["CR"]
    visit = visit_of(updated.plan, "E2", "CR")
    travel = updated.problem.travel
    home, node = updated.problem.home_node("E2"), updated.problem.request_node("CR")
    assert (visit.leg_km, visit.leg_min) == (
        round(travel.km(home, node, Transport.CAR), 2),
        travel.minutes(home, node, Transport.CAR, 840),
    )
    stored = updated.events[0].event
    assert (stored.previous_transport, stored.transport) == (Transport.PUBLIC, Transport.CAR)
    assert updated.engineer("E2").transport == Transport.CAR


def test_visit_on_the_way_is_kept_after_transport_change():
    ctx = context()
    session = transit_session(ctx)
    busy = busy_engineer(session.plan)
    before = route_of(session.plan, busy).visits

    updated = apply_event(session, change(busy, "public", time=IN_TRANSIT_TO_B), ctx)

    held = [visit.request_id for visit in updated.problem.pinned[busy]]
    assert held == ["A", "B"]
    after = route_of(updated.plan, busy).visits
    for old, new in zip(before[:2], after[:2], strict=True):
        assert new.model_dump(exclude={"pinned"}) == old.model_dump(exclude={"pinned"})
    assert visit_times(updated.plan, busy)[:2] == visit_times(session.plan, busy)[:2]
    assert [visit.pinned for visit in after[:2]] == [True, False]
    # C остаётся у того же инженера (второго задействовать дороже), но к нему он уже едет общественным транспортом.
    assert routes(updated.plan) == {busy: ["A", "B", "C"], other_engineer(busy): []}
    travel = updated.problem.travel
    b, c = updated.problem.request_node("B"), updated.problem.request_node("C")
    assert (after[2].leg_km, after[2].leg_min) == (
        round(travel.km(b, c, Transport.PUBLIC), 2),
        travel.minutes(b, c, Transport.PUBLIC, 630),
    )
    assert after[2].arrival == before[1].end + after[2].leg_min


def test_transport_change_rejections_are_russian():
    ctx = context()
    session = new_session(ctx=ctx)
    cases = [
        (session, change("E9", "bike"), "Инженер E9 не найден."),
        (session, change("E1", "car"), "У Инженер E1 уже транспорт «Автомобиль»."),
    ]
    unavailable = apply_event(
        session, Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id="E2"), ctx
    )
    cases += [
        (
            unavailable,
            change("E2", "bike", time="13:30"),
            "Инженер E2 недоступен с 13:00, сменить транспорт нельзя.",
        ),
        (
            unavailable,
            change("E1", "bike", time="12:00"),
            "Время события 12:00 раньше текущего времени плана 13:00.",
        ),
    ]
    for current, event, text in cases:
        with pytest.raises(EventRejected) as rejected:
            apply_event(current, event, ctx)
        assert str(rejected.value) == text
        with pytest.raises(EventRejected) as checked:
            check_event(current, event, ctx)
        assert str(checked.value) == text


def test_check_event_fills_previous_transport_without_replanning():
    ctx = context()
    session = new_session(ctx=ctx)
    stored = check_event(session, change("E1", "public", previous_transport="bike"), ctx)
    assert (stored.previous_transport, stored.transport) == (Transport.CAR, Transport.PUBLIC)
    assert session.engineer("E1").transport == Transport.CAR and session.version == 1


def test_old_foot_value_is_accepted_as_public_transport():
    ctx = context()
    session = new_session(ctx=ctx, engineers=[eng("E1"), eng("E2", transport="foot")])
    assert session.engineer("E2").transport == Transport.PUBLIC

    stored = check_event(session, change("E1", "foot", previous_transport="foot"), ctx)
    assert (stored.previous_transport, stored.transport) == (Transport.CAR, Transport.PUBLIC)
    assert stored.model_dump(mode="json")["transport"] == "public"
    with pytest.raises(EventRejected) as rejected:
        check_event(session, change("E2", "foot"), ctx)
    assert str(rejected.value) == "У Инженер E2 уже транспорт «Общественный транспорт и пешком»."
