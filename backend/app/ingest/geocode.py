"""Геокодирование через Nominatim с кэшем в JSON-файле (кэш коммитится в git)."""

from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from app.ingest.address import normalize_house, parse_address, query_variants

# min_lat, min_lon, max_lat, max_lon: Москва и Московская область
MOSCOW_REGION_BBOX = (54.2, 35.1, 57.0, 40.3)
NOMINATIM_TIMEOUT_S = 5.0
# После сетевой ошибки геокодер не вызывается минуту: адреса ищутся только в кэше и не ждут таймаутов.
NETWORK_RETRY_S = 60.0
_network_down_until: float | None = None
# Ранг объекта Nominatim (place_rank): 30 — дом или адресная точка, 26–27 — улица, 16 — город.
HOUSE_PLACE_RANK = 28
STREET_PLACE_RANK = 26
_PRECISION_ORDER = {"locality": 0, "street": 1, "house": 2}
# Поля адреса в ответе Nominatim /reverse по убыванию важности: населённый пункт и улица.
REVERSE_CITY_FIELDS = ("city", "town", "village", "municipality")
REVERSE_ROAD_FIELDS = ("road", "pedestrian", "footway", "residential")
# Координаты точки округляются до 5 знаков (около метра): повторный клик рядом берётся из кэша.
REVERSE_CACHE_DIGITS = 5
REVERSE_CACHE_SIZE = 512
_CITY_PREFIX = re.compile(r"^(?:город\s+|г\.\s*)", re.IGNORECASE)


@dataclass(frozen=True)
class GeoHit:
    lat: float
    lon: float
    category: str = ""
    place_rank: int | None = None


@dataclass(frozen=True)
class GeoResult:
    lat: float | None
    lon: float | None
    precision: str
    query: str | None


@dataclass(frozen=True)
class ReverseHit:
    """Части адреса точки из обратного геокодирования; неизвестная часть — None."""

    city: str | None = None
    road: str | None = None
    house_number: str | None = None


@dataclass(frozen=True)
class ReverseAddress:
    """Короткий адрес точки («Москва, Перовская улица, 42к1») и его точность; без адреса — (None, "none")."""

    address: str | None
    precision: str


NO_ADDRESS = ReverseAddress(None, "none")


class Geocoder(Protocol):
    def lookup(self, query: str) -> GeoHit | None: ...


@runtime_checkable
class ReverseGeocoder(Protocol):
    def reverse(self, lat: float, lon: float) -> ReverseHit | None: ...


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

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        """GET к Nominatim не чаще раза в min_interval_s: интервал общий для поиска по адресу и по точке."""
        if self._last_call is not None:
            wait = self._min_interval_s - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        try:
            response = self._client.get(
                f"{self._base_url}/{path}", params=params, headers={"User-Agent": self._user_agent}
            )
        finally:
            self._last_call = self._clock()
        response.raise_for_status()
        return response.json()

    def lookup(self, query: str) -> GeoHit | None:
        min_lat, min_lon, max_lat, max_lon = MOSCOW_REGION_BBOX
        items = self._get(
            "search",
            {
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "ru",
                "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}",
                "bounded": 1,
            },
        )
        if not items:
            return None
        item = items[0]
        rank = item.get("place_rank")
        return GeoHit(
            float(item["lat"]),
            float(item["lon"]),
            str(item.get("category", "")),
            None if rank is None else int(rank),
        )

    def reverse(self, lat: float, lon: float) -> ReverseHit | None:
        """Адрес ближайшего к точке объекта; None, если Nominatim ничего не нашёл."""
        data = self._get(
            "reverse",
            {
                "lat": lat,
                "lon": lon,
                "format": "jsonv2",
                "zoom": 18,
                "addressdetails": 1,
                "accept-language": "ru",
            },
        )
        if not isinstance(data, dict) or "error" in data:
            return None
        return reverse_hit(data.get("address") or {})


def _first(address: Mapping[str, Any], fields: tuple[str, ...]) -> str | None:
    for name in fields:
        value = str(address.get(name) or "").strip()
        if value:
            return value
    return None


def reverse_hit(address: Mapping[str, Any]) -> ReverseHit:
    """Части адреса из поля address ответа Nominatim. Москва без города в ответе узнаётся по субъекту (state)."""
    city = _first(address, REVERSE_CITY_FIELDS)
    if city is not None:
        city = _CITY_PREFIX.sub("", city) or city
    elif str(address.get("state") or "").strip() == "Москва":
        city = "Москва"
    return ReverseHit(city, _first(address, REVERSE_ROAD_FIELDS), _first(address, ("house_number",)))


def short_address(hit: ReverseHit | None) -> ReverseAddress:
    """Короткий адрес, который геокодер разбирает обратно: «Москва, Перовская улица, 42к1».

    Номер дома пишется без пробелов перед корпусом и строением («42 к1» -> «42к1»), из списка домов («7;9»)
    берётся первый. Недостающие части пропускаются; точность — по самой точной известной части.
    """
    hit = hit or ReverseHit()
    city = (hit.city or "").strip()
    road = (hit.road or "").strip()
    house = re.split(r"[,;]", hit.house_number or "")[0].strip()
    if house:
        house = normalize_house(house) or re.sub(r"\s+", " ", house)
    parts = [part for part in (city, road, house) if part]
    if not parts:
        return NO_ADDRESS
    precision = "house" if house else "street" if road else "locality"
    return ReverseAddress(", ".join(parts), precision)


class ReverseGeocodeCache:
    """Кэш адресов точек в памяти: ключ — координаты, округлённые до REVERSE_CACHE_DIGITS знаков.

    Хранит последние max_size точек; пустой ответ геокодера тоже кэшируется, ошибки сети и сервиса — нет.
    """

    def __init__(self, max_size: int = REVERSE_CACHE_SIZE) -> None:
        self._max_size = max_size
        self._data: OrderedDict[tuple[float, float], ReverseAddress] = OrderedDict()

    @staticmethod
    def _key(lat: float, lon: float) -> tuple[float, float]:
        return round(lat, REVERSE_CACHE_DIGITS), round(lon, REVERSE_CACHE_DIGITS)

    def get(self, lat: float, lon: float) -> ReverseAddress | None:
        key = self._key(lat, lon)
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, lat: float, lon: float, value: ReverseAddress) -> None:
        key = self._key(lat, lon)
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)


class JsonGeocodeCache:
    """запрос -> [lat, lon, category, place_rank] или null (промах тоже кэшируется).

    place_rank пишется, только если он известен; старые записи [lat, lon, category] читаются с place_rank=None.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._data: dict[str, list | None] = {}
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def get(self, query: str) -> tuple[bool, GeoHit | None]:
        if query not in self._data:
            return False, None
        value = self._data[query]
        if value is None:
            return True, None
        return True, GeoHit(value[0], value[1], value[2], value[3] if len(value) > 3 else None)

    def put(self, query: str, hit: GeoHit | None) -> None:
        if hit is None:
            self._data[query] = None
            return
        rank = [] if hit.place_rank is None else [hit.place_rank]
        self._data[query] = [hit.lat, hit.lon, hit.category, *rank]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
        )


def _network_down(clock: Callable[[], float]) -> bool:
    return _network_down_until is not None and clock() < _network_down_until


def auto_precision(hit: GeoHit) -> str:
    """Точность попадания по адресу как его ввели: по рангу объекта, для старых записей кэша — по категории."""
    if hit.category == "highway":
        return "street"
    if hit.place_rank is None:
        return "house" if hit.category in ("building", "place") else "locality"
    if hit.place_rank >= HOUSE_PLACE_RANK:
        return "house"
    return "street" if hit.place_rank >= STREET_PLACE_RANK else "locality"


def geocode_address(
    raw: str,
    district: str,
    geocoder: Geocoder | None,
    cache: JsonGeocodeCache,
    clock: Callable[[], float] = time.monotonic,
) -> GeoResult:
    """Перебирает варианты запроса от точного к грубому; ошибки сети и сервиса не кэшируются.

    После сетевой ошибки оставшиеся варианты и следующие адреса NETWORK_RETRY_S секунд ищутся только в кэше.
    Точность варианта "auto" определяет найденный объект. Если это не дом, поиск идёт дальше: улица из разбора
    точнее района, а при равной или худшей точности остаётся ответ на адрес как его ввели.
    """
    global _network_down_until
    coarse: GeoResult | None = None
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
        if precision == "auto":
            precision = auto_precision(hit)
            if precision != "house":
                coarse = GeoResult(hit.lat, hit.lon, precision, query)
                continue
        elif precision == "house" and hit.category == "highway":
            precision = "street"
        if coarse is not None and _PRECISION_ORDER[coarse.precision] >= _PRECISION_ORDER[precision]:
            return coarse
        return GeoResult(hit.lat, hit.lon, precision, query)
    return coarse or GeoResult(None, None, "none", None)


def reverse_geocode(
    lat: float,
    lon: float,
    geocoder: ReverseGeocoder | None,
    cache: ReverseGeocodeCache,
    clock: Callable[[], float] = time.monotonic,
) -> ReverseAddress:
    """Адрес точки для ручной заявки: из кэша, иначе у геокодера.

    Без геокодера (GEOCODER=cache-only), при ошибке сервиса и NETWORK_RETRY_S секунд после сетевой ошибки
    (общее окно с поиском по адресу) точка остаётся без адреса; такие ответы не кэшируются.
    """
    global _network_down_until
    cached = cache.get(lat, lon)
    if cached is not None:
        return cached
    if geocoder is None or _network_down(clock):
        return NO_ADDRESS
    try:
        hit = geocoder.reverse(lat, lon)
    except httpx.TransportError:
        _network_down_until = clock() + NETWORK_RETRY_S
        return NO_ADDRESS
    except (httpx.HTTPError, ValueError):
        return NO_ADDRESS
    result = short_address(hit)
    cache.put(lat, lon, result)
    return result
