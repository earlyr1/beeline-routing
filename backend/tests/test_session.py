import pytest

from app.domain.enums import EventType, Priority, RequestStatus
from app.domain.models import Event
from app.ingest.geocode import GeoResult
from app.planning.session import EventRejected, apply_event
from tests.helpers import req
from tests.planning_helpers import busy_engineer, context, new_session, routes


def test_start_session_builds_optimized_and_baseline_plans():
    session = new_session()
    assert session.plan.solver == "ortools" and session.baseline.solver == "fcfs"
    assert session.plan.metrics.engineers_used == 1
    assert (session.version, session.now, session.events) == (1, 0, [])


def test_cancel_future_request_pins_past_and_reports_diff():
    session = new_session()
    ctx = context()
    updated = apply_event(session, Event(type=EventType.CANCEL, time="13:00", request_id="R2"), ctx)

    assert updated.request("R2").status == RequestStatus.CANCELLED
    assert session.request("R2").status == RequestStatus.ACTIVE  # исходная сессия не изменилась
    engineer_id = busy_engineer(updated.plan)
    first = updated.plan.routes[[r.engineer_id for r in updated.plan.routes].index(engineer_id)].visits[0]
    assert (first.request_id, first.pinned) == ("R1", True)
    assert "R2" not in [rid for seq in routes(updated.plan).values() for rid in seq]
    assert [(r.request_id, r.reason) for r in updated.last_diff.removed] == [("R2", "Заявка отменена")]
    assert (updated.version, updated.now, len(updated.events)) == (2, 780, 1)
    assert updated.previous_plan == session.plan
    assert updated.baseline.solver == "fcfs"
    assert any(v.pinned for route in updated.baseline.routes for v in route.visits)


def test_cannot_cancel_started_request():
    session = new_session()
    with pytest.raises(EventRejected, match="уже в работе"):
        apply_event(session, Event(type=EventType.CANCEL, time="10:15", request_id="R1"), context())


def test_event_time_cannot_go_back():
    ctx = context()
    session = apply_event(new_session(), Event(type=EventType.CANCEL, time="13:00", request_id="R2"), ctx)
    with pytest.raises(EventRejected, match="раньше текущего времени"):
        apply_event(session, Event(type=EventType.RESTORE, time="12:00", request_id="R2"), ctx)


def test_restore_returns_request_to_plan():
    ctx = context()
    cancelled = apply_event(new_session(), Event(type=EventType.CANCEL, time="13:00", request_id="R2"), ctx)
    restored = apply_event(cancelled, Event(type=EventType.RESTORE, time="13:30", request_id="R2"), ctx)
    assert restored.request("R2").status == RequestStatus.ACTIVE
    assert [a.request_id for a in restored.last_diff.added] == ["R2"]


def test_restore_after_window_is_rejected():
    ctx = context()
    cancelled = apply_event(new_session(), Event(type=EventType.CANCEL, time="11:00", request_id="R2"), ctx)
    with pytest.raises(EventRejected, match="уже прошло"):
        apply_event(cancelled, Event(type=EventType.RESTORE, time="16:30", request_id="R2"), ctx)


def test_engineer_unavailable_moves_future_work_to_another_engineer():
    session = new_session()
    engineer_id = busy_engineer(session.plan)
    other = "E2" if engineer_id == "E1" else "E1"
    updated = apply_event(
        session, Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id=engineer_id), context()
    )
    assert routes(updated.plan)[engineer_id] == ["R1"]
    assert sorted(routes(updated.plan)[other]) == ["R2", "R3"]
    assert {(m.request_id, m.to_engineer_id) for m in updated.last_diff.moved} == {
        ("R2", other),
        ("R3", other),
    }
    engineer = updated.engineer(engineer_id)
    assert (engineer.available, engineer.unavailable_from) == (False, 780)


def test_urgent_request_is_added_and_geocoded_when_needed():
    geocoded = []

    def geocode(address, district):
        geocoded.append(address)
        return GeoResult(55.7505, 37.61, "house", address)

    urgent = req("U1", 0, 0, "13:00", "15:00", duration=60).model_copy(update={"lat": None, "lon": None})
    updated = apply_event(
        new_session(), Event(type=EventType.URGENT, time="13:00", request=urgent), context(geocode=geocode)
    )
    assert geocoded == ["адрес U1"]
    assert [a.request_id for a in updated.last_diff.added] == ["U1"]
    stored = updated.events[0].event.request
    assert (stored.priority, stored.lat, stored.geocode_precision) == (Priority.URGENT, 55.7505, "house")


def test_urgent_with_existing_id_is_rejected():
    duplicate = req("R1", 0, 0, "13:00", "15:00")
    with pytest.raises(EventRejected, match="уже есть"):
        apply_event(new_session(), Event(type=EventType.URGENT, time="13:00", request=duplicate), context())
