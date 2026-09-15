import httpx
import pytest

from app.domain.enums import Transport
from app.geo.haversine import haversine_km
from app.geo.kvcache import KVCache
from app.geo.matrix import BaseMatrix, TrafficProfile, TravelModel, TravelTimes, build_base_matrix
from app.geo.osrm import OsrmClient

POINTS = [(55.7558, 37.6176), (55.7000, 37.7800)]


def test_haversine_moscow_to_saint_petersburg():
    assert haversine_km(55.7558, 37.6176, 59.9343, 30.3351) == pytest.approx(634, abs=5)


def test_kvcache_roundtrip():
    cache = KVCache(":memory:")
    assert cache.get("k") is None
    cache.set("k", "v")
    assert cache.get("k") == "v"


def _osrm(handler):
    return OsrmClient("http://osrm:5000", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_osrm_table_converts_units_and_keeps_unreachable_as_none():
    def handler(request):
        assert request.url.path == "/table/v1/driving/37.617600,55.755800;37.780000,55.700000"
        assert request.url.params["annotations"] == "duration,distance"
        return httpx.Response(
            200,
            json={"code": "Ok", "distances": [[0, 12000], [None, 0]], "durations": [[0, 1200], [None, 0]]},
        )

    km, minutes = _osrm(handler).table(POINTS)
    assert km == [[0.0, 12.0], [None, 0.0]]
    assert minutes == [[0.0, 20.0], [None, 0.0]]


def test_build_base_matrix_uses_osrm_fills_gaps_and_caches():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"code": "Ok", "distances": [[0, 12000], [None, 0]], "durations": [[0, 1200], [None, 0]]},
        )

    cache = KVCache(":memory:")
    model = TravelModel()
    first = build_base_matrix(POINTS, model, osrm=_osrm(handler), cache=cache)
    second = build_base_matrix(POINTS, model, osrm=_osrm(handler), cache=cache)
    assert first.source == "osrm" and first.road_km[0][1] == 12.0
    assert first.road_km[1][0] == pytest.approx(first.straight_km[1][0] * model.detour_factor)
    assert second == first
    assert len(calls) == 1


def test_build_base_matrix_falls_back_to_haversine_when_osrm_is_down():
    def handler(request):
        return httpx.Response(503)

    matrix = build_base_matrix(POINTS, TravelModel(), osrm=_osrm(handler))
    assert matrix.source == "haversine"


def test_travel_times_rules_per_transport():
    base = BaseMatrix(
        road_km=[[0, 10.0], [10.0, 0]],
        car_min=[[0, 20.0], [20.0, 0]],
        straight_km=[[0, 8.0], [8.0, 0]],
        source="osrm",
    )
    travel = TravelTimes(base, TravelModel(), TrafficProfile({17: 1.8}))
    assert travel.minutes(0, 1, Transport.CAR, slot_min=10 * 60) == 20
    assert travel.minutes(0, 1, Transport.CAR, slot_min=17 * 60 + 30) == 36
    assert travel.km(0, 1, Transport.BIKE) == 10.0
    assert travel.minutes(0, 1, Transport.BIKE, slot_min=17 * 60) == 30
    assert travel.km(0, 1, Transport.FOOT) == pytest.approx(12.0)
    assert travel.minutes(0, 1, Transport.FOOT, slot_min=0) == 144
    assert travel.km(0, 1, Transport.PUBLIC) == pytest.approx(10.4)
    assert travel.minutes(0, 1, Transport.PUBLIC, slot_min=0) == 52
    assert travel.minutes(1, 1, Transport.PUBLIC, slot_min=0) == 0


def test_traffic_profile_loads_yaml(tmp_path):
    path = tmp_path / "traffic.yaml"
    path.write_text("factors:\n  8: 1.7\n", encoding="utf-8")
    profile = TrafficProfile.load(path)
    assert profile.factor_at(8 * 60 + 59) == 1.7
    assert profile.factor_at(3 * 60) == 1.0
