"""Эталон запросов к геокодеру для адресов из выгрузок Билайна.

Ключи data/geocode_cache.json — это строки запросов, поэтому разбор адресов выгрузки не должен их менять.
Пересобрать эталон (только осознанно, с разбором каждой изменившейся записи): из каталога backend
выполнить `python -m tests.test_address_golden`.
"""

import json
from pathlib import Path

from app.ingest.address import parse_address, query_variants
from app.ingest.beeline_csv import parse_beeline_csv

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
GOLDEN_PATH = Path(__file__).parent / "data" / "address_variants_golden.json"


def export_addresses() -> list[tuple[str, str]]:
    """Уникальные пары (адрес, район) из строк заявок всех файлов data/raw, в порядке появления."""
    pairs: dict[tuple[str, str], None] = {}
    for path in sorted(RAW_DIR.glob("*.csv")):
        for row in parse_beeline_csv(path.read_bytes()).rows:
            pairs.setdefault((row.address, row.district), None)
    return list(pairs)


def build_golden() -> list[dict]:
    return [
        {
            "raw": raw,
            "district": district,
            "variants": [list(variant) for variant in query_variants(parse_address(raw), district)],
        }
        for raw, district in export_addresses()
    ]


def test_export_addresses_keep_geocoder_queries():
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    actual = build_golden()
    assert golden, "эталон пуст"
    assert [(e["raw"], e["district"]) for e in actual] == [(e["raw"], e["district"]) for e in golden]
    changed = [(old, new) for old, new in zip(golden, actual, strict=True) if old != new]
    assert changed == []


if __name__ == "__main__":
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(build_golden(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
