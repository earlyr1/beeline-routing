from collections import Counter

import pytest

from app.domain.enums import Transport
from app.ingest.beeline_csv import parse_beeline_csv
from app.settings import BACKEND_DIR, REPO_ROOT
from app.synth.config import SynthConfig
from app.synth.engineers import build_engineers
from app.synth.generate_region import (
    NOT_SENT,
    CrewSpec,
    DistrictSpec,
    OfficeSpec,
    RegionSpec,
    beeline_address,
    beeline_house,
    beeline_street,
    generate_region,
    load_empirical,
    pool_from_elements,
)
from app.synth.requests import check_alignment, synth_duration, synth_transport_required

CFG = SynthConfig.load(BACKEND_DIR / "config" / "synth_config.yaml")
EMPIRICAL = load_empirical(
    [
        REPO_ROOT / "data" / "raw" / f"{region}_control.csv"
        for region in ("east", "south_east", "south_center")
    ],
    CFG,
)


@pytest.mark.parametrize(
    ("street", "expected"),
    [
        ("улица Маршала Тухачевского", "ул.Маршала Тухачевского"),
        ("Живописная улица", "ул.Живописная"),
        ("проспект Маршала Жукова", "пр-кт.Маршала Жукова"),
        ("бульвар Генерала Карбышева", "б-р.Генерала Карбышева"),
        ("Карамышевская набережная", "наб.Карамышевская"),
        ("Новохорошёвский проезд", "проезд.Новохорошевский"),
        ("3-я Хорошёвская улица", "ул.Хорошевская 3-я"),
        ("улица Нижние Мнёвники", "ул.Нижние Мневники"),
        ("Волоколамское шоссе", "ш.Волоколамское"),
        ("3-я Линия", None),
        ("улица", None),
    ],
)
def test_street_is_written_like_the_beeline_export(street, expected):
    assert beeline_street(street) == expected


@pytest.mark.parametrize(
    ("house", "expected"),
    [
        ("56", "д. 56"),
        ("35 к1", "д. 35 к 1"),
        ("9 с1", "д. 9 стр. 1"),
        ("37А с4", "д. 37А стр. 4"),
        ("12 к2 с3", "д. 12 к 2 стр. 3"),
        ("3/1", "д. 3/1"),
        ("12;14", None),
        ("10-12", None),
    ],
)
def test_house_is_written_like_the_beeline_export(house, expected):
    assert beeline_house(house) == expected


def test_address_must_parse_back_to_street_and_house():
    assert (
        beeline_address("улица Маршала Тухачевского", "56 к1")
        == "Город Москва, ул.Маршала Тухачевского, д. 56 к 1"
    )
    assert beeline_address("3-я Линия", "5") is None


def test_pool_keeps_residential_addresses_once_and_in_stable_order():
    elements: list[dict] = [
        {
            "type": "way",
            "center": {"lat": 55.8, "lon": 37.4},
            "tags": {"building": "apartments", "addr:street": "Живописная улица", "addr:housenumber": "6 к1"},
        },
        {
            "type": "node",
            "lat": 55.8,
            "lon": 37.4,
            "tags": {"addr:street": "Живописная улица", "addr:housenumber": "6 к1"},
        },
        {
            "type": "node",
            "lat": 55.7,
            "lon": 37.5,
            "tags": {"addr:street": "улица Нижние Мнёвники", "addr:housenumber": "12"},
        },
        {
            "type": "way",
            "center": {"lat": 55.7, "lon": 37.5},
            "tags": {"building": "school", "addr:street": "Живописная улица", "addr:housenumber": "9"},
        },
        {
            "type": "way",
            "center": {"lat": 55.7, "lon": 37.5},
            "tags": {"building": "yes", "addr:street": "3-я Линия", "addr:housenumber": "5"},
        },
        {
            "type": "way",
            "center": {"lat": 55.7, "lon": 37.5},
            "tags": {"building": "house", "addr:street": "Таманская улица", "addr:housenumber": "12;14"},
        },
    ]
    pool = pool_from_elements(elements, "test")
    assert [(entry["address"], entry["flat"]) for entry in pool] == [
        ("Город Москва, ул.Живописная, д. 6 к 1", True),
        ("Город Москва, ул.Нижние Мневники, д. 12", False),
    ]


def _spec(not_sent=1):
    return RegionSpec(
        region="test_region",
        title="Тест",
        date="17.08.2026",
        seed=7,
        not_sent=not_sent,
        addresses_file="unused.json",
        control_file="unused_control.csv",
        synthetic_file="unused_synthetic.csv",
        empirical_from=[],
        office=OfficeSpec(district="a", street="улица Альфа"),
        districts=[
            DistrictSpec(id="a", name="Альфа", osm_area="район Альфа", count=12),
            DistrictSpec(id="b", name="Бета", osm_area="район Бета", count=10),
        ],
        crews=[
            CrewSpec(name="Бригада Первов", districts=["a"], car=True),
            CrewSpec(name="Бригада Вторых", districts=["a", "b"], car=False),
            CrewSpec(name="Бригада Третьяков", districts=["b"], car=False),
            CrewSpec(name="Бригада Четвериков", districts=["b"], car=False),
        ],
    )


def _pools():
    def entries(street, lat):
        return [
            {
                "address": f"Город Москва, ул.{street}, д. {n}",
                "flat": n % 2 == 0,
                "lat": lat + n / 1000,
                "lon": 37.4 + n / 1000,
                "osm_street": f"улица {street}",
                "osm_house": str(n),
            }
            for n in range(1, 25)
        ]

    return {"a": entries("Альфа", 55.80), "b": entries("Бета", 55.75)}


def test_generated_files_parse_align_and_follow_the_spec():
    spec = _spec()
    result = generate_region(spec, _pools(), CFG, EMPIRICAL, taken_ids={"10001"})
    control = parse_beeline_csv(result.control_csv.encode())
    synthetic = parse_beeline_csv(result.synthetic_csv.encode())
    check_alignment(synthetic, control)
    assert control.is_control and not synthetic.is_control
    assert synthetic.office_address == result.office_address == "г. Москва, ул.Альфа, д. 1"
    assert Counter(row.district for row in synthetic.rows) == {"Альфа": 12, "Бета": 10}
    assert "10001" not in {row.request_id for row in synthetic.rows}

    crews = {crew.name: crew for crew in spec.crews}
    not_sent = [row for row in control.rows if row.status_bk == NOT_SENT]
    assert len(not_sent) == 1 and not_sent[0].crew == ""
    for row in control.rows:
        if row.status_bk == NOT_SENT:
            continue
        assert row.crew in crews
        needs_car = synth_transport_required(CFG, CFG.skill_by_bk[row.type_bk], row.type_hd) == Transport.CAR
        assert crews[row.crew].car or not needs_car
    assert sum(result.visits_by_crew.values()) == len(control.rows) - 1


def test_generated_region_dispatches_crews_by_the_official_norms():
    """Раздача заявок бригадам считает время на адресе по нормативу типа заявки BK."""
    spec = _spec()
    result = generate_region(spec, _pools(), CFG, EMPIRICAL)
    control = parse_beeline_csv(result.control_csv.encode())
    synthetic = parse_beeline_csv(result.synthetic_csv.encode())
    for row in synthetic.rows:
        assert synth_duration(CFG, row.request_id, row.type_bk) == CFG.duration_by_bk[row.type_bk]
    sent = [row for row in control.rows if row.status_bk != NOT_SENT]
    assert all(row.crew for row in sent)
    assert sum(result.visits_by_crew.values()) == len(sent)


def test_generated_region_synthesizes_engineers_with_their_transport():
    spec = _spec()
    result = generate_region(spec, _pools(), CFG, EMPIRICAL)
    control = parse_beeline_csv(result.control_csv.encode())
    from app.domain.models import Office

    office = Office(region="test_region", title="Тест", address=result.office_address, lat=55.8, lon=37.4)
    engineers, crew_to_engineer = build_engineers(CFG, "test_region", control, office)
    assert set(crew_to_engineer) <= {crew.name for crew in spec.crews}
    no_car_crews = {crew.name for crew in spec.crews if not crew.car}
    for engineer in engineers:
        crew = next(name for name, eid in crew_to_engineer.items() if eid == engineer.id)
        if crew in no_car_crews:
            assert "emergency" not in engineer.skills


def test_generation_is_deterministic():
    first = generate_region(_spec(), _pools(), CFG, EMPIRICAL)
    second = generate_region(_spec(), _pools(), CFG, EMPIRICAL)
    assert (first.control_csv, first.synthetic_csv) == (second.control_csv, second.synthetic_csv)


def test_district_without_enough_addresses_is_an_error():
    pools = _pools()
    pools["b"] = pools["b"][:3]
    with pytest.raises(ValueError, match="в районе b 3 адресов, нужно 10"):
        generate_region(_spec(), pools, CFG, EMPIRICAL)


def test_generated_points_replace_geocoder_misses_in_the_cache(tmp_path):
    from app.ingest.geocode import GeoHit, JsonGeocodeCache
    from app.synth.generate_region import seed_geocode_cache

    cache = JsonGeocodeCache(tmp_path / "cache.json")
    # Промах: одноимённая улица в области за 127 км; верный ответ рядом с домом; чужой запрос.
    cache.put("Москва, Центральная улица, 84", GeoHit(56.76, 37.15, "building", 30))
    cache.put("Москва, Живописная улица, 6к1", GeoHit(55.78561, 37.47001, "building", 30))
    cache.put("Москва, Тверская улица, 7", GeoHit(55.7575, 37.6128, "building", 30))
    points = [
        ("Город Москва, ул.Центральная, д. 84", "Левобережный", 55.8712, 37.4735),
        ("Город Москва, ул.Живописная, д. 6 к 1", "Хорошево-Мневники", 55.7856, 37.4700),
    ]

    written = seed_geocode_cache(cache, points)

    assert written == 3  # два варианта промаха и недостающий вариант «улица Живописная»
    assert cache.get("Москва, Центральная улица, 84")[1] == GeoHit(55.8712, 37.4735, "building", 30)
    assert cache.get("Москва, улица Центральная, 84")[1] == GeoHit(55.8712, 37.4735, "building", 30)
    assert cache.get("Москва, Живописная улица, 6к1")[1] == GeoHit(55.78561, 37.47001, "building", 30)
    assert cache.get("Москва, Тверская улица, 7")[1] == GeoHit(55.7575, 37.6128, "building", 30)


def test_generated_region_lists_points_of_requests_and_office():
    pools = _pools()
    result = generate_region(_spec(), pools, CFG, EMPIRICAL)
    assert len(result.points) == 22 + 1
    office = pools["a"][0]
    assert result.points[-1] == ("г. Москва, ул.Альфа, д. 1", "", office["lat"], office["lon"])


def test_addresses_outside_the_region_frame_are_dropped():
    from app.synth.generate_region import BBox

    moscow = BBox(south=55.69, west=37.28, north=55.93, east=37.62)
    elements = [
        {
            "type": "node",
            "lat": 55.87,
            "lon": 37.47,
            "tags": {"addr:street": "Смольная улица", "addr:housenumber": "2"},
        },
        # Тёзка района в Дубне
        {
            "type": "node",
            "lat": 56.758,
            "lon": 37.148,
            "tags": {"addr:street": "Базарный переулок", "addr:housenumber": "19"},
        },
    ]
    pool = pool_from_elements(elements, "test", bbox=moscow)
    assert [entry["address"] for entry in pool] == ["Город Москва, ул.Смольная, д. 2"]

    pools = _pools()
    spec = _spec().model_copy(update={"bbox": BBox(south=55.70, west=37.0, north=55.83, east=38.0)})
    pools["b"] = [dict(entry, lat=56.7) for entry in pools["b"][:5]] + pools["b"][5:]
    result = generate_region(spec, pools, CFG, EMPIRICAL)
    assert all(spec.bbox.contains(lat, lon) for _, _, lat, lon in result.points)


def test_street_level_hit_near_the_house_is_replaced_by_the_house(tmp_path):
    from app.ingest.geocode import GeoHit, JsonGeocodeCache
    from app.synth.generate_region import seed_geocode_cache

    cache = JsonGeocodeCache(tmp_path / "cache.json")
    cache.put("Москва, Онежская улица, 45/19", GeoHit(55.8651, 37.4952, "highway", 26))
    seed_geocode_cache(cache, [("Город Москва, ул.Онежская, д. 45/19", "Ховрино", 55.8653, 37.4955)])
    assert cache.get("Москва, Онежская улица, 45/19")[1] == GeoHit(55.8653, 37.4955, "building", 30)
