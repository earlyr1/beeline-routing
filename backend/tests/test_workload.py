"""Нагрузка инженеров: стоимость нового инженера для оптимизатора и запас времени на дорогу по уровню дня."""

from dataclasses import replace

import pytest

from app.domain.enums import EventType, ReasonCode
from app.domain.models import Event
from app.geo.matrix import TrafficProfile, TravelModel
from app.planning import session as session_module
from app.planning.session import apply_event
from app.planning.workload import (
    DEFAULT_WORKLOAD_LEVEL,
    WORKLOAD_LEVEL_TEXT,
    WORKLOAD_LEVELS,
    is_workload_level,
    travel_buffer,
    workload,
    workload_weights,
)
from app.solvers.assemble import build_plan
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import ObjectiveWeights, OrToolsSolver
from app.solvers.problem import NO_BUFFER, TravelBuffer, make_problem
from app.solvers.simulate import simulate_route
from tests.helpers import at, eng, problem_of, req
from tests.planning_helpers import EXACT_TRAVEL_LEVEL, context, new_session, routes


def buffered_problem(requests, engineers, level=DEFAULT_WORKLOAD_LEVEL):
    return make_problem(
        requests, engineers, model=TravelModel(), traffic=TrafficProfile({}), buffer=travel_buffer(level)
    )


def test_level_table_matches_contract():
    """Три уровня: это прежние уровни 0, 2 и 4 из пяти, с теми же стоимостью инженера и запасом на дорогу."""
    assert DEFAULT_WORKLOAD_LEVEL == 1
    table = [
        (item.title, item.emoji, item.vehicle_fixed_cost, item.travel_buffer) for item in WORKLOAD_LEVELS
    ]
    assert table == [
        ("Спокойный день", "😌", 20_000, TravelBuffer(1.30, 5)),
        ("Обычный день", "😐", 1_000_000, TravelBuffer(1.10, 5)),
        ("На пределе", "🥵", 6_000_000, TravelBuffer(1.00, 0)),
    ]
    # Сценарии с минутами, посчитанными прямо по матрице, планируются на уровне без запаса.
    assert EXACT_TRAVEL_LEVEL == 2


def test_level_outside_zero_to_two_is_rejected_with_dispatcher_text():
    assert WORKLOAD_LEVEL_TEXT == "уровень нагрузки должен быть от 0 до 2"
    assert [level for level in range(-1, 5) if is_workload_level(level)] == [0, 1, 2]
    for level in (-1, 3, 4):
        with pytest.raises(ValueError, match="^уровень нагрузки должен быть от 0 до 2$"):
            workload(level)
        with pytest.raises(ValueError, match="от 0 до 2"):
            workload_weights(level)


def test_level_changes_only_the_new_engineer_cost():
    default = ObjectiveWeights()
    for level, item in enumerate(WORKLOAD_LEVELS):
        weights = workload_weights(level)
        assert weights == replace(default, vehicle_fixed_cost=item.vehicle_fixed_cost)
        # Даже на пределе новый инженер дешевле, чем оставить заявку без исполнителя.
        assert weights.vehicle_fixed_cost < weights.drop_normal
        assert travel_buffer(level) == item.travel_buffer
    assert workload_weights(DEFAULT_WORKLOAD_LEVEL) == default


@pytest.mark.parametrize(
    ("level", "trips"),
    [
        (0, {0: 0, 1: 6, 10: 15, 40: 52}),
        (1, {0: 0, 1: 6, 10: 15, 60: 66, 100: 110}),
        (2, {0: 0, 1: 1, 37: 37}),
    ],
)
def test_buffer_is_factor_rounded_up_but_not_less_than_minimum_extra(level, trips):
    buffer = travel_buffer(level)
    assert {minutes: buffer.minutes(minutes) for minutes in trips} == trips


def test_no_buffer_keeps_minutes_and_trip_to_the_same_point_stays_zero():
    assert [NO_BUFFER.minutes(minutes) for minutes in (0, 1, 16)] == [0, 1, 16]
    problem = buffered_problem(
        [req("A", 2, 0, "10:00", "12:00"), req("B", 2, 0, "10:00", "12:00")], [eng("E1")], 0
    )
    engineer = problem.engineers[0]
    a, b = problem.request_node("A"), problem.request_node("B")
    assert (problem.travel_min(a, b, engineer), problem.travel_min(a, a, engineer)) == (0, 0)
    assert problem.travel_min(problem.home_node("E1"), a, engineer) > 0


def test_buffer_makes_visit_start_later_but_keeps_kilometres():
    requests, engineers = [req("R1", 5, 0, "09:00", "12:00")], [eng("E1")]
    raw, buffered = problem_of(requests, engineers), buffered_problem(requests, engineers)
    [plain] = simulate_route(raw, raw.states[0], ["R1"]).visits
    [slow] = simulate_route(buffered, buffered.states[0], ["R1"]).visits
    # 6.5 км на машине без пробок 16 минут; обычный день: max(ceil(16 * 1.1), 16 + 5) = 21.
    assert (plain.leg_min, plain.arrival, plain.start) == (16, 556, 556)
    assert (slow.leg_min, slow.arrival, slow.start, slow.end) == (21, 561, 561, 591)
    assert slow.leg_km == plain.leg_km == 6.51


def test_kilometres_and_route_distance_do_not_depend_on_level():
    requests = [
        req("R1", 5, 0, "09:00", "17:00"),
        req("R2", 1, 0, "09:00", "17:00"),
        req("R3", -3, 2, "09:00", "17:00"),
    ]
    engineers = [eng("E1")]
    problems = [buffered_problem(requests, engineers, level) for level in range(len(WORKLOAD_LEVELS))]
    engineer, nodes = engineers[0], range(len(requests) + len(engineers))
    for problem in problems:
        assert [[problem.travel_km(a, b, engineer) for b in nodes] for a in nodes] == [
            [problems[0].travel_km(a, b, engineer) for b in nodes] for a in nodes
        ]
    plans = [build_plan(problem, "fcfs", {"E1": ["R1", "R2", "R3"]}) for problem in problems]
    assert {plan.metrics.total_km for plan in plans} == {plans[0].metrics.total_km}
    assert plans[0].routes[0].total_travel_min > plans[-1].routes[0].total_travel_min


def test_fcfs_and_ortools_plan_with_buffered_minutes():
    # Без запаса инженер приезжает к R1 в 09:16 и успевает в окно до 09:18, с запасом обычного дня только в 09:21.
    requests, engineers = [req("R1", 5, 0, "09:00", "09:18"), req("R2", 1, 0, "10:00", "12:00")], [eng("E1")]
    raw, buffered = problem_of(requests, engineers), buffered_problem(requests, engineers)
    for solver in (FcfsSolver(), OrToolsSolver(time_limit_s=1)):
        assert routes(solver.solve(raw)) == {"E1": ["R1", "R2"]}
        plan = solver.solve(buffered)
        assert routes(plan) == {"E1": ["R2"]}, solver.name
        [visit] = plan.routes[0].visits
        assert (visit.leg_min, visit.arrival) == (9, 549)  # 4 минуты без запаса, с запасом 4 + 5
        [lost] = plan.unassigned
        assert (lost.request_id, lost.reason_code) == ("R1", ReasonCode.DOES_NOT_FIT)
        assert "начнёт не раньше 09:21" in lost.reason_text
    # Модель OR-Tools сама видит минуты с запасом, а не только страховочная проверка маршрута после поиска.
    solver = OrToolsSolver(time_limit_s=1)
    assert solver._solve_model(raw, raw.states, ["R1", "R2"]) == [["R1", "R2"]]
    assert solver._solve_model(buffered, buffered.states, ["R1", "R2"]) == [["R2"]]


def test_calm_day_uses_more_engineers_than_day_at_the_limit():
    # Инженеры стартуют в 20 км друг от друга, у каждого рядом своя заявка, любой успевает к обеим за смену.
    far_lat, far_lon = at(20, 0)
    engineers = [eng("E1"), eng("E2").model_copy(update={"start_lat": far_lat, "start_lon": far_lon})]
    requests = [req("R1", 0.5, 0, "09:00", "17:00"), req("R2", 20.5, 0, "09:00", "17:00")]

    def plan_at(level):
        solver = OrToolsSolver(time_limit_s=1, weights=workload_weights(level))
        return solver.solve(buffered_problem(requests, engineers, level))

    calm, limit = plan_at(0), plan_at(2)
    assert routes(calm) == {"E1": ["R1"], "E2": ["R2"]}
    assert (calm.metrics.engineers_used, limit.metrics.engineers_used) == (2, 1)
    assert calm.unassigned == limit.unassigned == []
    assert limit.metrics.total_km > calm.metrics.total_km


@pytest.fixture
def solver_weights(monkeypatch):
    """Веса, с которыми сессия создавала OrToolsSolver, по порядку решений."""
    seen = []

    class SpySolver(OrToolsSolver):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.append(self.weights)

    monkeypatch.setattr(session_module, "OrToolsSolver", SpySolver)
    return seen


def test_session_starts_at_default_level(solver_weights):
    session = new_session()
    assert session.workload_level == DEFAULT_WORKLOAD_LEVEL
    assert session.problem.buffer == travel_buffer(DEFAULT_WORKLOAD_LEVEL)
    assert solver_weights == [workload_weights(DEFAULT_WORKLOAD_LEVEL)]


def test_events_keep_session_level_for_weights_and_travel_buffer(solver_weights):
    ctx = context()
    session = new_session(ctx=ctx, workload_level=0)
    assert (session.workload_level, session.problem.buffer) == (0, travel_buffer(0))

    updated = apply_event(session, Event(type=EventType.CANCEL, time="13:00", request_id="R2"), ctx)

    assert (updated.workload_level, updated.problem.buffer, updated.version) == (0, travel_buffer(0), 2)
    assert solver_weights == [workload_weights(0), workload_weights(0)]
    for route in updated.baseline.routes:
        for visit in route.visits:
            if not visit.pinned and visit.leg_min:
                assert visit.leg_min >= 5  # FCFS тоже едет с запасом дня
