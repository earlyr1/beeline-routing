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


def test_manual_address_finds_the_house_query_of_export_format(tmp_path):
    geocoder = FakeGeocoder({"Москва, Перовская улица, 42к1": GeoHit(55.74548, 37.78192, "building")})
    result = geocode_address(
        "Москва, Перовская улица 42к1", "", geocoder, JsonGeocodeCache(tmp_path / "c.json")
    )
    assert result == GeoResult(55.74548, 37.78192, "house", "Москва, Перовская улица, 42к1")
    assert geocoder.calls == ["Москва, Перовская улица, 42к1"]


NO_STREET_TYPE = "Москва, Перовская, 42к1"


@pytest.mark.parametrize(
    ("category", "place_rank", "precision"),
    [
        ("building", 30, "house"),
        ("place", 28, "house"),
        ("highway", 26, "street"),
        ("highway", 30, "street"),
        ("landuse", 27, "street"),
        ("place", 16, "locality"),
        ("boundary", 14, "locality"),
    ],
)
def test_raw_address_precision_comes_from_place_rank(tmp_path, category, place_rank, precision):
    geocoder = FakeGeocoder({NO_STREET_TYPE: GeoHit(55.74, 37.78, category, place_rank)})
    result = geocode_address(NO_STREET_TYPE, "", geocoder, JsonGeocodeCache(tmp_path / "c.json"))
    assert result == GeoResult(55.74, 37.78, precision, NO_STREET_TYPE)


@pytest.mark.parametrize(
    ("category", "precision"),
    [
        ("building", "house"),
        ("place", "house"),
        ("highway", "street"),
        ("boundary", "locality"),
        ("", "locality"),
    ],
)
def test_raw_address_precision_from_old_cache_entry_uses_category(tmp_path, category, precision):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({NO_STREET_TYPE: [55.74, 37.78, category]}), encoding="utf-8")
    result = geocode_address(NO_STREET_TYPE, "", None, JsonGeocodeCache(path))
    assert result == GeoResult(55.74, 37.78, precision, NO_STREET_TYPE)


def test_raw_address_hit_outside_region_is_skipped(tmp_path):
    geocoder = FakeGeocoder(
        {
            NO_STREET_TYPE: GeoHit(59.93, 30.31, "building", 30),
            "район Перово, Москва": GeoHit(55.74, 37.77, "boundary", 14),
        }
    )
    result = geocode_address(NO_STREET_TYPE, "Перово", geocoder, JsonGeocodeCache(tmp_path / "c.json"))
    assert result == GeoResult(55.74, 37.77, "locality", "район Перово, Москва")


SAMARKANDSKY = "Город Москва, б-р.Самаркандский Квартал 137а, д. к5"


def test_coarse_raw_address_hit_does_not_beat_a_street_variant(tmp_path):
    geocoder = FakeGeocoder(
        {
            SAMARKANDSKY: GeoHit(55.75, 37.62, "place", 16),
            "Москва, Самаркандский бульвар": GeoHit(55.70, 37.82, "highway", 26),
        }
    )
    result = geocode_address(SAMARKANDSKY, "Выхино", geocoder, JsonGeocodeCache(tmp_path / "c.json"))
    assert result == GeoResult(55.70, 37.82, "street", "Москва, Самаркандский бульвар")


def test_coarse_raw_address_hit_is_kept_when_nothing_more_precise_is_found(tmp_path):
    geocoder = FakeGeocoder(
        {
            SAMARKANDSKY: GeoHit(55.71, 37.81, "boundary", 16),
            "район Выхино, Москва": GeoHit(55.70, 37.82, "boundary", 14),
        }
    )
    result = geocode_address(SAMARKANDSKY, "Выхино", geocoder, JsonGeocodeCache(tmp_path / "c.json"))
    assert result == GeoResult(55.71, 37.81, "locality", SAMARKANDSKY)
    assert geocoder.calls == [
        SAMARKANDSKY,
        "Москва, Самаркандский бульвар",
        "Москва, бульвар Самаркандский",
        "район Выхино, Москва",
    ]


def test_cache_round_trip_with_and_without_place_rank(tmp_path):
    path = tmp_path / "cache.json"
    cache = JsonGeocodeCache(path)
    cache.put("дом", GeoHit(55.74, 37.78, "building", 30))
    cache.put("улица", GeoHit(55.75, 37.77, "highway"))
    cache.put("промах", None)
    cache.save()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "дом": [55.74, 37.78, "building", 30],
        "улица": [55.75, 37.77, "highway"],
        "промах": None,
    }
    loaded = JsonGeocodeCache(path)
    assert loaded.get("дом") == (True, GeoHit(55.74, 37.78, "building", 30))
    assert loaded.get("улица") == (True, GeoHit(55.75, 37.77, "highway", None))
    assert loaded.get("промах") == (True, None)


def test_nominatim_client_reads_place_rank():
    def handler(request):
        return httpx.Response(
            200, json=[{"lat": "55.745478", "lon": "37.781916", "category": "building", "place_rank": 30}]
        )

    geocoder = NominatimGeocoder(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert geocoder.lookup("Москва, Перовская улица, 42к1") == GeoHit(55.745478, 37.781916, "building", 30)


def test_cached_variant_is_used_while_network_is_down(tmp_path, network_up):
    attempts = []
    cache = JsonGeocodeCache(tmp_path / "c.json")
    cache.put("Москва, Грайвороновская улица", GeoHit(55.72, 37.73, "highway"))
    result = geocode_address(ADDRESS, "", _unreachable_nominatim(attempts), cache, clock=FakeClock(0.0))
    assert (result.lat, result.precision) == (55.72, "street")
    assert len(attempts) == 1
