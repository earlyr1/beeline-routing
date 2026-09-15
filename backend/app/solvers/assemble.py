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
    lunch: bool = True,
) -> Plan:
    """План по последовательностям. lunch=False — план без обеда (план диспетчеров показывается как есть)."""
    fixed_unassigned = fixed_unassigned or {}
    routes: list[Route] = []
    violations: list[str] = []
    placed: set[str] = set()
    for state in problem.states:
        engineer_id = state.engineer.id
        sim = simulate_route(problem, state, sequences.get(engineer_id, []), lunch=lunch)
        # Флаг pinned у закреплённых визитов задаёт pin_problem: True только у начатой работы.
        visits = list(problem.pinned.get(engineer_id, [])) + sim.visits
        violations.extend(sim.violations)
        placed.update(visit.request_id for visit in visits)
        routes.append(
            Route(
                engineer_id=engineer_id,
                visits=visits,
                total_km=round(sum(visit.leg_km for visit in visits), 2),
                total_travel_min=sum(visit.leg_min for visit in visits),
                # Начатый до события обед остаётся как в прежнем плане, остальной ставит прогон маршрута.
                lunch=(problem.pinned_lunch.get(engineer_id) or sim.lunch) if lunch else None,
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
