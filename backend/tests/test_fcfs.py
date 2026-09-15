from app.domain.enums import ReasonCode, Skill
from app.solvers.fcfs import FcfsSolver
from tests.helpers import eng, problem_of, req


def test_conflict_from_tz_sequential_assignment_uses_extra_engineer():
    # R1 поступила раньше, но её окно позже: после R1 инженер E1 уже не успевает к R2.
    problem = problem_of(
        [req("R1", 1, 0, "14:00", "16:00"), req("R2", 1, 0.1, "10:00", "12:00")],
        [eng("E1"), eng("E2")],
    )
    plan = FcfsSolver().solve(problem)
    routes = {route.engineer_id: [v.request_id for v in route.visits] for route in plan.routes}
    assert routes == {"E1": ["R1"], "E2": ["R2"]}
    assert plan.metrics.engineers_used == 2
    assert plan.metrics.violations == 0


def test_skips_engineers_without_skill_and_explains_unassigned():
    problem = problem_of(
        [
            req("R1", 1, 0, "10:00", "12:00", skill=Skill.CONNECTION),
            req("R2", 1, 0, "10:00", "12:00", skill=Skill.EMERGENCY),
        ],
        [eng("E1", skills=[Skill.LOCAL]), eng("E2", skills=[Skill.CONNECTION])],
    )
    plan = FcfsSolver().solve(problem)
    assert [v.request_id for v in plan.routes[1].visits] == ["R1"]
    assert [(u.request_id, u.reason_code) for u in plan.unassigned] == [("R2", ReasonCode.NO_SKILL)]
    assert plan.metrics.km_per_engineer == {"E2": 1.3}
