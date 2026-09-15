import json

import httpx

from app.ingest.geocode import GeoHit, JsonGeocodeCache, NominatimGeocoder, geocode_address


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
