"""Сборка плана из последовательностей заявок: время, пробег, причины, метрики."""

from __future__ import annotations

from app.domain.models import Plan, Route, Unassigned
from app.solvers.metrics import compute_metrics
from app.solvers.problem import Problem
from app.solvers.reasons import unassigned_reason
from app.solvers.simulate import simulate_route


def build_plan(
    problem: Problem,
    solver: str,
    sequences: dict[str, list[str]],
    *,
    fixed_unassigned: dict[str, Unassigned] | None = None,
) -> Plan:
    fixed_unassigned = fixed_unassigned or {}
    routes: list[Route] = []
    violations: list[str] = []
    placed: set[str] = set()
    for state in problem.states:
        engineer_id = state.engineer.id
        sim = simulate_route(problem, state, sequences.get(engineer_id, []))
        pinned = [visit.model_copy(update={"pinned": True}) for visit in problem.pinned.get(engineer_id, [])]
        visits = pinned + sim.visits
        violations.extend(sim.violations)
        placed.update(visit.request_id for visit in visits)
        routes.append(
            Route(
                engineer_id=engineer_id,
                visits=visits,
                total_km=round(sum(visit.leg_km for visit in visits), 2),
                total_travel_min=sum(visit.leg_min for visit in visits),
            )
        )
    unassigned = list(problem.unplannable)
    for request_id in problem.open_request_ids:
        if request_id not in placed:
            unassigned.append(
                fixed_unassigned.get(request_id) or unassigned_reason(problem, request_id, sequences)
            )
    return Plan(
        solver=solver,
        routes=routes,
        unassigned=unassigned,
        metrics=compute_metrics(routes, unassigned, len(violations)),
        violations=violations,
    )
