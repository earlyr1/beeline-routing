from app.domain.enums import Priority, ReasonCode, RequestTier, Transport
from app.domain.models import Request, dispatch_order
from app.solvers.assemble import build_plan
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import (
    ObjectiveWeights,
    OrToolsSolver,
    drop_penalty,
    repair_unassigned,
)
from app.solvers.portfolio import plan_cost
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
        states=[
            EngineerState(e1.engineer, base.request_node("P"), 600, e1.available_until, e1.equipment_left),
            e2,
        ],
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


# --- Три уровня приоритета распределения (ответ организаторов, вопрос 15) -------------------------------------


# День на одну заявку: четыре заявки в одно и то же узкое окно, одна бригада. Всё, что не первое, снимается.
def _tiered_day(*tiers):
    return problem_of(
        [
            req(
                f"R{k}",
                1,
                k / 10,
                "10:00",
                "10:10",
                duration=60,
                tier=tier,
                priority=Priority.URGENT if tier == RequestTier.EMERGENCY else Priority.NORMAL,
            )
            for k, tier in enumerate(tiers)
        ],
        [eng("E1")],
    )


def test_repair_drops_routine_before_connection_and_connection_before_emergency():
    problem = _tiered_day(RequestTier.ROUTINE, RequestTier.CONNECTION)
    assert repair_unassigned(problem, {"E1": []}) == {"E1": ["R1"]}
    problem = _tiered_day(RequestTier.CONNECTION, RequestTier.EMERGENCY)
    assert repair_unassigned(problem, {"E1": []}) == {"E1": ["R1"]}


def test_solver_keeps_the_connection_and_drops_the_repair():
    problem = _tiered_day(RequestTier.ROUTINE, RequestTier.CONNECTION)
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan)["E1"] == ["R1"]
    assert [u.request_id for u in plan.unassigned] == ["R0"]


def test_solver_keeps_the_emergency_and_drops_the_connection():
    problem = _tiered_day(RequestTier.CONNECTION, RequestTier.EMERGENCY)
    plan = OrToolsSolver(time_limit_s=1).solve(problem)
    assert _routes(plan)["E1"] == ["R1"]
    assert [u.request_id for u in plan.unassigned] == ["R0"]


def test_extra_order_does_not_outrank_a_connection():
    """«Дозаказ» и «Подключение» делят навык connection, но уровень у них разный."""
    problem = _tiered_day(RequestTier.ROUTINE, RequestTier.CONNECTION)
    extra, connection = problem.request("R0"), problem.request("R1")
    assert extra.skill == connection.skill
    weights = ObjectiveWeights()
    assert drop_penalty(extra, weights) < drop_penalty(connection, weights)


def test_drop_penalty_and_plan_cost_agree_on_the_tiers():
    """Стоимость снятой заявки у модели и у сравнения планов в портфеле — одна и та же функция."""
    problem = _tiered_day(RequestTier.ROUTINE, RequestTier.CONNECTION, RequestTier.EMERGENCY)
    weights = ObjectiveWeights()
    plan = build_plan(problem, "ortools", {"E1": []})
    expected = sum(drop_penalty(problem.request(f"R{k}"), weights) for k in range(3))
    assert plan_cost(problem, plan, weights) == expected
    penalties = [drop_penalty(problem.request(f"R{k}"), weights) for k in range(3)]
    assert penalties[0] < penalties[1] < penalties[2] < weights.drop_fixed


def test_old_bundles_without_the_tier_load_on_the_lowest_level():
    request = Request.model_validate_json(
        '{"id": "R1", "address": "Москва", "duration_min": 30, "window_start": "10:00", '
        '"window_end": "12:00", "skill": "local"}'
    )
    assert request.tier == RequestTier.ROUTINE
    assert dispatch_order(request) == 3


# --- Оборудование: утренний запас бригады (ответ организаторов, вопрос 4) --------------------------------------


def test_brigade_at_its_equipment_limit_gets_no_more_equipment_requests():
    problem = problem_of(
        [req(f"R{k}", 1, k / 10, "10:00", "16:00", duration=30, equipment=True) for k in range(4)],
        [eng("E1", equipment_stock=2), eng("E2", equipment_stock=0)],
    )
    plan = OrToolsSolver(time_limit_s=2).solve(problem)
    assert len(_routes(plan)["E1"]) == 2
    assert _routes(plan)["E2"] == []
    assert plan.metrics.violations == 0
    assert len(plan.unassigned) == 2


def test_equipment_stock_does_not_limit_requests_without_equipment():
    problem = problem_of(
        [req(f"R{k}", 1, k / 10, "10:00", "16:00", duration=30) for k in range(4)],
        [eng("E1", equipment_stock=0)],
    )
    plan = OrToolsSolver(time_limit_s=2).solve(problem)
    assert len(_routes(plan)["E1"]) == 4
