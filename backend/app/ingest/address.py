"""Разбор адресов из выгрузки Билайна и построение запросов к геокодеру."""

from __future__ import annotations

import re
from dataclasses import dataclass

STREET_TYPES: dict[str, str] = {
    "ул": "улица",
    "пр-кт": "проспект",
    "пер": "переулок",
    "проезд": "проезд",
    "пр-зд": "проезд",
    "б-р": "бульвар",
    "наб": "набережная",
    "ш": "шоссе",
    "пл": "площадь",
    "туп": "тупик",
}
TOWNS_OUTSIDE_MOSCOW = ("Домодедово", "Кашира", "Ступино")

_TYPES = "|".join(sorted((re.escape(key) for key in STREET_TYPES), key=len, reverse=True))
_PREFIX_TYPE = re.compile(rf"^(?P<type>{_TYPES})(?:\.\s*|\s+)(?P<name>.+)$")
_SUFFIX_TYPE = re.compile(rf"^(?P<name>.+?)\s+(?P<type>{_TYPES})\.?$")
_HOUSE = re.compile(r"(?:^|[\s,])д\.?\s*(?P<house>(?:\d|к\d)[^,]*)$")
_FLAT = re.compile(r",?\s*кв\.\s*\S+\s*$")
_TOWN_PREFIX = re.compile(r"^(?:г\.\s*)?(?:Город\s+)?(?:Москва|Домодедово|Кашира|Ступино)\b\s*")
_ORDINAL_SUFFIX = re.compile(r"^(?P<rest>.+?)\s+(?P<ord>\d+-[йяе])$")
_QUARTER = re.compile(r"\s+Квартал\s+\S+$")


@dataclass(frozen=True)
class ParsedAddress:
    raw: str
    city: str
    street_type: str | None
    street_name: str | None
    house: str | None


def normalize_house(raw: str) -> str | None:
    value = raw.strip()
    value = re.sub(r"\s*стр\.\s*", "с", value)
    value = re.sub(r"\s*к\s*(?=\d)", "к", value)
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
        return STREET_TYPES[match.group("type")], name
    return None


def parse_address(raw: str) -> ParsedAddress:
    text = _FLAT.sub("", raw.strip())
    city = "Москва"
    for town in TOWNS_OUTSIDE_MOSCOW:
        if re.search(rf"\b{town}\b", text):
            city = f"{town}, Московская область"
            break
    house = None
    match = _HOUSE.search(text)
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


def query_variants(parsed: ParsedAddress, district: str = "") -> list[tuple[str, str]]:
    """Запросы к геокодеру от самого точного к самому грубому: [(запрос, точность)]."""
    variants: list[tuple[str, str]] = []
    if parsed.street_name and parsed.street_type:
        name_first = f"{parsed.street_name} {parsed.street_type}"
        type_first = f"{parsed.street_type} {parsed.street_name}"
        if parsed.house:
            variants.append((f"{parsed.city}, {name_first}, {parsed.house}", "house"))
            variants.append((f"{parsed.city}, {type_first}, {parsed.house}", "house"))
        variants.append((f"{parsed.city}, {name_first}", "street"))
        variants.append((f"{parsed.city}, {type_first}", "street"))
    if parsed.city == "Москва":
        clean = re.sub(r"\s*-\s*", "-", district.replace("GPON", "")).strip()
        if clean:
            variants.append((f"район {clean}, Москва", "locality"))
    else:
        variants.append((parsed.city, "locality"))
    return variants
