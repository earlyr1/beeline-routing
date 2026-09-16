import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.deps import build_deps
from app.ingest import geocode as geocode_module
from app.ingest.address import parse_address
from app.ingest.geocode import NominatimGeocoder, ReverseHit
from app.settings import Settings
from tests.api_helpers import make_client

URL = "/api/geocode/reverse"
NO_ADDRESS = {"address": None, "precision": "none"}


class PointGeocoder:
    """Поиск по адресу ничего не находит, по точке всегда отвечает заданным адресом."""

    def __init__(self, hit):
        self.hit = hit
        self.calls = []

    def lookup(self, query):
        return None

    def reverse(self, lat, lon):
        self.calls.append((lat, lon))
        return self.hit


@pytest.fixture
def network_up(monkeypatch):
    monkeypatch.setattr(geocode_module, "_network_down_until", None)


@pytest.mark.parametrize(
    ("hit", "expected"),
    [
        (
            ReverseHit("Москва", "Перовская улица", "42 к1"),
            {"address": "Москва, Перовская улица, 42к1", "precision": "house"},
        ),
        (
            ReverseHit("Москва", "Перовская улица"),
            {"address": "Москва, Перовская улица", "precision": "street"},
        ),
        (ReverseHit("Балашиха"), {"address": "Балашиха", "precision": "locality"}),
        (ReverseHit(), NO_ADDRESS),
        (None, NO_ADDRESS),
    ],
)
def test_reverse_returns_short_address_and_precision(tmp_path, network_up, hit, expected):
    client, _ = make_client(tmp_path, geocoder=PointGeocoder(hit))
    response = client.get(URL, params={"lat": 55.745478, "lon": 37.781916})
    assert response.status_code == 200, response.text
    assert response.json() == expected


def test_returned_address_parses_back_into_street_and_house(tmp_path, network_up):
    client, _ = make_client(
        tmp_path, geocoder=PointGeocoder(ReverseHit("Москва", "Перовская улица", "42 к1"))
    )
    address = client.get(URL, params={"lat": 55.745478, "lon": 37.781916}).json()["address"]
    assert address == "Москва, Перовская улица, 42к1"
    parsed = parse_address(address)
    assert (parsed.street_name, parsed.house) == ("Перовская", "42к1")


def test_repeated_click_does_not_call_geocoder_again(tmp_path, network_up):
    geocoder = PointGeocoder(ReverseHit("Москва", "Перовская улица", "42 к1"))
    client, _ = make_client(tmp_path, geocoder=geocoder)
    first = client.get(URL, params={"lat": 55.745478, "lon": 37.781916})
    second = client.get(URL, params={"lat": 55.7454781, "lon": 37.781916})
    assert first.json() == second.json()
    assert geocoder.calls == [(55.745478, 37.781916)]


@pytest.mark.parametrize(("lat", "lon"), [(59.93, 30.31), (55.74, 41.0), ("nan", 37.78)])
def test_point_outside_moscow_region_is_rejected(tmp_path, lat, lon):
    geocoder = PointGeocoder(ReverseHit("Санкт-Петербург", "Невский проспект", "1"))
    client, _ = make_client(tmp_path, geocoder=geocoder)
    response = client.get(URL, params={"lat": lat, "lon": lon})
    assert response.status_code == 422
    assert response.json()["detail"] == "Точка вне Москвы и Московской области."
    assert geocoder.calls == []


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ({"lon": 37.78}, "Некорректный запрос: lat: обязательное поле"),
        ({"lat": "север", "lon": 37.78}, "Некорректный запрос: lat: нужно число"),
        ({"lat": 55.74}, "Некорректный запрос: lon: обязательное поле"),
    ],
)
def test_missing_or_non_numeric_coordinates_are_russian_422(tmp_path, params, detail):
    client, _ = make_client(tmp_path, geocoder=PointGeocoder(ReverseHit("Москва")))
    response = client.get(URL, params=params)
    assert response.status_code == 422
    assert response.json()["detail"] == detail


def test_geocoder_without_reverse_lookup_gives_no_address(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get(URL, params={"lat": 55.74, "lon": 37.78})
    assert response.status_code == 200 and response.json() == NO_ADDRESS


def test_cache_only_geocoder_gives_no_address(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        cache_path=tmp_path / "cache.sqlite",
        transit_dir=tmp_path / "transit",
        osrm_url=None,
        yandex_maps_api_key=None,
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
        geocoder="cache-only",
        solver_time_limit_s=1,
    )
    client = TestClient(create_app(build_deps(settings)))
    response = client.get(URL, params={"lat": 55.74, "lon": 37.78})
    assert response.status_code == 200 and response.json() == NO_ADDRESS


def test_unreachable_nominatim_gives_no_address(tmp_path, network_up):
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        raise httpx.ConnectError("сеть недоступна", request=request)

    geocoder = NominatimGeocoder(
        client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None
    )
    client, _ = make_client(tmp_path, geocoder=geocoder)
    response = client.get(URL, params={"lat": 55.74, "lon": 37.78})
    assert response.status_code == 200 and response.json() == NO_ADDRESS
    again = client.get(URL, params={"lat": 55.75, "lon": 37.78})
    assert again.json() == NO_ADDRESS
    assert attempts == ["/reverse"]
