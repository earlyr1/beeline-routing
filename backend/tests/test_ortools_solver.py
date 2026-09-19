from app.domain.enums import Priority, ReasonCode, Transport
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import OrToolsSolver, repair_unassigned
from tests.helpers import eng, problem_of, req


def _routes(plan):
    return {route.engineer_id: [v.request_id for v in route.visits] for route in plan.routes}


def test_resolves_tz_conflict_with_one_engineer():
    problem = problem_of(
        [req("R1", 1, 0, "14:00", "16:00"), req("R2", 1, 0.1, "10:00", "12:00")],
        [eng("E1"), eng("E2")],
    )
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert plan.metrics.engineers_used == 1
    assert max(_routes(plan).values(), key=len) == ["R2", "R1"]
    assert plan.metrics.violations == 0
    assert FcfsSolver().solve(problem).metrics.engineers_used == 2


def test_respects_transport_and_skill_constraints():
    problem = problem_of(
        [req("R1", 2, 0, "10:00", "12:00", transport=Transport.CAR), req("R2", 0.5, 0, "10:00", "12:00")],
        [eng("E1", transport=Transport.PUBLIC), eng("E2", transport=Transport.CAR)],
    )
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert "R1" in _routes(plan)["E2"]
    assert plan.metrics.violations == 0


def test_urgent_request_wins_when_only_one_fits():
    problem = problem_of(
        [
            req("N1", 1, 0, "10:00", "10:10", duration=60),
            req("U1", 1, 0.2, "10:00", "10:10", duration=60, priority=Priority.URGENT),
        ],
        [eng("E1")],
    )
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan)["E1"] == ["U1"]
    assert [(u.request_id, u.reason_code) for u in plan.unassigned] == [("N1", ReasonCode.NO_FREE_ENGINEER)]


def test_previous_assignment_breaks_ties():
    problem = problem_of([req("R1", 1, 0, "10:00", "12:00")], [eng("E1"), eng("E2")])
    problem.previous_assignment = {"R1": "E2"}
    problem.previous_order = {"E2": ["R1"]}
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan) == {"E1": [], "E2": ["R1"]}


def test_inactive_engineer_gets_no_work():
    problem = problem_of([req("R1", 1, 0, "10:00", "12:00")], [eng("E1", available=False), eng("E2")])
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan) == {"E1": [], "E2": ["R1"]}


def test_engineer_who_already_worked_today_has_no_fixed_cost():
    from dataclasses import replace

    from app.domain.models import Visit
    from app.solvers.problem import EngineerState

    base = problem_of(
        [req("P", 4, 0, "09:00", "12:00"), req("R1", 0.5, 0, "10:00", "12:00")],
        [eng("E1"), eng("E2")],
    )
    e1, e2 = base.states
    pinned_visit = Visit(request_id="P", arrival=560, start=560, end=600, leg_km=5.2, leg_min=20, pinned=True)
    problem = replace(
        base,
        states=[EngineerState(e1.engineer, base.request_node("P"), 600, e1.available_until), e2],
        open_request_ids=["R1"],
        pinned={"E1": [pinned_visit]},
        now=600,
    )
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan) == {"E1": ["P", "R1"], "E2": []}
    assert plan.metrics.engineers_used == 1


def test_repair_inserts_left_out_request_preferring_working_engineer():
    problem = problem_of(
        [req("R1", 1, 0, "10:00", "12:00"), req("R2", 1.2, 0, "10:00", "12:00")], [eng("E1"), eng("E2")]
    )
    assert repair_unassigned(problem, {"E1": ["R1"], "E2": []}) == {"E1": ["R1", "R2"], "E2": []}


def test_repair_puts_urgent_first_when_slots_are_scarce():
    problem = problem_of(
        [
            req("N1", 1, 0, "10:00", "10:10", duration=60),
            req("U1", 1, 0.1, "10:00", "10:10", duration=60, priority=Priority.URGENT),
        ],
        [eng("E1")],
    )
    assert repair_unassigned(problem, {"E1": []}) == {"E1": ["U1"]}


# Форма Каширы: дальняя заявка, велобригада рядом с офисом и бригада на машине уже на месте. Дешёвый ответ —
# отдать обе заявки одному велосипедисту, и до предела плеча решатель так и делал.
FAR_REQUESTS = [req("R1", 60, 0, "10:00", "16:00"), req("R2", 1, 0, "09:30", "10:00")]
FAR_ENGINEERS = [eng("E1", transport=Transport.BIKE), eng("E2", start=(58, 0))]


def test_far_request_goes_to_the_car_brigade_at_the_distance():
    plan = OrToolsSolver(time_limit_s=1).solve(problem_of(FAR_REQUESTS, FAR_ENGINEERS))
    assert _routes(plan) == {"E1": ["R2"], "E2": ["R1"]}
    assert plan.metrics.violations == 0


def test_without_the_limit_the_cheap_answer_uses_the_long_leg():
    from app.geo.matrix import TravelModel

    problem = problem_of(FAR_REQUESTS, FAR_ENGINEERS, model=TravelModel(bike_leg_limit_km=1000))
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan) == {"E1": ["R2", "R1"], "E2": []}
