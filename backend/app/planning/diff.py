"""Что изменилось между двумя планами."""

from __future__ import annotations

from collections.abc import Collection

from app.domain.models import Plan, Visit
from app.planning.models import DiffAssign, DiffMove, DiffRemove, DiffShift, PlanDiff


def _assignments(plan: Plan) -> dict[str, tuple[str, Visit]]:
    return {visit.request_id: (route.engineer_id, visit) for route in plan.routes for visit in route.visits}


def compute_diff(before: Plan, after: Plan, cancelled_ids: Collection[str] = ()) -> PlanDiff:
    old, new = _assignments(before), _assignments(after)
    unassigned_text = {item.request_id: item.reason_text for item in after.unassigned}

    moved = [
        DiffMove(request_id=rid, from_engineer_id=old[rid][0], to_engineer_id=new[rid][0])
        for rid in old
        if rid in new and old[rid][0] != new[rid][0]
    ]
    added = [DiffAssign(request_id=rid, engineer_id=new[rid][0]) for rid in new if rid not in old]
    removed = []
    for rid, (engineer_id, _) in old.items():
        if rid in new:
            continue
        reason = "Заявка отменена" if rid in cancelled_ids else unassigned_text.get(rid, "Снята с плана")
        removed.append(DiffRemove(request_id=rid, engineer_id=engineer_id, reason=reason))

    old_order = {route.engineer_id: [v.request_id for v in route.visits] for route in before.routes}
    reordered = []
    for route in after.routes:
        new_seq = [v.request_id for v in route.visits]
        old_seq = old_order.get(route.engineer_id, [])
        common_old = [rid for rid in old_seq if rid in set(new_seq)]
        common_new = [rid for rid in new_seq if rid in set(old_seq)]
        if common_old != common_new:
            reordered.append(route.engineer_id)

    shifts = [
        DiffShift(
            request_id=rid,
            engineer_id=new[rid][0],
            old_start=old[rid][1].start,
            new_start=new[rid][1].start,
            delta_min=new[rid][1].start - old[rid][1].start,
        )
        for rid in new
        if rid in old and new[rid][1].start != old[rid][1].start
    ]
    return PlanDiff(
        moved=moved,
        added=added,
        removed=removed,
        reordered_engineers=reordered,
        time_shifts=shifts,
        metrics_before=before.metrics,
        metrics_after=after.metrics,
    )
