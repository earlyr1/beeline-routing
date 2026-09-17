"""Параллельный поиск OR-Tools: стоимость плана, выбор лучшей стратегии и пул процессов."""

import time

import pytest

from app.domain.enums import EventType, Priority
from app.domain.models import Event, Metrics, Plan, Route, Unassigned, Visit
from app.planning.session import apply_event
from app.planning.workload import workload_weights
from app.solvers.ortools_solver import DEFAULT_STRATEGY, ObjectiveWeights
from app.solvers.portfolio import PORTFOLIO, SolverPool, plan_cost
from tests.helpers import req
from tests.planning_helpers import busy_engineer, context, new_session


def _plan(routes_, unassigned=(), violations=()):
    return Plan(
        solver="ortools",
        routes=routes_,
        unassigned=list(unassigned),
        metrics=Metrics(
            engineers_used=0, km_per_engineer={}, total_km=0, assigned=0, unassigned=len(unassigned)
        ),
        violations=list(violations),
    )


def _visit(request_id, start=600, km=2.0):
    return Visit(request_id=request_id, arrival=start, start=start, end=start + 30, leg_km=km, leg_min=10)


def test_plan_cost_follows_the_solver_objective():
    problem = new_session(
        context(),
        requests=[
            req("R1", 1, 0, "10:00", "12:00"),
            req("R2", 1.2, 0, "14:00", "16:00", priority=Priority.URGENT),
            req("R3", -1, 0, "15:00", "17:00"),
        ],
    ).problem
    weights = ObjectiveWeights()
    both = _plan(
        [
            Route(engineer_id="E1", visits=[_visit("R1")], total_km=2, total_travel_min=10),
            Route(engineer_id="E2", visits=[_visit("R2", km=1.5)], total_km=1.5, total_travel_min=10),
        ],
        unassigned=[Unassigned(request_id="R3", reason_code="no_free_engineer_in_window", reason_text="x")],
    )
    assert plan_cost(problem, both, weights) == 2 * weights.vehicle_fixed_cost + 3500 + weights.drop_normal
    urgent_dropped = _plan(
        [Route(engineer_id="E1", visits=[_visit("R1")], total_km=2, total_travel_min=10)],
        unassigned=[Unassigned(request_id="R2", reason_code="no_free_engineer_in_window", reason_text="x")],
    )
    assert (
        plan_cost(problem, urgent_dropped, weights) == weights.vehicle_fixed_cost + 2000 + weights.drop_urgent
    )
    broken = _plan([], violations=["R1: начало позже окна"])
    assert plan_cost(problem, broken, weights) == weights.drop_urgent


def test_pool_shares_the_portfolio_between_simultaneous_searches():
    assert PORTFOLIO[0] == DEFAULT_STRATEGY and len(set(PORTFOLIO)) == len(PORTFOLIO) == 4
    assert SolverPool(4).strategies() == PORTFOLIO
    assert SolverPool(4).strategies(share=2) == PORTFOLIO[:2]
    assert SolverPool(3).strategies(share=2) == PORTFOLIO[:1]
    assert SolverPool(8).strategies() == PORTFOLIO
    with pytest.raises(ValueError):
        SolverPool(0)


def test_pool_picks_the_cheapest_plan_of_the_strategies(monkeypatch):
    problem = new_session(context()).problem
    weights = workload_weights(1)
    cheap = _plan([Route(engineer_id="E1", visits=[_visit("R1", km=1)], total_km=1, total_travel_min=10)])
    dear = _plan([Route(engineer_id="E1", visits=[_visit("R1", km=9)], total_km=9, total_travel_min=10)])

    class Done:
        def __init__(self, value):
            self.value = value

        def result(self, timeout=None):
            return self.value

    class FakeExecutor:
        def submit(self, fn, problem_, weights_, limit, strategy):
            return Done("dear" if strategy == PORTFOLIO[0] else "cheap")

    pool = SolverPool(2)
    monkeypatch.setattr(pool, "_pool", lambda: FakeExecutor())
    monkeypatch.setattr(
        "app.solvers.portfolio.build_plan", lambda problem_, name, seq: cheap if seq == "cheap" else dear
    )
    assert pool.solve(problem, weights, 1, PORTFOLIO[:2]) is cheap


def test_pool_falls_back_to_a_search_in_this_process_when_no_worker_answers(monkeypatch):
    problem = new_session(context()).problem

    class Broken:
        def result(self, timeout=None):
            raise RuntimeError("процесс упал")

    class FakeExecutor:
        def submit(self, *args):
            return Broken()

        def shutdown(self, wait=False, cancel_futures=False):
            pass

    pool = SolverPool(2)
    monkeypatch.setattr(pool, "_pool", lambda: FakeExecutor())
    plan = pool.solve(problem, workload_weights(1), 1, PORTFOLIO[:2])
    assert plan.solver == "ortools" and plan.metrics.assigned == 3


def test_real_processes_solve_the_day_and_both_event_variants_at_once():
    pool = SolverPool(2)
    try:
        pool.warm_up()
        ctx = context(solver_pool=pool, time_limit_s=2, time_limit_lunch_s=2)
        base = new_session(ctx)
        assert base.plan.metrics.assigned == 3 and base.plan.violations == []
        busy = busy_engineer(base.plan)
        event = Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id=busy)

        started = time.monotonic()
        replanned = apply_event(base, event, ctx, variant="stable")
        # Две стратегии в пуле идут одновременно: одна двухсекундная попытка плюс передача задачи процессам.
        assert time.monotonic() - started < 2 + 10
        assert not any(
            visit.start >= 13 * 60
            for route in replanned.plan.routes
            if route.engineer_id == busy
            for visit in route.visits
        )
    finally:
        pool.shutdown()


def test_solve_uses_the_pool_with_the_share_of_the_event(monkeypatch):
    calls = []

    class Pool:
        workers = 4

        def strategies(self, share=1):
            calls.append(share)
            return PORTFOLIO[: 4 // share]

        def solve(self, problem, weights, limit, strategies):
            calls.append((limit, len(strategies), weights.reassignment))
            from app.solvers.fcfs import FcfsSolver

            return FcfsSolver().solve(problem)

    pool = Pool()
    ctx = context(solver_pool=pool)
    base = new_session(ctx)
    assert calls == [1, (1, 4, 20_000)]
    calls.clear()
    busy = busy_engineer(base.plan)
    apply_event(
        base,
        Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id=busy),
        ctx,
        variant="stable",
    )
    apply_event(base, Event(type=EventType.CANCEL, time="09:30", request_id="R2"), ctx)
    assert calls == [2, (1, 2, 500_000), 1, (1, 4, 20_000)]
