from __future__ import annotations

from app.domain.models import Metrics, Route, Unassigned


def compute_metrics(routes: list[Route], unassigned: list[Unassigned], violations: int) -> Metrics:
    used = [route for route in routes if route.visits]
    return Metrics(
        engineers_used=len(used),
        km_per_engineer={route.engineer_id: route.total_km for route in used},
        total_km=round(sum(route.total_km for route in used), 2),
        assigned=sum(len(route.visits) for route in used),
        unassigned=len(unassigned),
        violations=violations,
    )
