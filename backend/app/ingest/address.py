"""Разбор адресов (выгрузка Билайна и ручной ввод диспетчера) и построение запросов к геокодеру."""

from __future__ import annotations

import re
from dataclasses import dataclass

STREET_TYPES: dict[str, str] = {
    "ул": "улица",
    "улица": "улица",
    "пр-кт": "проспект",
    "пр-т": "проспект",
    "просп": "проспект",
    "проспект": "проспект",
    "пер": "переулок",
    "переулок": "переулок",
    "проезд": "проезд",
    "пр-зд": "проезд",
    "б-р": "бульвар",
    "бульвар": "бульвар",
    "наб": "набережная",
    "набережная": "набережная",
    "ш": "шоссе",
    "шоссе": "шоссе",
    "пл": "площадь",
    "площадь": "площадь",
    "туп": "тупик",
    "тупик": "тупик",
    "аллея": "аллея",
}
TOWNS_OUTSIDE_MOSCOW = ("Домодедово", "Кашира", "Ступино")

_TYPES = "|".join(sorted((re.escape(key) for key in STREET_TYPES), key=len, reverse=True))
_PREFIX_TYPE = re.compile(rf"^(?P<type>{_TYPES})(?:\.\s*|\s+)(?P<name>.+)$", re.IGNORECASE)
_SUFFIX_TYPE = re.compile(rf"^(?P<name>.+?)\s+(?P<type>{_TYPES})\.?$", re.IGNORECASE)
# Квартира, подъезд, этаж, офис и помещение в конце адреса к номеру дома не относятся.
_TAIL = re.compile(
    r",?\s*(?<![а-яё])(?:кв\.\s*\S+|(?:кв|квартира)\s+\d\S*"
    r"|(?:подъезд|под\.|этаж|эт\.?|офис|оф\.?|помещение|пом\.?)\s*\d\S*)\s*$",
    re.IGNORECASE,
)
_TOWN_PREFIX = re.compile(
    r"^(?:г\.\s*)?(?:Город\s+)?(?:Москва|Домодедово|Кашира|Ступино)\b\s*", re.IGNORECASE
)
_ORDINAL_SUFFIX = re.compile(r"^(?P<rest>.+?)\s+(?P<ord>\d+-[йяе])$")
_QUARTER = re.compile(r"\s+Квартал\s+\S+$")

# Части дома: корпус («корпус 1», «корп. 1», «к1») и строение («строение 2», «стр. 2», «с2»).
_KORPUS = r"(?:корпус|корп\.?|кор\.?|к\.?)"
_STROENIE = r"(?:строение|стр\.?|с\.?)"
_BUILDING = rf"(?<![а-яё])(?:{_KORPUS}|{_STROENIE})"
_HOUSE_WORD = r"(?:дом|д\.?|владение|вл\.?)"
_HOUSE_NUMBER = rf"\d+[а-яё]?(?:/\d+[а-яё]?)?(?:\s*{_BUILDING}\s*\d+[а-яё]?)*"
# Формат выгрузки: дом после «д.» в конце строки («..., д. 10 к 2»).
_HOUSE = re.compile(r"(?:^|[\s,])д\.?\s*(?P<house>(?:\d|к\d)[^,]*)$")
# Ручной ввод: номер дома в конце строки, со словом «дом» или без него («Перовская улица 42к1»).
_OWN_HOUSE = re.compile(rf"(?:^|[\s,])(?:{_HOUSE_WORD}\s*)?(?P<house>{_HOUSE_NUMBER})$", re.IGNORECASE)
_COMMA_BEFORE_BUILDING = re.compile(rf",\s*(?={_BUILDING}\s*\d)", re.IGNORECASE)
_KORPUS_MARK = re.compile(rf"\s*(?<![а-яё]){_KORPUS}\s*(?=\d)", re.IGNORECASE)
_STROENIE_MARK = re.compile(rf"\s*(?<![а-яё]){_STROENIE}\s*(?=\d)", re.IGNORECASE)
_HOUSE_WORD_MARK = re.compile(rf"(?<![а-яё]){_HOUSE_WORD}\s*(?=\d)", re.IGNORECASE)
_BUILDING_MARK = re.compile(
    rf"(?:(?<=\d)[\s,]*)?(?<![а-яё])(?:(?P<korpus>{_KORPUS})|{_STROENIE})\s*(?=\d)", re.IGNORECASE
)


@dataclass(frozen=True)
class ParsedAddress:
    raw: str
    city: str
    street_type: str | None
    street_name: str | None
    house: str | None


def normalize_house(raw: str) -> str | None:
    value = _STROENIE_MARK.sub("с", raw.strip())
    value = _KORPUS_MARK.sub("к", value)
    value = re.sub(r"\s+", "", value)
    return value if value[:1].isdigit() else None


def _split_street(chunk: str) -> tuple[str, str] | None:
    for pattern in (_PREFIX_TYPE, _SUFFIX_TYPE):
        match = pattern.match(chunk)
        if not match:
            continue
        name = _QUARTER.sub("", match.group("name").strip().rstrip("."))
        ordinal = _ORDINAL_SUFFIX.match(name)
        if ordinal:
            name = f"{ordinal.group('ord')} {ordinal.group('rest')}"
        return STREET_TYPES[match.group("type").lower()], name
    return None


def _strip_tail(text: str) -> str:
    """Убирает с конца адреса квартиру, подъезд, этаж, офис и помещение, сколько бы их ни было подряд."""
    while True:
        stripped = _TAIL.sub("", text, count=1)
        if stripped == text:
            return stripped
        text = stripped


def parse_address(raw: str) -> ParsedAddress:
    text = _strip_tail(raw.strip())
    city = "Москва"
    for town in TOWNS_OUTSIDE_MOSCOW:
        if re.search(rf"\b{town}\b", text, re.IGNORECASE):
            city = f"{town}, Московская область"
            break
    # «дом 42, корпус 1» -> «дом 42 корпус 1»: корпус и строение относятся к дому перед запятой.
    text = _COMMA_BEFORE_BUILDING.sub(" ", text)
    house = None
    match = _HOUSE.search(text) or _OWN_HOUSE.search(text)
    if match:
        house = normalize_house(match.group("house"))
        text = text[: match.start()].strip(" ,")
    street_type = street_name = None
    for part in reversed([piece.strip() for piece in text.split(",") if piece.strip()]):
        chunk = _TOWN_PREFIX.sub("", part).strip()
        if not chunk:
            continue
        split = _split_street(chunk)
        if split:
            street_type, street_name = split
            break
    return ParsedAddress(raw=raw, city=city, street_type=street_type, street_name=street_name, house=house)


def raw_query(raw: str) -> str:
    """Адрес как его ввели, но без квартиры, подъезда, этажа и слов «дом», «корпус», «строение»: «42, корпус 1» -> «42к1»."""
    text = _strip_tail(raw.strip())
    text = _HOUSE_WORD_MARK.sub("", text)
    text = _BUILDING_MARK.sub(lambda match: "к" if match.group("korpus") else "с", text)
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"\s*,[\s,]*", ", ", text).strip(" ,")


def query_variants(parsed: ParsedAddress, district: str = "") -> list[tuple[str, str]]:
    """Запросы к геокодеру от самого точного к самому грубому: [(запрос, точность)].

    Если дом из разбора искать не с чем, после домов идёт адрес как его ввели с точностью "auto": её
    определяет найденный объект. Такой запрос добавляется, когда в адресе есть цифры или других запросов нет.
    """
    houses: list[tuple[str, str]] = []
    streets: list[tuple[str, str]] = []
    if parsed.street_name and parsed.street_type:
        name_first = f"{parsed.street_name} {parsed.street_type}"
        type_first = f"{parsed.street_type} {parsed.street_name}"
        if parsed.house:
            houses.append((f"{parsed.city}, {name_first}, {parsed.house}", "house"))
            houses.append((f"{parsed.city}, {type_first}, {parsed.house}", "house"))
        streets.append((f"{parsed.city}, {name_first}", "street"))
        streets.append((f"{parsed.city}, {type_first}", "street"))
    localities: list[tuple[str, str]] = []
    if parsed.city == "Москва":
        clean = re.sub(r"\s*-\s*", "-", district.replace("GPON", "")).strip()
        if clean:
            localities.append((f"район {clean}, Москва", "locality"))
    else:
        localities.append((parsed.city, "locality"))
    others = streets + localities
    as_typed = raw_query(parsed.raw)
    if (
        not houses
        and as_typed
        and as_typed not in {query for query, _ in others}
        and (re.search(r"\d", as_typed) or not others)
    ):
        houses.append((as_typed, "auto"))
    return houses + others
