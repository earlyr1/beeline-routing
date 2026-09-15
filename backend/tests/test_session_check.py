import pytest

from app.domain.models import Event
from app.ingest.geocode import GeoResult
from app.planning.session import EventRejected, apply_event, check_event
from tests.helpers import req
from tests.planning_helpers import context, new_session


def test_check_event_geocodes_urgent_request_without_replanning():
    ctx = context(geocode=lambda address, district: GeoResult(55.7, 37.6, "street", address))
    session = new_session(ctx)
    urgent_request = req("U1", 0, 0, "13:00", "15:00").model_copy(update={"lat": None, "lon": None})

    stored = check_event(session, Event(type="urgent", time="13:00", request=urgent_request), ctx)

    assert (stored.request.lat, stored.request.lon, stored.request.geocode_precision) == (
        55.7,
        37.6,
        "street",
    )
    assert stored.request.priority == "urgent"
    assert session.version == 1 and session.request("U1") is None


def test_check_event_rejects_conflicts_like_apply_event():
    ctx = context()
    session = new_session(ctx)
    with pytest.raises(EventRejected, match="Заявка NOPE не найдена"):
        check_event(session, Event(type="cancel", time="13:00", request_id="NOPE"), ctx)

    later = apply_event(session, Event(type="cancel", time="13:00", request_id="R3"), ctx)
    with pytest.raises(EventRejected, match="раньше текущего времени плана 13:00"):
        check_event(later, Event(type="cancel", time="12:00", request_id="R2"), ctx)
    with pytest.raises(EventRejected, match="уже отменена"):
        check_event(later, Event(type="cancel", time="13:00", request_id="R3"), ctx)
