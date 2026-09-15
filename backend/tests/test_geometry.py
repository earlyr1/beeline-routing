import httpx

from app.api.geometry import route_geometry
from app.domain.enums import Transport
from app.geo.kvcache import KVCache
from app.geo.osrm import OsrmClient
from tests.helpers import eng
from tests.planning_helpers import busy_engineer, new_session


def test_osrm_legs_are_cached_and_public_transport_uses_straight_lines():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "routes": [{"geometry": {"coordinates": [[37.6, 55.75], [37.61, 55.751], [37.62, 55.752]]}}],
            },
        )

    osrm = OsrmClient("http://osrm:5000", client=httpx.Client(transport=httpx.MockTransport(handler)))
    cache = KVCache(":memory:")
    session = new_session()
    engineer_id = busy_engineer(session.plan)

    first = route_geometry(session, engineer_id, "current", osrm, cache)
    second = route_geometry(session, engineer_id, "current", osrm, cache)
    assert first.source == "osrm" and len(first.legs) == 3
    assert first.legs[0].coordinates[1] == [37.61, 55.751]
    assert second == first and len(calls) == 3

    public = new_session(engineers=[eng("E1", transport=Transport.PUBLIC)])
    straight = route_geometry(public, "E1", "current", osrm, cache)
    assert straight.source == "straight" and all(len(leg.coordinates) == 2 for leg in straight.legs)


def test_osrm_failure_falls_back_to_straight():
    def handler(request):
        return httpx.Response(500)

    osrm = OsrmClient("http://osrm:5000", client=httpx.Client(transport=httpx.MockTransport(handler)))
    session = new_session()
    result = route_geometry(session, busy_engineer(session.plan), "current", osrm, None)
    assert result.source == "straight"
