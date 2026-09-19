"""Обед по плану: 45 минут между визитами, начало через 3–6 часов после начала смены."""

from dataclasses import replace

from app.domain.enums import EventType, ReasonCode
from app.domain.models import LUNCH_MIN, Event, Lunch, Visit
from app.domain.timeutil import fmt_hhmm
from app.planning.explain import build_explanation
from app.planning.session import apply_event
from app.solvers.assemble import build_plan
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import OrToolsSolver
from app.solvers.problem import EngineerState
from app.solvers.simulate import simulate_route
from tests.api_helpers import make_client, sample_bundle, upload
from tests.helpers import eng, problem_of, req
from tests.planning_helpers import (
    EXACT_TRAVEL_LEVEL,
    busy_engineer,
    context,
    new_session,
    other_engineer,
    routes,
)


def route_of(plan, engineer_id):
    return next(route for route in plan.routes if route.engineer_id == engineer_id)


def visit_of(plan, request_id):
    return next(visit for route in plan.routes for visit in route.visits if visit.request_id == request_id)


def assert_lunch_between_visits(problem, plan):
    """У инженера с визитами обед в окне, работа и дорога его не пересекают; у инженера без визитов обеда нет."""
    for route in plan.routes:
        if not route.visits:
            assert route.lunch is None, route.engineer_id
            continue
        state = problem.state(route.engineer_id)
        lunch = route.lunch
        assert lunch is not None, (plan.solver, route.engineer_id)
        shift_start = state.engineer.shift_start
        assert shift_start + 180 <= lunch.start <= shift_start + 360
        # Начатый обед остаётся и у инженера, который после этого стал недоступен.
        kept = problem.pinned_lunch.get(route.engineer_id) == lunch
        assert lunch.end == lunch.start + LUNCH_MIN
        assert lunch.end <= (state.engineer.shift_end if kept else state.available_until)
        for visit in route.visits:
            assert visit.end <= lunch.start or visit.start >= lunch.end, (plan.solver, visit)
            departure = visit.arrival - visit.leg_min
            assert visit.arrival <= lunch.start or departure >= lunch.end, (plan.solver, visit)


# Прогон маршрута


def test_lunch_takes_a_waiting_gap_without_delaying_visits():
    # Смена 09:00–18:00: обед начинается с 12:00 до 15:00. После R1 до R2 в 13:00 больше двух часов ожидания.
    problem = problem_of(
        [req("R1", 1, 0, "10:00", "11:00"), req("R2", 1.2, 0, "13:00", "14:00")], [eng("E1")]
    )
    sim = simulate_route(problem, problem.states[0], ["R1", "R2"])
    assert sim.feasible and not sim.lunch_conflict
    assert sim.lunch == Lunch(start="12:00", end="12:45")
    first, second = sim.visits
    assert (first.start, first.end) == (600, 630)
    # Обед у объекта R1, к R2 инженер едет после обеда и всё равно успевает к 13:00.
    assert (second.arrival, second.start, second.end) == (765 + second.leg_min, 780, 810)


def test_lunch_without_a_gap_pushes_later_visits():
    # R1 11:00–13:00, дальше R2 сразу: обед с 13:00 у R1, к R2 инженер едет после обеда.
    problem = problem_of(
        [
            req("R1", 1, 0, "11:00", "11:10", duration=120),
            req("R2", 1.2, 0, "13:00", "15:00", duration=120),
        ],
        [eng("E1")],
    )
    sim = simulate_route(problem, problem.states[0], ["R1", "R2"])
    assert sim.feasible
    assert sim.lunch == Lunch(start="13:00", end="13:45")
    second = sim.visits[1]
    assert (second.arrival, second.start) == (825 + second.leg_min, 825 + second.leg_min)
    assert second.late_min == 0


def test_route_is_infeasible_when_no_lunch_placement_fits():
    # Работа 09:30–16:30 без перерыва: до неё обед не начать, после неё окно обеда уже прошло.
    problem = problem_of([req("R1", 1, 0, "09:30", "09:40", duration=420)], [eng("E1")])
    sim = simulate_route(problem, problem.states[0], ["R1"])
    assert not sim.feasible and sim.lunch_conflict
    assert any("обед" in violation for violation in sim.violations)
    for plan in (FcfsSolver().solve(problem), OrToolsSolver(time_limit_s=1).solve(problem)):
        assert routes(plan) == {"E1": []}
        [lost] = plan.unassigned
        assert lost.reason_code == ReasonCode.DOES_NOT_FIT
        assert "с учётом обеда" in lost.reason_text, lost.reason_text


def test_reason_without_lunch_conflict_does_not_mention_lunch():
    problem = problem_of([req("R1", 40, 0, "09:00", "09:30")], [eng("E1")])
    [lost] = FcfsSolver().solve(problem).unassigned
    assert lost.reason_code == ReasonCode.DOES_NOT_FIT and "обед" not in lost.reason_text


def test_workday_shorter_than_six_hours_has_no_lunch():
    requests = [req("R1", 1, 0, "10:00", "11:00")]
    short = problem_of(requests, [eng("E1", shift=("09:00", "14:59"))])
    six_hours = problem_of(requests, [eng("E1", shift=("09:00", "15:00"))])
    assert simulate_route(short, short.states[0], ["R1"]).lunch is None
    assert simulate_route(six_hours, six_hours.states[0], ["R1"]).lunch == Lunch(start="12:00", end="12:45")
    assert build_plan(short, "fcfs", {"E1": ["R1"]}).routes[0].lunch is None


def test_engineer_without_visits_has_no_lunch():
    problem = problem_of([req("R1", 1, 0, "10:00", "11:00")], [eng("E1"), eng("E2")])
    plan = build_plan(problem, "fcfs", {"E1": ["R1"], "E2": []})
    assert route_of(plan, "E1").lunch == Lunch(start="12:00", end="12:45")
    assert route_of(plan, "E2").lunch is None
    assert simulate_route(problem, problem.states[1], []).lunch is None


def test_engineer_who_already_worked_today_gets_lunch_after_pinned_visits():
    base = problem_of([req("P", 1, 0, "11:00", "12:00")], [eng("E1")])
    [state] = base.states
    pinned = Visit(request_id="P", arrival=544, start=660, end=750, leg_km=1.3, leg_min=4, pinned=True)
    problem = replace(
        base,
        states=[
            EngineerState(
                state.engineer, base.request_node("P"), 750, state.available_until, state.equipment_left
            )
        ],
        open_request_ids=[],
        pinned={"E1": [pinned]},
        now=740,
    )
    assert simulate_route(problem, problem.states[0], []).lunch == Lunch(start="12:30", end="13:15")
    assert build_plan(problem, "ortools", {"E1": []}).routes[0].lunch == Lunch(start="12:30", end="13:15")


def test_plan_without_lunch_keeps_times_as_given():
    """План диспетчеров показывается как есть: без обеда и без сдвига визитов обедом."""
    problem = problem_of(
        [
            req("R1", 1, 0, "11:00", "11:10", duration=120),
            req("R2", 1.2, 0, "13:00", "15:00", duration=120),
        ],
        [eng("E1")],
    )
    plan = build_plan(problem, "dispatchers", {"E1": ["R1", "R2"]}, lunch=False)
    assert plan.routes[0].lunch is None
    assert visit_of(plan, "R2").start == 780 + visit_of(plan, "R2").leg_min
    assert plan.violations == []


# Солверы


def lined_day():
    """Восемь визитов подряд с короткими переездами: обед помещается только со сдвигом части визитов."""
    return [
        req(f"R{k}", 0.5 * k, 0, fmt_hhmm(540 + 50 * k), fmt_hhmm(600 + 50 * k), duration=40)
        for k in range(1, 9)
    ]


def test_fcfs_and_ortools_plans_both_have_lunch_between_visits():
    problem = problem_of(lined_day(), [eng("E1"), eng("E2")])
    for plan in (FcfsSolver().solve(problem), OrToolsSolver(time_limit_s=1).solve(problem)):
        assert plan.unassigned == [] and plan.violations == [], plan.solver
        assert any(route.lunch for route in plan.routes)
        assert_lunch_between_visits(problem, plan)


def test_ortools_model_itself_keeps_room_for_lunch():
    # Одному инженеру обе заявки без обеда успеть можно (11:00–13:00 и 13:01–15:31), с обедом нельзя.
    problem = problem_of(
        [
            req("R1", 1, 0, "11:00", "11:10", duration=120),
            req("R2", 1, 0.1, "13:00", "13:10", duration=150),
        ],
        [eng("E1"), eng("E2")],
    )
    solver = OrToolsSolver(time_limit_s=1)
    solved = solver._solve_model(problem, problem.states, ["R1", "R2"])
    assert sorted(len(sequence) for sequence in solved) == [1, 1]
    plan = solver.solve(problem)
    assert plan.metrics.engineers_used == 2 and plan.unassigned == []
    assert_lunch_between_visits(problem, plan)


# Перепланирование


def after_visit_lunch_session(ctx):
    """R1 11:30–12:15, обед сразу после неё 12:15–13:00 у объекта R1, R2 в 14:00, R3 в 15:00."""
    requests = [
        req("R1", 1, 0, "11:30", "11:40", duration=45),
        req("R2", 1.2, 0, "14:00", "16:00"),
        req("R3", -1, 0, "15:00", "17:00"),
    ]
    session = new_session(ctx=ctx, requests=requests, workload_level=EXACT_TRAVEL_LEVEL)
    busy = busy_engineer(session.plan)
    assert routes(session.plan)[busy] == ["R1", "R2", "R3"]
    assert route_of(session.plan, busy).lunch == Lunch(start="12:15", end="13:00")
    return session, busy


def test_replan_keeps_started_lunch_and_releases_engineer_at_its_end():
    ctx = context()
    session, busy = after_visit_lunch_session(ctx)

    updated = apply_event(session, Event(type=EventType.CANCEL, time="12:30", request_id="R3"), ctx)

    lunch = Lunch(start="12:15", end="13:00")
    assert route_of(updated.plan, busy).lunch == lunch
    assert route_of(updated.baseline, busy).lunch == lunch
    assert updated.problem.pinned_lunch == {busy: lunch}
    state = updated.problem.state(busy)
    assert (state.available_from, state.start_node) == (780, updated.problem.request_node("R1"))
    assert routes(updated.plan)[busy] == ["R1", "R2"]
    r2 = visit_of(updated.plan, "R2")
    assert (r2.arrival, r2.start) == (780 + r2.leg_min, 840)
    assert updated.plan.violations == []


def test_lunch_not_started_yet_is_planned_again():
    ctx = context()
    session, busy = after_visit_lunch_session(ctx)

    updated = apply_event(session, Event(type=EventType.CANCEL, time="12:10", request_id="R3"), ctx)

    assert updated.problem.pinned_lunch == {}
    assert route_of(updated.plan, busy).lunch == Lunch(start="12:15", end="13:00")


def test_engineer_who_becomes_unavailable_keeps_started_lunch():
    ctx = context()
    session, busy = after_visit_lunch_session(ctx)

    updated = apply_event(
        session, Event(type=EventType.ENGINEER_UNAVAILABLE, time="12:30", engineer_id=busy), ctx
    )

    assert routes(updated.plan)[busy] == ["R1"]
    assert route_of(updated.plan, busy).lunch == Lunch(start="12:15", end="13:00")
    other = route_of(updated.plan, other_engineer(busy))
    assert sorted(visit.request_id for visit in other.visits) == ["R2", "R3"]
    assert other.lunch is not None
    assert_lunch_between_visits(updated.problem, updated.plan)


def delay(engineer_id, delay_min, time):
    return Event(type=EventType.ENGINEER_DELAYED, time=time, engineer_id=engineer_id, delay_min=delay_min)


def test_lunch_is_not_required_once_its_window_has_passed():
    ctx = context()
    requests = [req("R1", 1, 0, "10:00", "10:10"), req("R2", 1.2, 0, "16:00", "17:00")]
    session = new_session(ctx=ctx, requests=requests, workload_level=EXACT_TRAVEL_LEVEL)
    busy = busy_engineer(session.plan)
    assert route_of(session.plan, busy).lunch == Lunch(start="12:00", end="12:45")

    # На объекте R1 задержка до 15:30: окно обеда (до 15:00) прошло, а обеда ещё не было.
    updated = apply_event(session, delay(busy, 300, "10:15"), ctx)

    assert updated.problem.state(busy).available_from == 930
    assert routes(updated.plan)[busy] == ["R1", "R2"]
    assert route_of(updated.plan, busy).lunch is None
    assert updated.plan.unassigned == [] and updated.plan.violations == []


def test_delay_forecast_without_replan_includes_lunch():
    ctx = context()
    requests = [
        req("R1", 1, 0, "11:30", "11:40", duration=60),
        req("R2", 1, 0.1, "13:20", "13:40", duration=120),
    ]
    session = new_session(
        ctx=ctx, requests=requests, engineers=[eng("E1")], workload_level=EXACT_TRAVEL_LEVEL
    )
    assert routes(session.plan) == {"E1": ["R1", "R2"]}
    assert route_of(session.plan, "E1").lunch == Lunch(start="12:30", end="13:15")

    # R1 закончится в 13:00. Без обеда R2 успела бы в 13:01, с обедом 13:00–13:45 только в 13:46 при окне до 13:40.
    forecast = apply_event(session, delay("E1", 30, "12:00"), ctx).last_diff.delay_forecast

    assert [
        (late.request_id, late.planned_start, late.forecast_start, late.late_min)
        for late in forecast.late_without_replan
    ] == [("R2", 800, 826, 6)]


# Тексты и API


def test_explanation_mentions_engineer_lunch():
    ctx = context()
    requests = [req("R1", 1, 0, "10:00", "11:00"), req("R2", 1.2, 0, "13:00", "14:00")]
    session = new_session(
        ctx=ctx, requests=requests, engineers=[eng("E1")], workload_level=EXACT_TRAVEL_LEVEL
    )
    explanation = build_explanation(session.problem, session.plan, session.request("R1"))
    assert "Обед 12:00–12:45." in explanation.factors


def test_state_json_has_route_lunch(tmp_path):
    client, _ = make_client(tmp_path)
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    state = client.get(f"/api/datasets/{dataset_id}/state").json()
    for plan in (state["plan"], state["baseline"]):
        lunches = {route["engineer_id"]: route["lunch"] for route in plan["routes"]}
        busy = next(route["engineer_id"] for route in plan["routes"] if route["visits"])
        assert lunches[busy] == {"start": "12:00", "end": "12:45"}
        assert [lunch for engineer_id, lunch in lunches.items() if engineer_id != busy] == [None]
