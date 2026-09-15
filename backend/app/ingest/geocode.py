"""Геокодирование через Nominatim с кэшем в JSON-файле (кэш коммитится в git)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from app.ingest.address import parse_address, query_variants

# min_lat, min_lon, max_lat, max_lon: Москва и Московская область
MOSCOW_REGION_BBOX = (54.2, 35.1, 57.0, 40.3)
NOMINATIM_TIMEOUT_S = 5.0
# После сетевой ошибки геокодер не вызывается минуту: адреса ищутся только в кэше и не ждут таймаутов.
NETWORK_RETRY_S = 60.0
_network_down_until: float | None = None


@dataclass(frozen=True)
class GeoHit:
    lat: float
    lon: float
    category: str = ""


@dataclass(frozen=True)
class GeoResult:
    lat: float | None
    lon: float | None
    precision: str
    query: str | None


class Geocoder(Protocol):
    def lookup(self, query: str) -> GeoHit | None: ...


def in_region(lat: float, lon: float) -> bool:
    min_lat, min_lon, max_lat, max_lon = MOSCOW_REGION_BBOX
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


class NominatimGeocoder:
    def __init__(
        self,
        base_url: str = "https://nominatim.openstreetmap.org",
        user_agent: str = "beeline-routing-hackathon/0.1",
        min_interval_s: float = 1.1,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._min_interval_s = min_interval_s
        self._client = client or httpx.Client(timeout=NOMINATIM_TIMEOUT_S)
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None

    def lookup(self, query: str) -> GeoHit | None:
        if self._last_call is not None:
            wait = self._min_interval_s - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        min_lat, min_lon, max_lat, max_lon = MOSCOW_REGION_BBOX
        response = self._client.get(
            f"{self._base_url}/search",
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "ru",
                "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}",
                "bounded": 1,
            },
            headers={"User-Agent": self._user_agent},
        )
        self._last_call = self._clock()
        response.raise_for_status()
        items = response.json()
        if not items:
            return None
        item = items[0]
        return GeoHit(float(item["lat"]), float(item["lon"]), str(item.get("category", "")))


class JsonGeocodeCache:
    """запрос -> [lat, lon, category] или null (промах тоже кэшируется)."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._data: dict[str, list | None] = {}
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def get(self, query: str) -> tuple[bool, GeoHit | None]:
        if query not in self._data:
            return False, None
        value = self._data[query]
        return True, None if value is None else GeoHit(value[0], value[1], value[2])

    def put(self, query: str, hit: GeoHit | None) -> None:
        self._data[query] = None if hit is None else [hit.lat, hit.lon, hit.category]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
        )


def _network_down(clock: Callable[[], float]) -> bool:
    return _network_down_until is not None and clock() < _network_down_until


def geocode_address(
    raw: str,
    district: str,
    geocoder: Geocoder | None,
    cache: JsonGeocodeCache,
    clock: Callable[[], float] = time.monotonic,
) -> GeoResult:
    """Перебирает варианты запроса от точного к грубому; ошибки сети и сервиса не кэшируются.

    После сетевой ошибки оставшиеся варианты и следующие адреса NETWORK_RETRY_S секунд ищутся только в кэше.
    """
    global _network_down_until
    for query, precision in query_variants(parse_address(raw), district):
        found, hit = cache.get(query)
        if not found:
            if geocoder is None or _network_down(clock):
                continue
            try:
                hit = geocoder.lookup(query)
            except httpx.TransportError:
                _network_down_until = clock() + NETWORK_RETRY_S
                continue
            except httpx.HTTPError:
                continue
            cache.put(query, hit)
        if hit is None or not in_region(hit.lat, hit.lon):
            continue
        if precision == "house" and hit.category == "highway":
            precision = "street"
        return GeoResult(hit.lat, hit.lon, precision, query)
    return GeoResult(None, None, "none", None)
