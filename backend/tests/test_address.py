import pytest

from app.ingest.address import normalize_house, parse_address, query_variants

CASES = [
    ("Город Москва, пр-кт.Волгоградский, д. 128 к 5, кв. 1", "Москва", "проспект", "Волгоградский", "128к5"),
    ("г.Город Москва, наб.Семеновская, д. 3/1к2", "Москва", "набережная", "Семеновская", "3/1к2"),
    ("Москва, проезд Орехово-Зуевский, д. 18/8", "Москва", "проезд", "Орехово-Зуевский", "18/8"),
    (
        "Домодедово, проезд.Советский 1-й, д. 1А",
        "Домодедово, Московская область",
        "проезд",
        "1-й Советский",
        "1А",
    ),
    (
        "обл.Московская область, г.Домодедово, пгт.Востряково-1, ул.Жуковского, д. 14/18",
        "Домодедово, Московская область",
        "улица",
        "Жуковского",
        "14/18",
    ),
    ("МО, г. Кашира Центральная ул. д. 21", "Кашира, Московская область", "улица", "Центральная", "21"),
    ("Москва Бирюлевская ул. д. 44", "Москва", "улица", "Бирюлевская", "44"),
    ("Москва Булатниковский пр-зд. д. 6к1", "Москва", "проезд", "Булатниковский", "6к1"),
    ("Город Москва, б-р.Самаркандский Квартал 137а, д. к5", "Москва", "бульвар", "Самаркандский", None),
    ("Город Москва, ш.Каширское, д. 136", "Москва", "шоссе", "Каширское", "136"),
    ("Город Москва, пр-кт.60-летия Октября, д. 18 к 2", "Москва", "проспект", "60-летия Октября", "18к2"),
    ("г. Москва, ул Юных Ленинцев, д 83с 4", "Москва", "улица", "Юных Ленинцев", "83с4"),
    ("г.Москва проезд Симферопольский, д.7", "Москва", "проезд", "Симферопольский", "7"),
]


@pytest.mark.parametrize(("raw", "city", "street_type", "street_name", "house"), CASES)
def test_parse_address_real_formats(raw, city, street_type, street_name, house):
    parsed = parse_address(raw)
    assert (parsed.city, parsed.street_type, parsed.street_name, parsed.house) == (
        city,
        street_type,
        street_name,
        house,
    )


def test_normalize_house_building_suffix():
    assert normalize_house("16 стр. 1") == "16с1"
    assert normalize_house("к5") is None


def test_query_variants_go_from_house_to_locality():
    parsed = parse_address("Город Москва, ул.Грайвороновская, д. 10 к 2")
    assert query_variants(parsed, "Текстильщики") == [
        ("Москва, Грайвороновская улица, 10к2", "house"),
        ("Москва, улица Грайвороновская, 10к2", "house"),
        ("Москва, Грайвороновская улица", "street"),
        ("Москва, улица Грайвороновская", "street"),
        ("район Текстильщики, Москва", "locality"),
    ]


def test_query_variants_for_town_fall_back_to_town():
    parsed = parse_address("Кашира, ул.Победы, д. 9")
    assert query_variants(parsed, "Кашира")[-1] == ("Кашира, Московская область", "locality")


PEROVSKAYA = ("Москва", "улица", "Перовская", "42к1")
MANUAL_CASES = [
    ("Москва, Перовская улица 42к1", *PEROVSKAYA),
    ("Москва, Перовская улица, 42к1", *PEROVSKAYA),
    ("Москва, улица Перовская, 42 к1", *PEROVSKAYA),
    ("Перовская ул., 42к1", *PEROVSKAYA),
    ("ул. Перовская 42к1", *PEROVSKAYA),
    ("г. Москва, Перовская ул, дом 42, корпус 1", *PEROVSKAYA),
    ("Москва, Перовская улица, д. 42, стр. 2", "Москва", "улица", "Перовская", "42с2"),
    ("Москва, Перовская улица, 42 строение 2", "Москва", "улица", "Перовская", "42с2"),
    ("Москва, Перовская улица, 42А", "Москва", "улица", "Перовская", "42А"),
    ("Москва, проспект Мира, 10", "Москва", "проспект", "Мира", "10"),
    ("Москва, Мира пр-т 10", "Москва", "проспект", "Мира", "10"),
    ("Москва, Зеленый проспект 20", "Москва", "проспект", "Зеленый", "20"),
    ("Москва, Рязанское шоссе, 1", "Москва", "шоссе", "Рязанское", "1"),
    ("Москва, Каширское ш., 136", "Москва", "шоссе", "Каширское", "136"),
    ("Москва, Бульвар Яна Райниса, 4", "Москва", "бульвар", "Яна Райниса", "4"),
    ("Москва, 1-я Владимирская улица, 10", "Москва", "улица", "1-я Владимирская", "10"),
    ("Кашира, улица Победы, 9", "Кашира, Московская область", "улица", "Победы", "9"),
]


@pytest.mark.parametrize(("raw", "city", "street_type", "street_name", "house"), MANUAL_CASES)
def test_parse_address_manual_input(raw, city, street_type, street_name, house):
    parsed = parse_address(raw)
    assert (parsed.city, parsed.street_type, parsed.street_name, parsed.house) == (
        city,
        street_type,
        street_name,
        house,
    )


@pytest.mark.parametrize(
    ("house_text", "house"),
    [
        ("42", "42"),
        ("42к1", "42к1"),
        ("42 к1", "42к1"),
        ("42А", "42А"),
        ("3/1к2", "3/1к2"),
        ("18/8", "18/8"),
        ("дом 42", "42"),
        ("д 42", "42"),
        ("д.42", "42"),
        ("42, корпус 1", "42к1"),
        ("42, корп. 1", "42к1"),
        ("42 кор. 1", "42к1"),
        ("42, к. 1", "42к1"),
        ("дом 42 к1", "42к1"),
        ("42, строение 2", "42с2"),
        ("42 стр. 2", "42с2"),
        ("42, стр 2", "42с2"),
        ("42 с. 2", "42с2"),
        ("владение 5", "5"),
        ("вл. 5", "5"),
        ("42к1, кв. 17", "42к1"),
    ],
)
def test_house_without_export_marker(house_text, house):
    assert parse_address(f"Москва, Перовская улица, {house_text}").house == house


@pytest.mark.parametrize(
    ("written", "street_type"),
    [
        ("улица", "улица"),
        ("ул", "улица"),
        ("УЛ.", "улица"),
        ("проспект", "проспект"),
        ("пр-кт", "проспект"),
        ("пр-т", "проспект"),
        ("просп.", "проспект"),
        ("переулок", "переулок"),
        ("пер.", "переулок"),
        ("проезд", "проезд"),
        ("пр-зд", "проезд"),
        ("бульвар", "бульвар"),
        ("б-р", "бульвар"),
        ("набережная", "набережная"),
        ("наб.", "набережная"),
        ("шоссе", "шоссе"),
        ("ш", "шоссе"),
        ("площадь", "площадь"),
        ("пл.", "площадь"),
        ("тупик", "тупик"),
        ("туп", "тупик"),
        ("Аллея", "аллея"),
    ],
)
def test_street_type_before_or_after_name(written, street_type):
    before = parse_address(f"Москва, {written} Мира, 7")
    after = parse_address(f"Москва, Мира {written} 7")
    assert (before.street_type, before.street_name, before.house) == (street_type, "Мира", "7")
    assert (after.street_type, after.street_name, after.house) == (street_type, "Мира", "7")


def test_ambiguous_pr_is_not_a_street_type():
    assert parse_address("Москва, пр Мира, 7").street_type is None


def test_ordinal_after_name_moves_forward_in_manual_input():
    parsed = parse_address("Москва, Владимирская 1-я улица, 10")
    assert (parsed.street_name, parsed.house) == ("1-я Владимирская", "10")


def test_query_variants_manual_address_hits_the_same_house_query():
    parsed = parse_address("Москва, Перовская улица 42к1")
    assert query_variants(parsed, "Перово") == [
        ("Москва, Перовская улица, 42к1", "house"),
        ("Москва, улица Перовская, 42к1", "house"),
        ("Москва, Перовская улица", "street"),
        ("Москва, улица Перовская", "street"),
        ("район Перово, Москва", "locality"),
    ]


def test_query_variants_raw_address_goes_before_street_when_house_is_not_parsed():
    parsed = parse_address("Город Москва, б-р.Самаркандский Квартал 137а, д. к5, кв. 25")
    assert query_variants(parsed, "Выхино") == [
        ("Город Москва, б-р.Самаркандский Квартал 137а, д. к5", "auto"),
        ("Москва, Самаркандский бульвар", "street"),
        ("Москва, бульвар Самаркандский", "street"),
        ("район Выхино, Москва", "locality"),
    ]


def test_query_variants_raw_address_without_street_type():
    parsed = parse_address("Москва ,, Перовская ,  42  корпус 1,  кв. 7")
    assert query_variants(parsed, "Перово") == [
        ("Москва, Перовская, 42к1", "auto"),
        ("район Перово, Москва", "locality"),
    ]


def test_query_variants_raw_address_normalizes_building_parts():
    parsed = parse_address("Кашира, Победы, дом 9, строение 2")
    assert query_variants(parsed, "Кашира") == [
        ("Кашира, Победы, 9с2", "auto"),
        ("Кашира, Московская область", "locality"),
    ]


def test_query_variants_raw_address_when_nothing_else_is_known():
    assert query_variants(parse_address("Москва, Перовская"), "") == [("Москва, Перовская", "auto")]


def test_query_variants_no_raw_address_without_digits_when_other_variants_exist():
    assert query_variants(parse_address("Москва, Перовская улица"), "") == [
        ("Москва, Перовская улица", "street"),
        ("Москва, улица Перовская", "street"),
    ]
    assert query_variants(parse_address("Москва, Перовская"), "Перово") == [
        ("район Перово, Москва", "locality")
    ]


def test_query_variants_raw_address_does_not_repeat_a_street_query():
    assert query_variants(parse_address("Москва, 3-я Парковая улица"), "") == [
        ("Москва, 3-я Парковая улица", "street"),
        ("Москва, улица 3-я Парковая", "street"),
    ]
