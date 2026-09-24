import httpx
import pytest

from app.ingest import geocode as geocode_module
from app.ingest.address import parse_address
from app.ingest.geocode import (
    NominatimGeocoder,
    ReverseAddress,
    ReverseGeocodeCache,
    ReverseHit,
    reverse_geocode,
    short_address,
)

PEROVSKAYA = {"house_number": "42 к1", "road": "Перовская улица", "city": "Москва", "state": "Москва"}
NO_ADDRESS = ReverseAddress(None, "none")


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class FakeReverse:
    def __init__(self, hit):
        self.hit = hit
        self.calls = []

    def reverse(self, lat, lon):
        self.calls.append((lat, lon))
        return self.hit


@pytest.fixture
def network_up(monkeypatch):
    monkeypatch.setattr(geocode_module, "_network_down_until", None)


def _nominatim(handler, **kwargs):
    kwargs.setdefault("sleep", lambda _: None)
    return NominatimGeocoder(client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_nominatim_reverse_sends_point_and_reads_address_parts():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"display_name": "42 к1, Перовская улица", "address": PEROVSKAYA})

    hit = _nominatim(handler).reverse(55.745478, 37.781916)
    assert hit == ReverseHit(city="Москва", road="Перовская улица", house_number="42 к1")
    request = seen[0]
    assert request.url.path == "/reverse"
    assert dict(request.url.params) == {
        "lat": "55.745478",
        "lon": "37.781916",
        "format": "jsonv2",
        "zoom": "18",
        "addressdetails": "1",
        "accept-language": "ru",
    }
    assert request.headers["User-Agent"].startswith("beeline-routing")


def test_nominatim_reverse_keeps_the_same_interval_as_search():
    def handler(request):
        if request.url.path == "/search":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"address": PEROVSKAYA})

    sleeps: list[float] = []
    ticks = iter([0.0, 0.3, 0.3])
    geocoder = _nominatim(handler, sleep=sleeps.append, clock=lambda: next(ticks))
    geocoder.lookup("Москва, Перовская улица, 42к1")
    geocoder.reverse(55.745478, 37.781916)
    assert len(sleeps) == 1 and abs(sleeps[0] - 0.8) < 1e-9


@pytest.mark.parametrize(
    ("address", "hit"),
    [
        (
            {"town": "Балашиха", "pedestrian": "Центральная аллея"},
            ReverseHit("Балашиха", "Центральная аллея"),
        ),
        (
            {"village": "деревня Горки", "footway": "Лесная тропа"},
            ReverseHit("деревня Горки", "Лесная тропа"),
        ),
        ({"municipality": "городской округ Кашира"}, ReverseHit("городской округ Кашира")),
        ({"residential": "Жилой квартал", "state": "Москва"}, ReverseHit("Москва", "Жилой квартал")),
        ({"city": "город Москва", "road": "Тверская улица"}, ReverseHit("Москва", "Тверская улица")),
        ({"city": "Москва", "town": "Зеленоград"}, ReverseHit("Москва")),
        ({"state": "Московская область", "country": "Россия"}, ReverseHit()),
    ],
)
def test_nominatim_reverse_maps_settlement_and_road_fields(address, hit):
    def handler(request):
        return httpx.Response(200, json={"address": address})

    assert _nominatim(handler).reverse(55.74, 37.78) == hit


def test_nominatim_reverse_without_result_returns_none():
    def handler(request):
        return httpx.Response(200, json={"error": "Unable to geocode"})

    assert _nominatim(handler).reverse(55.74, 37.78) is None


@pytest.mark.parametrize("address", ["Москва", ["Москва"], 42])
def test_nominatim_reverse_with_malformed_address_returns_none(address):
    def handler(request):
        return httpx.Response(200, json={"address": address})

    assert _nominatim(handler).reverse(55.74, 37.78) is None


@pytest.mark.parametrize(
    ("hit", "expected"),
    [
        (
            ReverseHit("Москва", "Перовская улица", "42 к1"),
            ReverseAddress("Москва, Перовская улица, 42к1", "house"),
        ),
        (
            ReverseHit("Москва", "Рязанский проспект", "10 с1"),
            ReverseAddress("Москва, Рязанский проспект, 10с1", "house"),
        ),
        (
            ReverseHit("Москва", "Шоссейная улица", "12 к2 с3"),
            ReverseAddress("Москва, Шоссейная улица, 12к2с3", "house"),
        ),
        (
            ReverseHit("Москва", "Шоссейная улица", "12 корпус 2"),
            ReverseAddress("Москва, Шоссейная улица, 12к2", "house"),
        ),
        (ReverseHit("Москва", "Тверская улица", " 7 "), ReverseAddress("Москва, Тверская улица, 7", "house")),
        (ReverseHit("Москва", "Тверская улица", "7;9"), ReverseAddress("Москва, Тверская улица, 7", "house")),
        (
            ReverseHit("Москва", "улица Ильинка", "12, корпус 2"),
            ReverseAddress("Москва, улица Ильинка, 12к2", "house"),
        ),
        (
            ReverseHit("Москва", "улица Ильинка", "12, строение 3;14"),
            ReverseAddress("Москва, улица Ильинка, 12с3", "house"),
        ),
        (ReverseHit("Москва", "Перовская улица"), ReverseAddress("Москва, Перовская улица", "street")),
        (ReverseHit(None, "Перовская улица"), ReverseAddress("Перовская улица", "street")),
        (ReverseHit("Балашиха"), ReverseAddress("Балашиха", "locality")),
        (ReverseHit(), NO_ADDRESS),
        (ReverseHit("", " ", ""), NO_ADDRESS),
        (None, NO_ADDRESS),
    ],
)
def test_short_address_and_precision(hit, expected):
    assert short_address(hit) == expected


def test_short_address_parses_back_into_street_and_house():
    found = short_address(ReverseHit("Москва", "Перовская улица", "42 к1"))
    assert found.address is not None
    parsed = parse_address(found.address)
    assert (parsed.city, parsed.street_type, parsed.street_name, parsed.house) == (
        "Москва",
        "улица",
        "Перовская",
        "42к1",
    )


def test_repeated_point_is_answered_from_cache(network_up):
    geocoder = FakeReverse(ReverseHit("Москва", "Перовская улица", "42 к1"))
    cache = ReverseGeocodeCache()
    first = reverse_geocode(55.745478, 37.781916, geocoder, cache)
    again = reverse_geocode(55.7454781, 37.7819159, geocoder, cache)
    assert first == again == ReverseAddress("Москва, Перовская улица, 42к1", "house")
    assert geocoder.calls == [(55.745478, 37.781916)]
    reverse_geocode(55.74549, 37.781916, geocoder, cache)
    assert len(geocoder.calls) == 2


def test_empty_answer_is_cached_too(network_up):
    geocoder = FakeReverse(None)
    cache = ReverseGeocodeCache()
    assert reverse_geocode(55.74, 37.78, geocoder, cache) == NO_ADDRESS
    assert reverse_geocode(55.74, 37.78, geocoder, cache) == NO_ADDRESS
    assert len(geocoder.calls) == 1


def test_cache_keeps_only_the_latest_points():
    cache = ReverseGeocodeCache(max_size=2)
    for index in range(3):
        cache.put(55.0 + index, 37.0, ReverseAddress(f"точка {index}", "locality"))
    assert cache.get(55.0, 37.0) is None
    assert cache.get(57.0, 37.0) == ReverseAddress("точка 2", "locality")


def test_without_geocoder_point_has_no_address(network_up):
    assert reverse_geocode(55.74, 37.78, None, ReverseGeocodeCache()) == NO_ADDRESS


def test_network_error_gives_no_address_and_pauses_reverse_lookups(network_up):
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        raise httpx.ConnectError("сеть недоступна", request=request)

    geocoder = _nominatim(handler, clock=lambda: 0.0)
    cache = ReverseGeocodeCache()
    clock = FakeClock(1000.0)
    assert reverse_geocode(55.74, 37.78, geocoder, cache, clock=clock) == NO_ADDRESS
    clock.now = 1059.0
    assert reverse_geocode(55.74, 37.78, geocoder, cache, clock=clock) == NO_ADDRESS
    assert attempts == ["/reverse"]
    clock.now = 1061.0
    reverse_geocode(55.74, 37.78, geocoder, cache, clock=clock)
    assert attempts == ["/reverse", "/reverse"]


def test_service_error_gives_no_address_and_is_not_cached(network_up):
    answers = iter(
        [
            httpx.Response(503),
            httpx.Response(200, content=b"<html>"),
            httpx.Response(200, json={"address": PEROVSKAYA}),
        ]
    )

    def handler(request):
        return next(answers)

    geocoder = _nominatim(handler, clock=lambda: 0.0)
    cache = ReverseGeocodeCache()
    assert reverse_geocode(55.74, 37.78, geocoder, cache) == NO_ADDRESS
    assert reverse_geocode(55.74, 37.78, geocoder, cache) == NO_ADDRESS
    assert reverse_geocode(55.74, 37.78, geocoder, cache).precision == "house"
