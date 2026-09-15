import json

import httpx
import pytest

from app.ingest import geocode as geocode_module
from app.ingest.geocode import GeoHit, GeoResult, JsonGeocodeCache, NominatimGeocoder, geocode_address

ADDRESS = "Город Москва, ул.Грайвороновская, д. 10 к 2"


class FakeGeocoder:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def lookup(self, query):
        self.calls.append(query)
        return self.answers.get(query)


def test_falls_back_from_house_to_street(tmp_path):
    geocoder = FakeGeocoder({"Москва, Грайвороновская улица": GeoHit(55.72, 37.73, "highway")})
    cache = JsonGeocodeCache(tmp_path / "cache.json")
    result = geocode_address("Город Москва, ул.Грайвороновская, д. 10 к 2", "Текстильщики", geocoder, cache)
    assert (result.lat, result.lon, result.precision) == (55.72, 37.73, "street")
    assert geocoder.calls == [
        "Москва, Грайвороновская улица, 10к2",
        "Москва, улица Грайвороновская, 10к2",
        "Москва, Грайвороновская улица",
    ]


def test_house_hit_on_a_road_is_downgraded_to_street(tmp_path):
    geocoder = FakeGeocoder({"Москва, Грайвороновская улица, 10к2": GeoHit(55.72, 37.73, "highway")})
    result = geocode_address(
        "Город Москва, ул.Грайвороновская, д. 10 к 2", "", geocoder, JsonGeocodeCache(tmp_path / "c.json")
    )
    assert result.precision == "street"


def test_rejects_hits_outside_moscow_region(tmp_path):
    geocoder = FakeGeocoder(
        {
            "Москва, Грайвороновская улица, 10к2": GeoHit(59.93, 30.31, "building"),
            "Москва, улица Грайвороновская, 10к2": GeoHit(55.72, 37.73, "building"),
        }
    )
    result = geocode_address(
        "Город Москва, ул.Грайвороновская, д. 10 к 2", "", geocoder, JsonGeocodeCache(tmp_path / "c.json")
    )
    assert (result.lat, result.precision) == (55.72, "house")


def test_cache_persists_hits_and_misses_and_avoids_repeat_calls(tmp_path):
    path = tmp_path / "cache.json"
    geocoder = FakeGeocoder({"Кашира, Московская область": GeoHit(54.83, 38.15, "boundary")})
    cache = JsonGeocodeCache(path)
    geocode_address("Кашира, ул.Победы, д. 9", "Кашира", geocoder, cache)
    cache.save()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["Кашира, Московская область"] == [54.83, 38.15, "boundary"]
    assert stored["Кашира, Московская область, Победы улица, 9"] is None

    second = FakeGeocoder({})
    result = geocode_address("Кашира, ул.Победы, д. 9", "Кашира", second, JsonGeocodeCache(path))
    assert second.calls == []
    assert result.precision == "locality"


def test_without_geocoder_uses_cache_only(tmp_path):
    result = geocode_address("Кашира, ул.Победы, д. 9", "", None, JsonGeocodeCache(tmp_path / "c.json"))
    assert (result.lat, result.precision) == (None, "none")


def test_nominatim_client_sends_bounded_query_and_respects_rate_limit():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"lat": "55.70", "lon": "37.78", "category": "building"}])

    sleeps = []
    ticks = iter([0.0, 0.2, 0.2])
    geocoder = NominatimGeocoder(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
        clock=lambda: next(ticks),
    )
    assert geocoder.lookup("Москва, Волгоградский проспект, 128к5") == GeoHit(55.70, 37.78, "building")
    geocoder.lookup("второй запрос")
    params = seen[0].url.params
    assert params["bounded"] == "1" and params["format"] == "jsonv2" and params["countrycodes"] == "ru"
    assert seen[0].headers["User-Agent"].startswith("beeline-routing")
    assert len(sleeps) == 1 and abs(sleeps[0] - 0.9) < 1e-9


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def network_up(monkeypatch):
    monkeypatch.setattr(geocode_module, "_network_down_until", None)


def _unreachable_nominatim(attempts):
    def handler(request):
        attempts.append(request.url.params["q"])
        raise httpx.ConnectError("сеть недоступна", request=request)

    return NominatimGeocoder(
        client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None, clock=lambda: 0.0
    )


def test_nominatim_default_timeout_is_five_seconds():
    assert NominatimGeocoder()._client.timeout == httpx.Timeout(5.0)


def test_unreachable_nominatim_fails_fast_and_failure_is_not_cached(tmp_path, network_up):
    attempts = []
    path = tmp_path / "cache.json"
    cache = JsonGeocodeCache(path)
    result = geocode_address(
        ADDRESS, "Текстильщики", _unreachable_nominatim(attempts), cache, clock=FakeClock(1000.0)
    )
    assert result == GeoResult(None, None, "none", None)
    assert attempts == ["Москва, Грайвороновская улица, 10к2"]
    cache.save()
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_after_network_failure_geocoder_is_not_called_for_a_minute(tmp_path, network_up):
    attempts = []
    geocoder = _unreachable_nominatim(attempts)
    clock = FakeClock(1000.0)
    cache = JsonGeocodeCache(tmp_path / "c.json")
    geocode_address(ADDRESS, "", geocoder, cache, clock=clock)
    clock.now = 1059.0
    assert geocode_address(ADDRESS, "", geocoder, cache, clock=clock).precision == "none"
    assert len(attempts) == 1
    clock.now = 1061.0
    geocode_address(ADDRESS, "", geocoder, cache, clock=clock)
    assert len(attempts) == 2


def test_cached_variant_is_used_while_network_is_down(tmp_path, network_up):
    attempts = []
    cache = JsonGeocodeCache(tmp_path / "c.json")
    cache.put("Москва, Грайвороновская улица", GeoHit(55.72, 37.73, "highway"))
    result = geocode_address(ADDRESS, "", _unreachable_nominatim(attempts), cache, clock=FakeClock(0.0))
    assert (result.lat, result.precision) == (55.72, "street")
    assert len(attempts) == 1
