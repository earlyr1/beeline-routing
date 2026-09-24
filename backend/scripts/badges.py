"""Бейджи README: покрытие backend и frontend и число прошедших тестов — SVG в стиле shields.io (flat).

Репозиторий закрытый, и shields.io до его прогонов не достать, поэтому числа считает сам CI: job badges
в .github/workflows/ci.yml запускает этот скрипт после зелёного прогона верхушки main и кладёт SVG в ветку
badges, откуда их берёт README. Только стандартная библиотека: в job нет ни uv, ни окружения backend. Лежит
в backend/scripts, чтобы его проверяли те же ruff и mypy, что и остальной backend.

    python3 backend/scripts/badges.py OUT_DIR \\
        --backend-coverage backend/coverage.json \\
        --frontend-coverage frontend/coverage/coverage-summary.json \\
        --junit junit-backend.xml junit-backend-db.xml junit-frontend.xml

Каждый вход необязателен: чего не дали, того бейджа и не будет.
    --backend-coverage   отчёт `coverage json` (уже сложенный из прогона без базы и прогона на Postgres);
    --frontend-coverage  json-summary от vitest --coverage;
    --junit              отчёты JUnit XML pytest и vitest: тест в нескольких отчётах считается в каждом.

Процент покрытия — по строкам и округлён вниз до десятых: 89,96% не станет «90.0%».
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

# Ширина символов Verdana 11px в пикселях — тот же шрифт и размер, что у shields.io. Ширина нужна только для
# размера плашек: сам текст растягивается под неё атрибутом textLength, так что неточность видна не будет.
_WIDTHS = {
    " ": 3.9, "%": 12.1, "(": 4.5, ")": 4.5, ",": 3.6, "-": 4.4, ".": 3.6, "/": 4.4, ":": 4.4,
    "0": 7.0, "1": 7.0, "2": 7.0, "3": 7.0, "4": 7.0, "5": 7.0, "6": 7.0, "7": 7.0, "8": 7.0, "9": 7.0,
    "a": 6.6, "b": 6.9, "c": 5.7, "d": 6.9, "e": 6.6, "f": 3.9, "g": 6.9, "h": 7.0, "i": 3.0, "j": 3.8,
    "k": 6.5, "l": 3.0, "m": 10.7, "n": 7.0, "o": 6.7, "p": 6.9, "q": 6.9, "r": 4.7, "s": 5.7, "t": 4.4,
    "u": 7.0, "v": 6.5, "w": 9.0, "x": 6.5, "y": 6.5, "z": 5.8,
}  # fmt: skip
_DEFAULT_WIDTH = 7.5  # заглавные, кириллица и прочее, чего нет в таблице
_PADDING = 10  # по 5 px слева и справа от текста, как у shields.io

# Цвета shields.io и пороги покрытия, как у бейджей coverage в открытых проектах.
_COLORS = {
    "brightgreen": "#4c1",
    "green": "#97ca00",
    "yellowgreen": "#a4a61d",
    "yellow": "#dfb317",
    "orange": "#fe7d37",
    "red": "#e05d44",
}
_COVERAGE_SCALE = ((95, "brightgreen"), (90, "green"), (80, "yellowgreen"), (70, "yellow"), (60, "orange"))


def text_width(text: str) -> float:
    return sum(_WIDTHS.get(char, _DEFAULT_WIDTH) for char in text)


def render(label: str, value: str, color: str) -> str:
    """Плашка «label | value» в стиле flat: серая слева, цветная справа, текст с тенью."""
    left = round(text_width(label) + _PADDING)
    right = round(text_width(value) + _PADDING)
    total = left + right
    fill = _COLORS.get(color, color)
    title = escape(f"{label}: {value}", {'"': "&quot;"})
    # Текст задан в масштабе 1:10 (font-size 110, scale(.1)), как у shields.io: так ровнее сглаживание.
    parts = []
    for text, x, width in ((label, left / 2, left - _PADDING), (value, left + right / 2, right - _PADDING)):
        safe = escape(text)
        x10, width10 = round(x * 10), round(width * 10)
        parts.append(
            f'<text aria-hidden="true" x="{x10}" y="150" fill="#010101" fill-opacity=".3" '
            f'transform="scale(.1)" textLength="{width10}">{safe}</text>'
            f'<text x="{x10}" y="140" transform="scale(.1)" fill="#fff" textLength="{width10}">{safe}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{title}">'
        f"<title>{title}</title>"
        '<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        '<stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)"><rect width="{left}" height="20" fill="#555"/>'
        f'<rect x="{left}" width="{right}" height="20" fill="{fill}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/></g>'
        '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        f'text-rendering="geometricPrecision" font-size="110">{"".join(parts)}</g></svg>\n'
    )


def coverage_color(percent: float) -> str:
    return next((color for bound, color in _COVERAGE_SCALE if percent >= bound), "red")


def coverage_badge(label: str, percent: float) -> str:
    shown = math.floor(percent * 10) / 10
    return render(label, f"{shown:.1f}%", coverage_color(percent))


def backend_percent(path: Path) -> float:
    """Процент строк из отчёта `coverage json`: покрытые строки к строкам с кодом."""
    totals = json.loads(path.read_text(encoding="utf-8"))["totals"]
    statements = totals["num_statements"]
    return 100.0 * totals["covered_lines"] / statements if statements else 100.0


def frontend_percent(path: Path) -> float:
    """Процент строк из json-summary vitest (v8)."""
    lines = json.loads(path.read_text(encoding="utf-8"))["total"]["lines"]
    return 100.0 * lines["covered"] / lines["total"] if lines["total"] else 100.0


def count_tests(paths: list[Path]) -> tuple[int, int]:
    """Прошедшие и упавшие тесты по отчётам JUnit: пропущенные не идут ни туда, ни туда.

    У pytest и vitest отчёты устроены одинаково: <testcase> на тест, внутри <failure> или <error> — упал,
    <skipped> — пропущен (у pytest так же пишется xfail), пусто — прошёл.
    """
    passed = failed = 0
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            tags = {child.tag for child in case}
            if tags & {"failure", "error"}:
                failed += 1
            elif "skipped" not in tags:
                passed += 1
    return passed, failed


def tests_badge(passed: int, failed: int) -> str:
    if failed:
        return render("tests", f"{passed} passed, {failed} failed", "red")
    return render("tests", f"{passed} passed", "brightgreen")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SVG-бейджи покрытия и тестов для README.")
    parser.add_argument("out_dir", type=Path, help="куда положить SVG")
    parser.add_argument("--backend-coverage", type=Path, help="отчёт `coverage json`")
    parser.add_argument("--frontend-coverage", type=Path, help="json-summary от vitest --coverage")
    parser.add_argument("--junit", type=Path, nargs="+", default=[], help="отчёты JUnit XML")
    args = parser.parse_args(argv)

    badges: dict[str, str] = {}
    if args.backend_coverage:
        badges["coverage-backend.svg"] = coverage_badge(
            "backend coverage", backend_percent(args.backend_coverage)
        )
    if args.frontend_coverage:
        badges["coverage-frontend.svg"] = coverage_badge(
            "frontend coverage", frontend_percent(args.frontend_coverage)
        )
    if args.junit:
        badges["tests.svg"] = tests_badge(*count_tests(args.junit))
    if not badges:
        parser.error("не дано ни одного входа: нечего рисовать")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, svg in badges.items():
        (args.out_dir / name).write_text(svg, encoding="utf-8")
        print(f"{name}: {ET.fromstring(svg).get('aria-label')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
