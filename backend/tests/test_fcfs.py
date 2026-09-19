from app.domain.enums import ReasonCode, Skill, Transport
from app.solvers.eligibility import Exclusion, exclusion
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


def test_request_fixed_by_the_dispatcher_goes_only_to_its_brigade():
    # Без закрепления R2 досталась бы первому инженеру: E1 успевает к обеим заявкам.
    fixed = req("R2", 1, 0.1, "10:00", "12:00").model_copy(update={"fixed_engineer_id": "E2"})
    problem = problem_of(
        [req("R1", 1, 0, "10:00", "12:00"), fixed], [eng("E1"), eng("E2", skills=[Skill.LOCAL])]
    )
    assert exclusion(fixed, problem.state("E1")) == Exclusion.FIXED_TO_OTHER
    assert exclusion(fixed, problem.state("E2")) is None
    assert (
        exclusion(fixed.model_copy(update={"skill": Skill.EMERGENCY}), problem.state("E2"))
        == Exclusion.NO_SKILL
    )

    plan = FcfsSolver().solve(problem)

    assert {route.engineer_id: [v.request_id for v in route.visits] for route in plan.routes} == {
        "E1": ["R1"],
        "E2": ["R2"],
    }


def test_does_not_send_a_bicycle_beyond_the_leg_limit():
    problem = problem_of(
        [req("R1", 20, 0, "10:00", "12:00")], [eng("E1", transport=Transport.BIKE), eng("E2")]
    )
    plan = FcfsSolver().solve(problem)
    assert {route.engineer_id: [v.request_id for v in route.visits] for route in plan.routes} == {
        "E1": [],
        "E2": ["R1"],
    }
    assert plan.metrics.violations == 0
