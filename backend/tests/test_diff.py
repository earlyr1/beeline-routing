from app.domain.enums import ReasonCode
from app.domain.models import Metrics, Plan, Route, Unassigned, Visit
from app.planning.diff import compute_diff


def _plan(routes, unassigned=()):
    built = [
        Route(
            engineer_id=eid,
            visits=[
                Visit(request_id=rid, arrival=start, start=start, end=start + 30, leg_km=1.0, leg_min=5)
                for rid, start in visits
            ],
        )
        for eid, visits in routes.items()
    ]
    metrics = Metrics(
        engineers_used=sum(1 for r in built if r.visits),
        km_per_engineer={},
        total_km=0.0,
        assigned=sum(len(r.visits) for r in built),
        unassigned=len(unassigned),
    )
    return Plan(solver="ortools", routes=built, unassigned=list(unassigned), metrics=metrics)


def test_diff_detects_moves_additions_removals_reorders_and_shifts():
    before = _plan({"E1": [("A", 600), ("B", 700), ("C", 800)], "E2": [("D", 600)]})
    after = _plan(
        {"E1": [("B", 600), ("A", 700)], "E2": [("D", 600), ("C", 900), ("U", 1000)]},
        unassigned=[Unassigned(request_id="X", reason_code=ReasonCode.NO_SKILL, reason_text="нет")],
    )
    diff = compute_diff(before, after)
    assert [(m.request_id, m.from_engineer_id, m.to_engineer_id) for m in diff.moved] == [("C", "E1", "E2")]
    assert [(a.request_id, a.engineer_id) for a in diff.added] == [("U", "E2")]
    assert diff.removed == []
    assert diff.reordered_engineers == ["E1"]
    shifts = {s.request_id: s.delta_min for s in diff.time_shifts}
    assert shifts == {"B": -100, "A": 100, "C": 100}
    assert diff.metrics_before.assigned == 4 and diff.metrics_after.assigned == 5


def test_removed_reason_prefers_cancellation_then_unassigned_text():
    before = _plan({"E1": [("A", 600), ("B", 700), ("C", 800)]})
    after = _plan(
        {"E1": []},
        unassigned=[
            Unassigned(
                request_id="B",
                reason_code=ReasonCode.NO_FREE_ENGINEER,
                reason_text="Нет свободных исполнителей",
            )
        ],
    )
    diff = compute_diff(before, after, cancelled_ids={"A"})
    assert [(r.request_id, r.reason) for r in diff.removed] == [
        ("A", "Заявка отменена"),
        ("B", "Нет свободных исполнителей"),
        ("C", "Снята с плана"),
    ]


def test_diff_serializes_times_as_hhmm():
    before = _plan({"E1": [("A", 600)]})
    after = _plan({"E1": [("A", 615)]})
    dumped = compute_diff(before, after).model_dump(mode="json")
    assert dumped["time_shifts"][0] == {
        "request_id": "A",
        "engineer_id": "E1",
        "old_start": "10:00",
        "new_start": "10:15",
        "delta_min": 15,
    }
