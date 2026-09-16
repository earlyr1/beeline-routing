"""Генератор региона без реальной выгрузки: адреса OpenStreetMap, выдуманные бригады, распределение «диспетчера».

Регион собирается так же, как настоящие: скрипт пишет пару CSV в формате выгрузки Билайна (контрольное
распределение и синтетические данные), дальше работает обычный prepare. Адреса выгружаются из Overpass один раз
и лежат в репозитории, поэтому генерация повторяется без сети и даёт те же файлы.

Запуск из каталога backend:
  python -m app.synth.generate_region --spec config/regions/north_west.yaml --fetch
  python -m app.synth.generate_region --spec config/regions/north_west.yaml
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

from app.domain.enums import Transport
from app.geo.haversine import haversine_km
from app.ingest.address import parse_address, query_variants
from app.ingest.beeline_csv import decode_bytes, parse_beeline_csv
from app.ingest.geocode import GeoHit, JsonGeocodeCache
from app.settings import BACKEND_DIR, REPO_ROOT
from app.synth.config import SynthConfig
from app.synth.requests import synth_duration, synth_transport_required

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "beeline-routing-hackathon/0.1"
OVERPASS_PAUSE_S = 3.0
POOL_LIMIT = 150

CONTROL_HEADER = [
    "Заявка",
    "Тип заявки BK",
    "Статус BK",
    "Тип заявки HD",
    "Начало",
    "Окончание",
    "Район",
    "Адрес",
    "Бригада",
    "Подключение",
    "Гигабитное подключение",
]
SYNTHETIC_HEADER = [column for column in CONTROL_HEADER if column not in ("Статус BK", "Бригада")]
NOT_SENT = "Не отправлена"
OFFICE_MARKER = "Адрес Офиса"

SHIFT_START_MIN, SHIFT_END_MIN = 10 * 60, 22 * 60
# Грубая оценка дороги «диспетчера»: по прямой ×1.3, 25 км/ч и дневные пробки ×1.4.
DETOUR, SPEED_KMH, TRAFFIC = 1.3, 25.0, 1.4
MAX_VISITS_PER_CREW = 13

# Жилые здания и адресные точки без типа здания; школы, офисы и склады в заявки не попадают.
RESIDENTIAL_BUILDINGS = {None, "apartments", "residential", "house", "detached", "dormitory", "yes"}
FLAT_BUILDINGS = {"apartments", "residential", "dormitory"}
STREET_TYPES = {
    "улица": "ул.",
    "проспект": "пр-кт.",
    "бульвар": "б-р.",
    "набережная": "наб.",
    "шоссе": "ш.",
    "переулок": "пер.",
    "проезд": "проезд.",
    "площадь": "пл.",
    "тупик": "туп.",
}
_ORDINAL = re.compile(r"^\d+-[йяе]$")
_HOUSE = re.compile(
    r"^(?P<base>\d+[А-Яа-яЁё]?(?:/\d+[А-Яа-яЁё]?)?)(?:\s*к\s*(?P<k>\d+))?(?:\s*с\s*(?P<s>\d+))?$"
)


class Around(BaseModel):
    lat: float
    lon: float
    radius_m: int


class DistrictSpec(BaseModel):
    id: str
    name: str
    count: int
    osm_area: str | None = None
    around: Around | None = None

    @model_validator(mode="after")
    def _one_source(self) -> DistrictSpec:
        if (self.osm_area is None) == (self.around is None):
            raise ValueError(f"у района {self.id} нужен ровно один источник адресов: osm_area или around")
        return self


class CrewSpec(BaseModel):
    name: str
    districts: list[str]
    car: bool = False


class OfficeSpec(BaseModel):
    district: str
    street: str | None = None


class BBox(BaseModel):
    """Рамка региона: адреса за её пределами отбрасываются (у районов бывают тёзки в других городах)."""

    south: float
    west: float
    north: float
    east: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east


class RegionSpec(BaseModel):
    region: str
    title: str
    date: str
    seed: int
    not_sent: int = 0
    addresses_file: str
    control_file: str
    synthetic_file: str
    empirical_from: list[str]
    office: OfficeSpec
    districts: list[DistrictSpec]
    crews: list[CrewSpec]
    bbox: BBox | None = None

    @model_validator(mode="after")
    def _consistent(self) -> RegionSpec:
        ids = [district.id for district in self.districts]
        if len(ids) != len(set(ids)):
            raise ValueError("повторяются id районов")
        unknown = sorted({d for crew in self.crews for d in crew.districts} - set(ids))
        if unknown:
            raise ValueError(f"бригады ссылаются на неизвестные районы: {', '.join(unknown)}")
        uncovered = sorted(set(ids) - {d for crew in self.crews for d in crew.districts})
        if uncovered:
            raise ValueError(f"районы без бригад: {', '.join(uncovered)}")
        if self.office.district not in ids:
            raise ValueError(f"район офиса {self.office.district} не описан")
        return self

    @classmethod
    def load(cls, path: Path) -> RegionSpec:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def beeline_street(street: str) -> str | None:
    """Улица OSM в записи выгрузки Билайна: «3-я Хорошёвская улица» -> «ул.Хорошевская 3-я»; неизвестный тип -> None."""
    words = street.replace("ё", "е").replace("Ё", "Е").split()
    if len(words) < 2:
        return None
    for index in (0, len(words) - 1):
        abbreviation = STREET_TYPES.get(words[index].lower())
        if abbreviation is not None:
            name = words[:index] + words[index + 1 :]
            break
    else:
        return None
    if _ORDINAL.match(name[0]):
        name = name[1:] + name[:1]
    return f"{abbreviation}{' '.join(name)}"


def beeline_house(house: str) -> str | None:
    """Номер дома OSM в записи выгрузки: «35 к1» -> «д. 35 к 1», «37А с4» -> «д. 37А стр. 4»; списки и диапазоны -> None."""
    match = _HOUSE.match(house.strip())
    if match is None:
        return None
    text = f"д. {match['base']}"
    if match["k"]:
        text += f" к {match['k']}"
    if match["s"]:
        text += f" стр. {match['s']}"
    return text


def beeline_address(street: str, house: str) -> str | None:
    """Адрес выгрузки, который наш разбор понимает до дома; иначе None."""
    street_text, house_text = beeline_street(street), beeline_house(house)
    if street_text is None or house_text is None:
        return None
    address = f"Город Москва, {street_text}, {house_text}"
    parsed = parse_address(address)
    return address if parsed.street_type and parsed.house else None


def pool_from_elements(
    elements: list[dict], key: str, limit: int = POOL_LIMIT, bbox: BBox | None = None
) -> list[dict]:
    """Жилые адреса из ответа Overpass без повторов, в устойчивом порядке; не больше limit."""
    unique: dict[str, dict] = {}
    for element in elements:
        tags = element.get("tags", {})
        building = tags.get("building")
        if building not in RESIDENTIAL_BUILDINGS:
            continue
        street, house = tags.get("addr:street"), tags.get("addr:housenumber")
        lat = element.get("lat", element.get("center", {}).get("lat"))
        lon = element.get("lon", element.get("center", {}).get("lon"))
        if not street or not house or lat is None or lon is None:
            continue
        if bbox is not None and not bbox.contains(float(lat), float(lon)):
            continue
        address = beeline_address(street, house)
        if address is None or address in unique:
            continue
        unique[address] = {
            "address": address,
            "flat": building in FLAT_BUILDINGS,
            "lat": round(float(lat), 6),
            "lon": round(float(lon), 6),
            "osm_street": street,
            "osm_house": house,
        }
    entries = sorted(unique.values(), key=lambda entry: entry["address"])
    if len(entries) > limit:
        entries = sorted(
            random.Random(f"pool:{key}").sample(entries, limit), key=lambda entry: entry["address"]
        )
    return entries


def _overpass(query: str) -> list[dict]:
    body = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(4):
        request = urllib.request.Request(OVERPASS_URL, data=body, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=200) as response:
                return json.loads(response.read())["elements"]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
            if attempt == 3:
                raise
            time.sleep(20 * (attempt + 1))
    return []


def district_query(district: DistrictSpec, bbox: BBox | None = None) -> str:
    if district.osm_area is not None:
        scope = "(area.a)"
        prefix = f'area["name"="{district.osm_area}"]["admin_level"="8"]->.a;'
    else:
        around = district.around
        scope = f"(around:{around.radius_m},{around.lat},{around.lon})"
        prefix = ""
    setting = f"[bbox:{bbox.south},{bbox.west},{bbox.north},{bbox.east}]" if bbox is not None else ""
    return (
        f"[out:json][timeout:190]{setting};{prefix}"
        f'(way["building"]["addr:street"]["addr:housenumber"]{scope};'
        f'node["addr:street"]["addr:housenumber"]{scope};);out center tags;'
    )


def fetch_addresses(
    spec: RegionSpec, only: set[str] | None = None, existing: dict[str, list[dict]] | None = None
) -> dict[str, list[dict]]:
    """Выгружает адреса районов из Overpass; при only — только эти районы, остальные берутся из existing."""
    pools: dict[str, list[dict]] = dict(existing or {})
    for district in spec.districts:
        if only is not None and district.id not in only:
            continue
        pools[district.id] = pool_from_elements(
            _overpass(district_query(district, spec.bbox)), f"{spec.region}:{district.id}", bbox=spec.bbox
        )
        print(f"{district.id}: {len(pools[district.id])} адресов", flush=True)
        time.sleep(OVERPASS_PAUSE_S)
    return pools


Weighted = tuple[list, list[int]]


@dataclass
class Empirical:
    """Распределения реальных контрольных файлов: пары типов BK/HD, окна, статусы, колонки подключения."""

    pairs: Weighted
    windows_by_urgency: dict[bool, Weighted]
    statuses: Weighted
    connection_by_bk: dict[str, Weighted] = field(default_factory=dict)


def _weighted(counter: Counter) -> Weighted:
    items = sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))
    return [item[0] for item in items], [item[1] for item in items]


def load_empirical(paths: list[Path], cfg: SynthConfig) -> Empirical:
    pairs: Counter = Counter()
    windows: dict[bool, Counter] = {True: Counter(), False: Counter()}
    statuses: Counter = Counter()
    connections: dict[str, Counter] = {}
    for path in paths:
        for record in csv.DictReader(io.StringIO(decode_bytes(Path(path).read_bytes())), delimiter=";"):
            request_id = (record.get("Заявка") or "").strip()
            if not request_id or request_id.lower() == OFFICE_MARKER.lower():
                continue
            bk, hd = record["Тип заявки BK"].strip(), record["Тип заявки HD"].strip()
            pairs[(bk, hd)] += 1
            window = (record["Начало"].split()[-1], record["Окончание"].split()[-1])
            windows[bk in cfg.urgent_bk_types][window] += 1
            status = (record.get("Статус BK") or "").strip()
            if status and status != NOT_SENT:
                statuses[status] += 1
            connection = (
                (record.get("Подключение") or "").strip(),
                (record.get("Гигабитное подключение") or "").strip(),
            )
            connections.setdefault(bk, Counter())[connection] += 1
    return Empirical(
        pairs=_weighted(pairs),
        windows_by_urgency={urgent: _weighted(counter) for urgent, counter in windows.items()},
        statuses=_weighted(statuses),
        connection_by_bk={bk: _weighted(counter) for bk, counter in connections.items()},
    )


def _minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


@dataclass
class _Draft:
    district: DistrictSpec
    entry: dict
    type_bk: str
    type_hd: str
    start: str
    end: str
    status: str
    connection: tuple[str, str]
    flat: str
    synthetic_id: str = ""
    control_id: str = ""
    crew: str = ""


@dataclass
class _CrewState:
    spec: CrewSpec
    lat: float
    lon: float
    time_min: int = SHIFT_START_MIN
    visits: int = 0


@dataclass
class GeneratedRegion:
    control_csv: str
    synthetic_csv: str
    office_address: str
    late_by_estimate: int
    visits_by_crew: dict[str, int]
    # (адрес, район, широта, долгота) каждой заявки и офиса: точки домов OpenStreetMap для кэша геокодера
    points: list[tuple[str, str, float, float]] = field(default_factory=list)


def _office(spec: RegionSpec, pools: dict[str, list[dict]]) -> tuple[str, dict]:
    entries = pools[spec.office.district]
    if not entries:
        raise ValueError(f"в районе офиса {spec.office.district} нет адресов")
    matching = [
        entry for entry in entries if spec.office.street and entry["osm_street"] == spec.office.street
    ]
    entry = (matching or entries)[0]
    return entry["address"].replace("Город Москва", "г. Москва", 1), entry


def _unique_ids(rng: random.Random, count: int, low: int, high: int, taken: set[str]) -> list[str]:
    ids: list[str] = []
    seen = set(taken)
    while len(ids) < count:
        value = str(rng.randrange(low, high))
        if value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def _estimate(crew: _CrewState, draft: _Draft, duration: int) -> tuple[int, float, int]:
    """Грубая оценка диспетчера: (опоздание к окну или за смену, минут в пути, уже назначено заявок)."""
    km = haversine_km(crew.lat, crew.lon, draft.entry["lat"], draft.entry["lon"]) * DETOUR
    travel = km / SPEED_KMH * 60 * TRAFFIC
    start = max(crew.time_min + travel, _minutes(draft.start), SHIFT_START_MIN)
    lateness = max(0.0, start - _minutes(draft.end)) + max(0.0, start + duration - SHIFT_END_MIN)
    return round(lateness), travel, crew.visits


def _dispatch(spec: RegionSpec, cfg: SynthConfig, drafts: list[_Draft], pools: dict[str, list[dict]]) -> int:
    """Раздаёт заявки бригадам, как диспетчер: своя территория и успеть в окно, иначе ближайшая свободная бригада.

    Аварии и дозаказ оборудования достаются только бригадам с машиной. Возвращает число заявок, в окно которых
    бригада по грубой оценке не успевает.
    """
    crews = []
    for crew in spec.crews:
        points = [entry for district in crew.districts for entry in pools[district]]
        crews.append(
            _CrewState(
                crew,
                lat=sum(p["lat"] for p in points) / len(points),
                lon=sum(p["lon"] for p in points) / len(points),
            )
        )
    late = 0
    order = sorted(
        (d for d in drafts if d.status != NOT_SENT),
        key=lambda d: (_minutes(d.start), _minutes(d.end), d.synthetic_id),
    )
    for draft in order:
        skill = cfg.skill_by_bk[draft.type_bk]
        needs_car = synth_transport_required(cfg, skill, draft.type_hd) == Transport.CAR
        duration = synth_duration(cfg, draft.synthetic_id, draft.type_bk)
        estimates = {id(c): _estimate(c, draft, duration) for c in crews}
        capable = [c for c in crews if c.spec.car or not needs_car]
        free = [c for c in capable if c.visits < MAX_VISITS_PER_CREW] or capable
        own = [c for c in free if draft.district.id in c.spec.districts]
        feasible_own = [c for c in own if estimates[id(c)][0] == 0]
        feasible_any = [c for c in free if estimates[id(c)][0] == 0]
        pool = feasible_own or feasible_any or own or free
        chosen = min(pool, key=lambda c: (estimates[id(c)], c.spec.name))
        lateness, travel, _ = estimates[id(chosen)]
        late += lateness > 0
        chosen.time_min = (
            int(max(chosen.time_min + travel, _minutes(draft.start), SHIFT_START_MIN)) + duration
        )
        chosen.lat, chosen.lon = draft.entry["lat"], draft.entry["lon"]
        chosen.visits += 1
        draft.crew = chosen.spec.name
    return late


def generate_region(
    spec: RegionSpec,
    pools: dict[str, list[dict]],
    cfg: SynthConfig,
    empirical: Empirical,
    taken_ids: set[str] = frozenset(),
) -> GeneratedRegion:
    def rng(purpose: str, key: str = "") -> random.Random:
        return random.Random(f"{spec.seed}:{purpose}:{key}")

    drafts: list[_Draft] = []
    for district in spec.districts:
        entries = [
            entry
            for entry in pools.get(district.id, [])
            if spec.bbox is None or spec.bbox.contains(entry["lat"], entry["lon"])
        ]
        if len(entries) < district.count:
            raise ValueError(f"в районе {district.id} {len(entries)} адресов, нужно {district.count}")
        for k, entry in enumerate(rng("addresses", district.id).sample(entries, district.count)):
            r = rng("request", f"{district.id}:{k}")
            type_bk, type_hd = r.choices(*empirical.pairs)[0]
            start, end = r.choices(*empirical.windows_by_urgency[type_bk in cfg.urgent_bk_types])[0]
            connections = empirical.connection_by_bk.get(type_bk, ([("", "Нет")], [1]))
            drafts.append(
                _Draft(
                    district=district,
                    entry=entry,
                    type_bk=type_bk,
                    type_hd=type_hd,
                    start=start,
                    end=end,
                    status=r.choices(*empirical.statuses)[0],
                    connection=r.choices(*connections)[0],
                    flat=f", кв. {r.randint(1, 320)}" if entry["flat"] else "",
                )
            )
    rng("order").shuffle(drafts)
    synthetic_ids = _unique_ids(rng("synthetic_ids"), len(drafts), 10_000, 100_000, set(taken_ids))
    control_ids = _unique_ids(rng("control_ids"), len(drafts), 306_000_000, 307_000_000, set())
    for draft, synthetic_id, control_id in zip(drafts, synthetic_ids, control_ids, strict=True):
        draft.synthetic_id, draft.control_id = synthetic_id, control_id
    regular = [d for d in drafts if d.type_bk not in cfg.urgent_bk_types]
    for draft in rng("not_sent").sample(regular, min(spec.not_sent, len(regular))):
        draft.status = NOT_SENT
    late = _dispatch(spec, cfg, drafts, pools)

    office, office_entry = _office(spec, pools)
    control, synthetic = io.StringIO(), io.StringIO()
    control_writer = csv.writer(control, delimiter=";", lineterminator="\n")
    synthetic_writer = csv.writer(synthetic, delimiter=";", lineterminator="\n")
    control_writer.writerow(CONTROL_HEADER)
    synthetic_writer.writerow(SYNTHETIC_HEADER)
    for d in drafts:
        start, end = f"{spec.date} {d.start}", f"{spec.date} {d.end}"
        address = d.entry["address"]
        control_writer.writerow(
            [
                d.control_id,
                d.type_bk,
                d.status,
                d.type_hd,
                start,
                end,
                d.district.name,
                address + d.flat,
                d.crew,
                *d.connection,
            ]
        )
        synthetic_writer.writerow(
            [d.synthetic_id, d.type_bk, d.type_hd, start, end, d.district.name, address, *d.connection]
        )
    blank = [""] * len(SYNTHETIC_HEADER)
    synthetic_writer.writerow(blank)
    synthetic_writer.writerow(blank)
    synthetic_writer.writerow([OFFICE_MARKER, office, *[""] * (len(SYNTHETIC_HEADER) - 2)])
    return GeneratedRegion(
        control_csv=control.getvalue(),
        synthetic_csv=synthetic.getvalue(),
        office_address=office,
        late_by_estimate=late,
        visits_by_crew=dict(Counter(d.crew for d in drafts if d.crew)),
        points=[
            *((d.entry["address"], d.district.name, d.entry["lat"], d.entry["lon"]) for d in drafts),
            (office, "", office_entry["lat"], office_entry["lon"]),
        ],
    )


def _taken_synthetic_ids(spec: RegionSpec) -> set[str]:
    taken: set[str] = set()
    for path in sorted((REPO_ROOT / "data" / "raw").glob("*_synthetic.csv")):
        if path == REPO_ROOT / spec.synthetic_file:
            continue
        taken.update(row.request_id for row in parse_beeline_csv(path.read_bytes()).rows)
    return taken


# Ответ геокодера дальше этого расстояния от дома OpenStreetMap считается промахом и заменяется точкой дома.
SEED_MAX_ERROR_KM = 0.3
# Ранг адресной точки Nominatim (дом)
HOUSE_PLACE_RANK = 30


def seed_geocode_cache(cache: JsonGeocodeCache, points: list[tuple[str, str, float, float]]) -> int:
    """Кладёт точки домов OpenStreetMap в кэш геокодера под строками запросов, которые построит разбор адреса.

    Геокодер ищет «Москва, улица, дом» во всей Москве и области и на частых названиях улиц попадает в чужой город
    (например, «Центральная улица»). Для сгенерированных адресов точка известна заранее, поэтому промах в кэше
    заменяется, а верный ответ остаётся. Возвращает число записанных запросов.
    """
    written = 0
    for address, district, lat, lon in points:
        for query, precision in query_variants(parse_address(address), district):
            if precision != "house":
                continue
            found, hit = cache.get(query)
            close = hit is not None and haversine_km(hit.lat, hit.lon, lat, lon) <= SEED_MAX_ERROR_KM
            if found and close and hit.category != "highway":
                continue
            cache.put(query, GeoHit(lat, lon, "building", HOUSE_PLACE_RANK))
            written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Генерирует регион в формате выгрузки Билайна из адресов OpenStreetMap"
    )
    parser.add_argument("--spec", default=str(BACKEND_DIR / "config" / "regions" / "north_west.yaml"))
    parser.add_argument("--fetch", action="store_true", help="заново выгрузить адреса из Overpass API")
    parser.add_argument(
        "--district",
        action="append",
        help="вместе с --fetch: выгрузить только этот район, можно несколько раз",
    )
    args = parser.parse_args(argv)

    spec = RegionSpec.load(Path(args.spec))
    addresses_path = REPO_ROOT / spec.addresses_file
    if args.fetch:
        only = set(args.district) if args.district else None
        existing = None
        if only and addresses_path.exists():
            existing = json.loads(addresses_path.read_text(encoding="utf-8"))["districts"]
        pools = fetch_addresses(spec, only, existing)
        addresses_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"source": "© участники OpenStreetMap, ODbL; выгрузка Overpass API", "districts": pools}
        addresses_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    pools = json.loads(addresses_path.read_text(encoding="utf-8"))["districts"]

    cfg = SynthConfig.load(BACKEND_DIR / "config" / "synth_config.yaml")
    empirical = load_empirical([REPO_ROOT / path for path in spec.empirical_from], cfg)
    result = generate_region(spec, pools, cfg, empirical, _taken_synthetic_ids(spec))
    (REPO_ROOT / spec.control_file).write_text(result.control_csv, encoding="utf-8")
    (REPO_ROOT / spec.synthetic_file).write_text(result.synthetic_csv, encoding="utf-8")
    cache = JsonGeocodeCache(REPO_ROOT / "data" / "geocode_cache.json")
    seeded = seed_geocode_cache(cache, result.points)
    cache.save()
    print(f"офис: {result.office_address}")
    print(f"записано в кэш геокодера точек домов: {seeded}")
    print(f"заявок по бригадам: {dict(sorted(result.visits_by_crew.items()))}")
    print(f"по грубой оценке диспетчер не успевает в окно: {result.late_by_estimate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
