from app.domain.enums import ReasonCode, Skill, Transport
from app.domain.models import Request
from app.geo.matrix import TrafficProfile, TravelModel
from app.solvers.problem import make_problem
from app.solvers.simulate import simulate_route
from tests.helpers import eng, problem_of, req


def test_waits_for_window_and_computes_times():
    problem = problem_of([req("R1", 1, 0, "10:00", "12:00", duration=30)], [eng("E1")])
    sim = simulate_route(problem, problem.states[0], ["R1"])
    visit = sim.visits[0]
    assert sim.feasible
    assert visit.leg_min == 4  # 1 км * 1.3 / 25 км/ч = 3.12 мин -> 4
    assert (visit.arrival, visit.start, visit.end) == (9 * 60 + 4, 10 * 60, 10 * 60 + 30)
    assert visit.leg_km == 1.3


def test_reports_each_violated_constraint():
    problem = problem_of(
        [req("R1", 1, 0, "09:00", "09:01", skill=Skill.EMERGENCY, transport=Transport.CAR, duration=600)],
        [eng("E1", skills=[Skill.LOCAL], transport=Transport.FOOT)],
    )
    sim = simulate_route(problem, problem.states[0], ["R1"])
    text = " | ".join(sim.violations)
    assert not sim.feasible
    assert "нет навыка «Аварийные работы»" in text
    assert "нужен транспорт «Автомобиль»" in text
    assert "позже окна" in text
    assert "позже конца смены" in text


def test_unavailable_engineer_state_is_inactive():
    problem = problem_of(
        [], [eng("E1", available=False, unavailable_from="13:00"), eng("E2", available=False)]
    )
    assert problem.states[0].available_until == 13 * 60 and problem.states[0].active
    assert not problem.states[1].active


def test_requests_without_coordinates_become_unplannable():
    lost = Request(
        id="X", address="нигде", duration_min=30, window_start="10:00", window_end="12:00", skill=Skill.LOCAL
    )
    problem = make_problem([lost], [eng("E1")], model=TravelModel(), traffic=TrafficProfile({}))
    assert problem.open_request_ids == []
    assert problem.unplannable[0].reason_code == ReasonCode.ADDRESS_NOT_FOUND


def test_exclusion_checks_skill_then_transport_then_availability():
    from app.solvers.eligibility import Exclusion, exclusion

    request = req("R1", 1, 0, "10:00", "12:00", skill=Skill.CONNECTION, transport=Transport.CAR)
    problem = problem_of(
        [request],
        [
            eng("E1", skills=[Skill.LOCAL]),
            eng("E2", transport=Transport.BIKE),
            eng("E3", available=False),
            eng("E4"),
        ],
    )
    reasons = [exclusion(problem.request("R1"), state) for state in problem.states]
    assert reasons == [Exclusion.NO_SKILL, Exclusion.NO_TRANSPORT, Exclusion.UNAVAILABLE, None]
