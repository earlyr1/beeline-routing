"""Срочная заявка «как можно скорее»: окно от времени события до конца смен, ожидание до 4 часов без штрафа."""

import re

import pytest
from pydantic import ValidationError

from app.domain.enums import EventType, Priority, ReasonCode
from app.domain.models import ASAP_FREE_WAIT_MIN, Bundle, Event, Request
from app.domain.timeutil import fmt_hhmm
from app.planning.explain import build_explanation
from app.planning.session import EventRejected, apply_event, check_event
from app.solvers.assemble import build_plan
from app.solvers.ortools_solver import ObjectiveWeights, OrToolsSolver
from app.solvers.reasons import unassigned_reason
from app.solvers.simulate import simulate_route
from tests.api_helpers import make_client, sample_bundle, upload
from tests.helpers import at, eng, problem_of, req
from tests.planning_helpers import OFFICE, context, new_session, routes

WINDOW_TEXT = re.compile(r"окн\w* \d{2}:\d{2}–\d{2}:\d{2}")


def asap(request_id, x_km, y_km, window_start="09:00", window_end="18:00", **extra):
    request = req(request_id, x_km, y_km, window_start, window_end, priority=Priority.URGENT, **extra)
    return request.model_copy(update={"asap": True})


def engineer_at(engineer_id, x_km, y_km, **extra):
    lat, lon = at(x_km, y_km)
    return eng(engineer_id, **extra).model_copy(update={"start_lat": lat, "start_lon": lon})


def urgent(request, time):
    return Event(type=EventType.URGENT, time=time, request=request)


def update(request, time):
    return Event(type=EventType.REQUEST_UPDATED, time=time, request_id=request.id, request=request)


def edited(session, request_id, **changes):
    return Request.model_validate({**session.request(request_id).model_dump(), **changes})


def visit_of(plan, request_id):
    return next(
        (route.engineer_id, visit)
        for route in plan.routes
        for visit in route.visits
        if visit.request_id == request_id
    )


def window_of(request):
    return request.asap, request.window_start, request.window_end


# Модель и API


def test_request_asap_defaults_to_false_and_round_trips_through_state_json(tmp_path):
    assert req("R1", 1, 0, "10:00", "12:00").asap is False
    assert (
        Request.model_validate(req("R1", 1, 0, "10:00", "12:00").model_dump(exclude={"asap"})).asap is False
    )
    bundle = sample_bundle()
    bundle.requests[2] = bundle.requests[2].model_copy(update={"asap": True, "priority": Priority.URGENT})
    client, _ = make_client(tmp_path, bundle=bundle)
    base = f"/api/datasets/{upload(client, 'bundle.json', bundle.model_dump_json().encode())}"

    state = client.get(f"{base}/state").json()
    assert {request["id"]: request["asap"] for request in state["requests"]} == {
        "R1": False,
        "R2": False,
        "R3": True,
    }
    assert [Request.model_validate(request) for request in state["requests"]] == bundle.requests

    lat, lon = at(1, 1)
    sent = {
        "id": "U1",
        "address": "Город Москва, ул.Таганская, д. 1",
        "lat": lat,
        "lon": lon,
        "duration_min": 30,
        "window_start": "08:00",
        "window_end": "07:00",
        "skill": "local",
        "asap": True,
    }
    response = client.post(f"{base}/events", json={"type": "urgent", "time": "13:00", "request": sent})
    assert response.status_code == 200, response.text
    stored = next(request for request in response.json()["requests"] if request["id"] == "U1")
    assert (stored["asap"], stored["priority"], stored["window_start"], stored["window_end"]) == (
        True,
        "urgent",
        "13:00",
        "18:00",
    )
    assert response.json()["events"][0]["event"]["request"] == stored


def test_bundle_rejects_asap_requests_with_inverted_window():
    # Окно из события backend заменяет сам, а заявку из бандла солвер берёт как есть.
    base = req("U1", 1, 0, "10:00", "12:00").model_dump()
    inverted = Request.model_validate({**base, "asap": True, "window_start": "12:00", "window_end": "10:00"})
    fine = Request.model_validate({**base, "id": "U2", "asap": True})
    with pytest.raises(ValidationError) as error:
        Bundle(region="t", office=OFFICE, requests=[inverted, fine], engineers=[eng("E1")])
    assert "конец временного окна раньше начала у заявок: U1" in str(error.value)


# Срочная заявка «как можно скорее»


def test_urgent_asap_window_runs_from_event_time_to_latest_shift_end_of_available_engineers():
    ctx = context()
    engineers = [
        eng("E1"),
        eng("E2", shift=("13:00", "21:00")),
        # Недоступен с 12:00: во время события уже не работает, его смена до 23:00 не считается.
        eng("E3", shift=("09:00", "23:00"), available=False, unavailable_from="12:00"),
    ]
    session = new_session(ctx=ctx, engineers=engineers)
    event = urgent(asap("U1", 1, 1, "08:00", "09:00"), "13:00")

    updated = apply_event(session, event, ctx)

    stored = updated.request("U1")
    assert (stored.asap, stored.priority, stored.window_start, stored.window_end) == (
        True,
        Priority.URGENT,
        780,
        1260,
    )
    assert updated.events[0].event.request == stored
    assert check_event(session, event, ctx).request == stored
    _, visit = visit_of(updated.plan, "U1")
    assert 780 <= visit.start <= 780 + ASAP_FREE_WAIT_MIN

    # Инженер, недоступный только с более позднего времени, во время события ещё работает.
    later = [*engineers, eng("E4", shift=("09:00", "23:30"), available=False, unavailable_from="22:00")]
    cut = check_event(new_session(ctx=ctx, engineers=later), event, ctx).request
    assert (cut.window_start, cut.window_end) == (780, 1320)


def test_urgent_without_asap_keeps_the_sent_window():
    ctx = context()
    stored = check_event(
        new_session(ctx=ctx), urgent(req("U1", 1, 1, "13:00", "15:00"), "13:00"), ctx
    ).request
    assert window_of(stored) == (False, 780, 900)


def test_urgent_asap_after_all_shifts_gets_empty_window_and_does_not_fit_today():
    ctx = context()
    session = new_session(ctx=ctx, engineers=[eng("E1")])

    updated = apply_event(session, urgent(asap("U1", 1, 0, "10:00", "12:00"), "18:30"), ctx)

    assert window_of(updated.request("U1")) == (True, 1110, 1110)
    [item] = [item for item in updated.plan.unassigned if item.request_id == "U1"]
    visit = simulate_route(updated.problem, updated.problem.state("E1"), ["U1"]).visits[0]
    assert item.reason_code == ReasonCode.DOES_NOT_FIT
    assert item.reason_text == (
        f"Сегодня не успеть: даже без других заявок Инженер E1 начнёт не раньше {fmt_hhmm(visit.start)} "
        f"и закончит в {fmt_hhmm(visit.end)} (смена до 18:00)."
    )
    assert sorted(rid for sequence in routes(updated.plan).values() for rid in sequence) == ["R1", "R2", "R3"]


# Изменение заявки


def test_request_update_toggles_asap_and_keeps_the_waiting_clock():
    ctx = context()
    session = new_session(ctx=ctx)
    before = session.request("R2")

    on = apply_event(
        session,
        update(
            edited(session, "R2", priority="urgent", asap=True, window_start="20:00", window_end="21:00"),
            "08:00",
        ),
        ctx,
    )
    assert window_of(on.request("R2")) == (True, 480, 1080)
    assert on.request("R2").priority == Priority.URGENT
    assert on.events[-1].event.previous_request == before and before.asap is False

    kept = apply_event(
        on, update(edited(on, "R2", duration_min=45, window_start="15:00", window_end="16:00"), "08:10"), ctx
    )
    assert window_of(kept.request("R2")) == (True, 480, 1080)
    assert kept.request("R2").duration_min == 45
    assert kept.events[-1].event.previous_request.asap is True

    with pytest.raises(EventRejected) as same:
        apply_event(kept, update(edited(kept, "R2", window_start="16:00", window_end="17:00"), "08:20"), ctx)
    # Заявка стала срочной, поэтому в сообщении она подписана «URG-».
    assert str(same.value) == "В заявке URG-R2 ничего не изменилось."

    # Снять «как можно скорее» без других изменений: это изменение, окно берётся из запроса.
    off = apply_event(kept, update(edited(kept, "R2", asap=False), "08:20"), ctx)
    assert window_of(off.request("R2")) == (False, 480, 1080)
    assert off.events[-1].event.previous_request.asap is True

    again = apply_event(off, update(edited(off, "R2", asap=True), "08:30"), ctx)
    assert window_of(again.request("R2")) == (True, 510, 1080)

    moved = apply_event(
        again, update(edited(again, "R2", asap=False, window_start="15:00", window_end="17:00"), "08:40"), ctx
    )
    assert window_of(moved.request("R2")) == (False, 900, 1020)
    _, visit = visit_of(moved.plan, "R2")
    assert 900 <= visit.start <= 1020


def test_request_update_to_asap_ignores_a_past_or_inverted_sent_window():
    ctx = context()
    session = new_session(ctx=ctx)
    sent = edited(session, "R3", priority="urgent", asap=True, window_start="11:00", window_end="10:00")
    stored = check_event(session, update(sent, "13:00"), ctx).request
    assert window_of(stored) == (True, 780, 1080)


# Солвер


def test_asap_objective_weight_is_light():
    assert ObjectiveWeights().asap_late_per_min == 200


def test_waiting_under_four_hours_is_free_so_the_closer_engineer_wins():
    problem = problem_of(
        [asap("U1", 10, 0)], [eng("E1"), engineer_at("E2", 10, 0.5, shift=("12:00", "18:00"))]
    )

    plan = OrToolsSolver(time_limit_s=1).solve(problem)

    assert routes(plan) == {"E1": [], "E2": ["U1"]}
    _, visit = visit_of(plan, "U1")
    earlier = simulate_route(problem, problem.state("E1"), ["U1"]).visits[0]
    assert earlier.start < visit.start and earlier.leg_km > visit.leg_km + 10
    assert visit.start - problem.request("U1").window_start < ASAP_FREE_WAIT_MIN


def test_two_hours_past_the_free_wait_cost_more_than_ten_km():
    # Заявка с 09:00, без штрафа до 13:00. E1 успевает вовремя, E2 ближе на 10 км, но начинает только в 15:00.
    requests = [asap("U1", 0, 5)]
    engineers = [engineer_at("E1", 7.7, 5), engineer_at("E2", 0, 5, shift=("15:00", "18:00"))]
    problem = problem_of(requests, engineers)
    node = problem.request_node("U1")
    far = problem.travel_km(problem.home_node("E1"), node, engineers[0])
    near = problem.travel_km(problem.home_node("E2"), node, engineers[1])
    assert 9.5 < far - near < 10.5

    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert routes(plan) == {"E1": ["U1"], "E2": []}
    assert visit_of(plan, "U1")[1].start <= 540 + ASAP_FREE_WAIT_MIN

    unpenalized = OrToolsSolver(time_limit_s=1, weights=ObjectiveWeights(asap_late_per_min=0)).solve(problem)
    assert routes(unpenalized) == {"E1": [], "E2": ["U1"]}
    assert visit_of(unpenalized, "U1")[1].start == 900

    # У обычной заявки признак не добавляет штрафа: солвер видит только окно.
    normal = [requests[0].model_copy(update={"priority": Priority.NORMAL})]
    assert routes(OrToolsSolver(time_limit_s=1).solve(problem_of(normal, engineers))) == {
        "E1": [],
        "E2": ["U1"],
    }


def test_asap_request_nobody_can_serve_today_stays_unassigned_without_breaking_others():
    problem = problem_of(
        [
            req("R1", 1, 0, "10:00", "12:00"),
            asap("U1", 1, 0.2, "17:50", "18:00", duration=60),
            req("R2", 1.2, 0, "14:00", "16:00"),
        ],
        [eng("E1"), eng("E2")],
    )

    plan = OrToolsSolver(time_limit_s=1).solve(problem)

    assert sorted(rid for sequence in routes(plan).values() for rid in sequence) == ["R1", "R2"]
    assert (plan.metrics.engineers_used, plan.metrics.violations) == (1, 0)
    [item] = plan.unassigned
    assert (item.request_id, item.reason_code) == ("U1", ReasonCode.DOES_NOT_FIT)
    assert item.reason_text == (
        "Сегодня не успеть: даже без других заявок Инженер E1 начнёт не раньше 17:50 и закончит в 18:50 "
        "(смена до 18:00)."
    )


# Причины и объяснения


def test_asap_reasons_speak_about_today():
    busy = [asap("U1", 1, 0, "10:00", "10:10"), req("R1", 1, 0, "10:00", "10:10", duration=120)]
    problem = problem_of(busy, [eng("E1")])
    reason = unassigned_reason(problem, "U1", {"E1": ["R1"]})
    assert reason.reason_code == ReasonCode.NO_FREE_ENGINEER
    assert reason.reason_text == (
        "Сегодня нет свободных исполнителей: подходящие инженеры (1) заняты другими заявками. "
        "Раньше всех освобождается Инженер E1 в 12:00."
    )
    unavailable = problem_of([asap("U1", 1, 0)], [eng("E1", available=False)])
    assert (
        unassigned_reason(unavailable, "U1", {}).reason_text
        == "Все подходящие инженеры недоступны: Инженер E1."
    )


def _asap_check(explanation):
    assert [check.name for check in explanation.constraints] == [
        "Навык",
        "Транспорт",
        "Как можно скорее",
        "Смена",
    ]
    return explanation.constraints[2]


def test_assigned_asap_check_counts_waiting_from_creation():
    on_edge = problem_of([asap("U1", 1, 0)], [engineer_at("E1", 1, 0, shift=("13:00", "18:00"))])
    plan = build_plan(on_edge, "ortools", {"E1": ["U1"]})
    check = _asap_check(build_explanation(on_edge, plan, on_edge.request("U1")))
    assert (check.ok, check.detail) == (True, "Создана в 09:00, начало 13:00, ожидание 240 мин")

    late = problem_of([asap("U1", 1, 0)], [eng("E1", shift=("14:00", "18:00"))])
    plan = build_plan(late, "ortools", {"E1": ["U1"]})
    explanation = build_explanation(late, plan, late.request("U1"))
    check = _asap_check(explanation)
    start = explanation.visit.start
    assert start > 840
    assert (check.ok, check.detail) == (
        False,
        f"Создана в 09:00, начало {fmt_hhmm(start)}, ожидание {start - 540} мин, дольше 4 часов",
    )


def test_asap_summary_and_factors_do_not_print_the_window():
    problem = problem_of([asap("U1", 1, 0), req("R1", 1.1, 0, "10:00", "12:00")], [eng("E1"), eng("E2")])
    plan = build_plan(problem, "ortools", {"E1": ["R1", "U1"]})

    explanation = build_explanation(problem, plan, problem.request("U1"))

    assert explanation.status == "assigned"
    assert "(как можно скорее с 09:00)" in explanation.summary
    assert not WINDOW_TEXT.search(explanation.summary)
    assert not any(WINDOW_TEXT.search(factor) for factor in explanation.factors)
    assert any("как можно скорее с 09:00" in factor for factor in explanation.factors)
    normal = build_explanation(problem, plan, problem.request("R1"))
    assert "в окне 10:00–12:00" in normal.summary


def test_unassigned_asap_explanation_says_whether_anyone_makes_it_today():
    late = problem_of([asap("U1", 1, 0, "17:50", "18:00", duration=60)], [eng("E1")])
    explanation = build_explanation(late, build_plan(late, "ortools", {"E1": []}), late.request("U1"))
    check = _asap_check(explanation)
    assert (check.ok, check.detail) == (
        False,
        "Сегодня никто из подходящих инженеров не успевает до конца смен в 18:00",
    )
    assert explanation.summary.startswith("Сегодня не успеть: даже без других заявок Инженер E1")

    fits = problem_of([asap("U1", 1, 0)], [eng("E1")])
    explanation = build_explanation(fits, build_plan(fits, "ortools", {"E1": []}), fits.request("U1"))
    check = _asap_check(explanation)
    assert (check.ok, check.detail) == (True, "Хотя бы один подходящий инженер успевает сегодня")
    assert explanation.summary == (
        "Сегодня нет свободных исполнителей: подходящие инженеры (1) заняты другими заявками. "
        "Раньше всех освобождается Инженер E1 в 09:00."
    )
    assert not WINDOW_TEXT.search(explanation.summary)


def test_restore_after_the_day_ended_speaks_about_asap():
    ctx = context()
    session = apply_event(new_session(ctx=ctx), urgent(asap("U1", 1, 1), "13:00"), ctx)
    cancelled = apply_event(session, Event(type=EventType.CANCEL, time="13:01", request_id="U1"), ctx)
    with pytest.raises(EventRejected) as rejected:
        apply_event(cancelled, Event(type=EventType.RESTORE, time="18:30", request_id="U1"), ctx)
    assert str(rejected.value) == (
        "Заявка URG-U1 как можно скорее с 13:00: смены закончились в 18:00, вернуть её в план нельзя."
    )
