"""Задержка инженера посреди дня: на объекте, в пути, до выезда и прогноз опозданий без перепланирования."""

import pytest

from app.domain.enums import EventType
from app.domain.models import Event
from app.planning.session import EventRejected, apply_event, check_event
from tests.helpers import eng
from tests.planning_helpers import (
    EXACT_TRAVEL_LEVEL,
    IN_TRANSIT_TO_B,
    busy_engineer,
    context,
    new_session,
    other_engineer,
    routes,
    transit_requests,
    transit_session,
    visit_times,
)

# Минуты в пути без запаса на дорогу (EXACT_TRAVEL_LEVEL).
# В дне transit_session у занятого инженера A 09:16–09:46, B 09:59–10:29 (окно до 10:15), C 10:42–11:12.
# Переезды A→B и B→C по 13 минут. В 09:30 инженер на объекте A.
ON_SITE_A = "09:30"


def delay(engineer_id, delay_min, time="13:00"):
    return Event(type=EventType.ENGINEER_DELAYED, time=time, engineer_id=engineer_id, delay_min=delay_min)


def route_of(plan, engineer_id):
    return next(route for route in plan.routes if route.engineer_id == engineer_id)


def visit_of(plan, request_id):
    return next(
        (route.engineer_id, visit)
        for route in plan.routes
        for visit in route.visits
        if visit.request_id == request_id
    )


def test_delay_on_site_extends_started_visit_and_pushes_the_rest():
    ctx = context()
    session = transit_session(ctx)
    busy = busy_engineer(session.plan)
    assert visit_times(session.plan, busy) == [
        ("A", 556, 556, 586),
        ("B", 599, 599, 629),
        ("C", 642, 642, 672),
    ]

    updated = apply_event(session, delay(busy, 30, time=ON_SITE_A), ctx)

    first, *after = route_of(updated.plan, busy).visits
    assert (first.request_id, first.arrival, first.start, first.end, first.pinned) == (
        "A",
        556,
        556,
        616,
        True,
    )
    assert [visit.request_id for visit in updated.problem.pinned[busy]] == ["A"]
    assert updated.problem.state(busy).available_from == 616
    # С объекта A инженер приедет к B только в 10:29, а окно B до 10:15: B уходит другому инженеру.
    assert routes(updated.plan) == {busy: ["A", "C"], other_engineer(busy): ["B"]}
    assert after[0].request_id == "C" and after[0].arrival == 616 + after[0].leg_min

    [applied] = updated.events
    assert (applied.event.type, applied.event.engineer_id, applied.event.delay_min) == (
        EventType.ENGINEER_DELAYED,
        busy,
        30,
    )
    assert (applied.id, applied.version, updated.version, updated.now) == ("ev_1", 2, 2, 570)
    assert updated.engineers == session.engineers
    assert visit_times(session.plan, busy)[0] == ("A", 556, 556, 586)  # исходная сессия не изменилась
    assert updated.baseline.solver == "fcfs"
    baseline_first = route_of(updated.baseline, busy).visits[0]
    assert (baseline_first.request_id, baseline_first.end, baseline_first.pinned) == ("A", 616, True)


def test_delay_on_the_way_within_window_shifts_held_visit():
    ctx = context()
    session = transit_session(ctx)
    busy = busy_engineer(session.plan)

    updated = apply_event(session, delay(busy, 10, time=IN_TRANSIT_TO_B), ctx)

    assert [(v.request_id, v.pinned) for v in updated.problem.pinned[busy]] == [("A", True), ("B", False)]
    held = route_of(updated.plan, busy).visits[1]
    assert (held.request_id, held.arrival, held.start, held.end, held.pinned) == ("B", 609, 609, 639, False)
    assert (held.leg_min, held.late_min) == (13, 0)
    assert updated.problem.state(busy).available_from == 639
    assert updated.problem.state(busy).start_node == updated.problem.request_node("B")
    assert routes(updated.plan) == {busy: ["A", "B", "C"], other_engineer(busy): []}
    _, c = visit_of(updated.plan, "C")
    assert c.arrival == 639 + c.leg_min


def test_delay_on_the_way_waits_for_window_start():
    """Инженер ждал бы начала окна у объекта: задержка съедает ожидание, а начало остаётся в начале окна."""
    ctx = context()
    requests = transit_requests()
    requests[1] = requests[1].model_copy(update={"window_start": 610, "window_end": 640})
    session = new_session(ctx=ctx, requests=requests, workload_level=EXACT_TRAVEL_LEVEL)
    busy = busy_engineer(session.plan)
    assert visit_times(session.plan, busy)[1] == ("B", 599, 610, 640)

    updated = apply_event(session, delay(busy, 5, time="09:58"), ctx)

    held = route_of(updated.plan, busy).visits[1]
    assert (held.request_id, held.arrival, held.start, held.end) == ("B", 604, 610, 640)


def test_delay_on_the_way_beyond_window_releases_the_hold():
    ctx = context()
    session = transit_session(ctx)
    busy = busy_engineer(session.plan)

    updated = apply_event(session, delay(busy, 30, time=IN_TRANSIT_TO_B), ctx)

    assert [v.request_id for v in updated.problem.pinned[busy]] == ["A"]
    assert "B" in updated.problem.open_request_ids
    state = updated.problem.state(busy)
    assert (state.available_from, state.start_node) == (617, updated.problem.request_node("A"))
    # С объекта A в 10:17 к B не успеть до 10:15, а второй инженер из офиса успевает.
    assert routes(updated.plan) == {busy: ["A", "C"], other_engineer(busy): ["B"]}
    assert updated.plan.unassigned == []


def test_delay_before_departure_moves_available_from():
    ctx = context()
    session = new_session(ctx=ctx)
    busy = busy_engineer(session.plan)
    _, first = visit_of(session.plan, "R1")
    assert first.arrival - first.leg_min == 540  # выезд к R1 в 09:00, в 09:00 инженер ещё не выехал

    updated = apply_event(session, delay(busy, 45, time="09:00"), ctx)

    assert updated.problem.state(busy).available_from == 585
    assert updated.problem.pinned[busy] == []
    engineer, visit = visit_of(updated.plan, "R1")
    assert (engineer, visit.arrival, visit.start) == (busy, 585 + visit.leg_min, 600)


def test_delay_that_ends_before_departure_changes_nothing():
    ctx = context()
    session = new_session(ctx=ctx)

    updated = apply_event(session, delay(busy_engineer(session.plan), 60, time="07:00"), ctx)

    for engineer in ("E1", "E2"):
        assert visit_times(updated.plan, engineer) == visit_times(session.plan, engineer)
    assert updated.last_diff.time_shifts == []
    assert updated.last_diff.delay_forecast.late_without_replan == []


def test_delay_past_shift_end_on_site_keeps_started_work_and_stops_new_visits():
    ctx = context()
    session = new_session(ctx=ctx, workload_level=EXACT_TRAVEL_LEVEL)
    busy = busy_engineer(session.plan)
    assert visit_times(session.plan, busy)[0] == ("R1", 544, 600, 630)

    updated = apply_event(session, delay(busy, 480, time="10:15"), ctx)

    [started] = route_of(updated.plan, busy).visits
    assert (started.request_id, started.start, started.end, started.pinned) == ("R1", 600, 1110, True)
    assert [visit.request_id for visit in route_of(updated.baseline, busy).visits] == ["R1"]
    assert sorted(routes(updated.plan)[other_engineer(busy)]) == ["R2", "R3"]
    assert updated.engineer(busy).available is True


def test_delay_up_to_shift_end_while_idle_stops_new_visits():
    ctx = context()
    session = new_session(ctx=ctx)
    busy = busy_engineer(session.plan)

    updated = apply_event(session, delay(busy, 300, time="13:00"), ctx)

    assert updated.problem.state(busy).available_from == 1080  # ровно конец смены
    assert routes(updated.plan)[busy] == ["R1"]
    assert routes(updated.baseline)[busy] == ["R1"]
    assert route_of(updated.plan, busy).visits[0].pinned is True
    assert sorted(routes(updated.plan)[other_engineer(busy)]) == ["R2", "R3"]
    assert updated.engineer(busy).available is True


def test_forecast_lists_visits_that_would_miss_their_windows():
    ctx = context()
    session = transit_session(ctx)
    busy = busy_engineer(session.plan)

    updated = apply_event(session, delay(busy, 20, time=ON_SITE_A), ctx)

    forecast = updated.last_diff.delay_forecast
    assert (forecast.engineer_id, forecast.delay_min) == (busy, 20)
    # A заканчивается в 10:06, B: прибытие и начало 10:19 при окне до 10:15. C: 11:02 при окне до 17:00.
    assert [late.model_dump(mode="json") for late in forecast.late_without_replan] == [
        {"request_id": "B", "planned_start": "09:59", "forecast_start": "10:19", "late_min": 4}
    ]
    assert forecast.overtime_without_replan_min == 0


def test_forecast_counts_overtime_and_is_empty_when_nothing_is_late():
    ctx = context()
    short = [eng("E1", shift=("09:00", "11:15")), eng("E2", shift=("09:00", "11:15"))]
    session = new_session(
        ctx=ctx, requests=transit_requests(), engineers=short, workload_level=EXACT_TRAVEL_LEVEL
    )
    busy = busy_engineer(session.plan)
    assert visit_times(session.plan, busy)[2] == ("C", 642, 642, 672)

    small = apply_event(session, delay(busy, 5, time=ON_SITE_A), ctx).last_diff.delay_forecast
    assert small.late_without_replan == []
    assert small.overtime_without_replan_min == 2  # C закончится в 11:17 при смене до 11:15

    held = apply_event(session, delay(busy, 10, time=IN_TRANSIT_TO_B), ctx).last_diff.delay_forecast
    assert held.late_without_replan == []
    assert held.overtime_without_replan_min == 7  # B 10:09–10:39, C 10:52–11:22


def test_forecast_after_released_hold_starts_from_delayed_position():
    ctx = context()
    session = transit_session(ctx)
    busy = busy_engineer(session.plan)

    forecast = apply_event(session, delay(busy, 30, time=IN_TRANSIT_TO_B), ctx).last_diff.delay_forecast

    # С объекта A в 10:17: B в 10:30 (окно до 10:15), C в 11:13.
    assert [
        (late.request_id, late.planned_start, late.forecast_start, late.late_min)
        for late in forecast.late_without_replan
    ] == [("B", 599, 630, 15)]


def test_forecast_overtime_of_extended_visit_without_remaining_visits():
    ctx = context()
    evening = [eng("E1", shift=("09:00", "17:00")), eng("E2", shift=("09:00", "17:00"))]
    session = new_session(
        ctx=ctx, requests=transit_requests()[:1], engineers=evening, workload_level=EXACT_TRAVEL_LEVEL
    )
    busy = busy_engineer(session.plan)
    assert visit_times(session.plan, busy) == [("A", 556, 556, 586)]

    forecast = apply_event(session, delay(busy, 480, time=ON_SITE_A), ctx).last_diff.delay_forecast

    assert forecast.late_without_replan == []
    assert forecast.overtime_without_replan_min == 586 + 480 - 1020  # A до 17:46 при смене до 17:00


def test_other_events_have_no_delay_forecast():
    ctx = context()
    updated = apply_event(
        new_session(ctx=ctx), Event(type=EventType.CANCEL, time="13:00", request_id="R2"), ctx
    )
    assert updated.last_diff.delay_forecast is None
    assert updated.last_diff.model_dump(mode="json")["delay_forecast"] is None


def test_repeated_delays_on_site_accumulate():
    ctx = context()
    session = transit_session(ctx)
    busy = busy_engineer(session.plan)

    once = apply_event(session, delay(busy, 30, time=ON_SITE_A), ctx)
    twice = apply_event(once, delay(busy, 30, time="09:40"), ctx)

    first = route_of(twice.plan, busy).visits[0]
    assert (first.request_id, first.end, first.pinned) == ("A", 586 + 60, True)
    assert [applied.event.delay_min for applied in twice.events] == [30, 30]


def test_delay_before_departure_survives_the_next_event():
    ctx = context()
    session = new_session(ctx=ctx)
    busy = busy_engineer(session.plan)
    delayed = apply_event(session, delay(busy, 45, time="09:00"), ctx)

    updated = apply_event(delayed, Event(type=EventType.CANCEL, time="09:10", request_id="R3"), ctx)

    assert updated.problem.state(busy).available_from == 585
    engineer, visit = visit_of(updated.plan, "R1")
    assert (engineer, visit.arrival) == (busy, 585 + visit.leg_min)


def test_delay_rejections_are_russian():
    ctx = context()
    session = new_session(ctx=ctx)
    unavailable = apply_event(
        session, Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id="E2"), ctx
    )
    cases = [
        (session, delay("E9", 30), "Инженер E9 не найден."),
        (
            unavailable,
            delay("E2", 30, time="13:30"),
            "Инженер E2 недоступен с 13:00, задержку поставить нельзя.",
        ),
        (
            unavailable,
            delay("E1", 30, time="12:00"),
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


def test_check_event_keeps_delay_without_replanning():
    ctx = context()
    session = new_session(ctx=ctx)
    stored = check_event(session, delay("E1", 40), ctx)
    assert (stored.type, stored.engineer_id, stored.delay_min) == (EventType.ENGINEER_DELAYED, "E1", 40)
    assert session.version == 1 and session.events == []
