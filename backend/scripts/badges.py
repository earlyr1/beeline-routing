"""Бейджи README: число тестов и покрытие backend и frontend — SVG в стиле shields.io (flat).

Репозиторий закрытый, и shields.io до его прогонов не достать, поэтому числа считает сам CI: job badges
в .github/workflows/ci.yml запускает этот скрипт на каждом пуше в main, чем бы ни кончились тесты, и кладёт
SVG в ветку badges, откуда их берёт README. Только стандартная библиотека: в job нет ни uv, ни окружения
backend. Лежит в backend/scripts, чтобы его проверяли те же ruff, mypy и тесты (tests/test_badges.py), что
и весь backend.

    python3 backend/scripts/badges.py OUT_DIR \\
        --backend-coverage reports/coverage-backend.json \\
        --frontend-coverage reports/coverage-frontend/coverage-summary.json \\
        --junit backend=reports/junit-backend.xml frontend=reports/junit-frontend.xml \\
        --status backend=success frontend=failure backend-coverage=success frontend-coverage=failure

    --backend-coverage   отчёт `coverage json` (уже сложенный из прогона без базы и прогона на Postgres);
    --frontend-coverage  json-summary от vitest --coverage;
    --junit              отчёты JUnit XML pytest и vitest, ИМЯ=ПУТЬ, где ИМЯ — шаг, который пишет отчёт;
    --status             чем кончился шаг, давший вход, ИМЯ=success|failure|cancelled|skipped, где ИМЯ — имя
                         из --junit, backend-coverage или frontend-coverage. Без --status все шаги считаются
                         успешными (запуск руками), с ним итог нужен каждому входу.
Чего не дали, того бейджа и не будет.

Зелёным бейдж бывает, только если за ним успешный шаг и настоящие данные:
- отчёта нет, он не читается или в нём ноль (ни строки кода, ни одного теста) — серый «unknown»;
- покрытие упавшего шага (ниже порога, упавший тест) — измеренный процент, но красный;
- тесты красные, если в отчёте есть упавший или сломанный тест, если шаг с тестами не оставил отчёта или если
  он упал, хотя в отчёте всё прошло;
- шаг пропущен или отменён — его отчёту не верят, даже если файл есть: он мог остаться от прошлой попытки.

Процент покрытия — по строкам, округлён вниз до десятых: 89,96% не станет «90.0%». Тест, попавший в несколько
отчётов, считается в каждом.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
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
    "lightgrey": "#9f9f9f",
}
_COVERAGE_SCALE = ((95, "brightgreen"), (90, "green"), (80, "yellowgreen"), (70, "yellow"), (60, "orange"))

# Итоги job и шагов в GitHub Actions: needs.<job>.result и steps.<id>.outcome.
SUCCESS = "success"
RESULTS = frozenset({SUCCESS, "failure", "cancelled", "skipped"})
UNKNOWN = "unknown"
BACKEND_COVERAGE = "backend-coverage"
FRONTEND_COVERAGE = "frontend-coverage"

# Чем может кончиться чтение отчёта, которого нет, который обрезан или не того формата.
_BROKEN = (OSError, ValueError, KeyError, TypeError, ET.ParseError)


@dataclass(frozen=True)
class Badge:
    label: str
    value: str
    color: str

    def svg(self) -> str:
        return render(self.label, self.value, self.color)


def unknown(label: str) -> Badge:
    return Badge(label, UNKNOWN, "lightgrey")


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


def warn(message: str) -> None:
    """Предупреждение в сводку прогона GitHub Actions; при запуске руками — просто строка в выводе."""
    print(f"::warning::{message}")


def coverage_color(percent: Fraction) -> str:
    return next((color for bound, color in _COVERAGE_SCALE if percent >= bound), "red")


def coverage_badge(label: str, covered: int, total: int, *, failed: bool = False) -> Badge:
    """Процент строк: считается точной дробью, показывается округлённым вниз до десятых.

    Шаг упал — процент тот же, но красный. Строк с кодом ноль — серый «unknown», а не 100%: так выглядит,
    например, опечатка в списке файлов покрытия, и зелёным её не показать.
    """
    if total <= 0:
        warn(f"{label}: в отчёте ни одной строки кода")
        return unknown(label)
    percent = Fraction(100 * covered, total)
    tenths = math.floor(percent * 10)
    return Badge(label, f"{tenths // 10}.{tenths % 10}%", "red" if failed else coverage_color(percent))


def backend_lines(path: Path) -> tuple[int, int]:
    """Покрытые строки и строки с кодом из отчёта `coverage json`."""
    totals = json.loads(path.read_text(encoding="utf-8"))["totals"]
    return int(totals["covered_lines"]), int(totals["num_statements"])


def frontend_lines(path: Path) -> tuple[int, int]:
    """Покрытые строки и строки с кодом из json-summary vitest (v8)."""
    lines = json.loads(path.read_text(encoding="utf-8"))["total"]["lines"]
    return int(lines["covered"]), int(lines["total"])


def ran(result: str) -> bool:
    """Шаг дошёл до конца, успешно или нет. У пропущенного или отменённого шага отчёта этого прогона нет, а файл
    с тем же именем может остаться от прошлой попытки (Re-run): такому не верить."""
    return result in (SUCCESS, "failure")


def coverage_from_report(
    label: str, path: Path, read: Callable[[Path], tuple[int, int]], result: str
) -> Badge:
    if not ran(result):
        warn(f"{label}: шаг покрытия {result}, отчёта этого прогона нет")
        return unknown(label)
    try:
        covered, total = read(path)
    except _BROKEN as error:
        warn(f"{label}: отчёт {path} не прочитать: {error}")
        return unknown(label)
    return coverage_badge(label, covered, total, failed=result != SUCCESS)


def count_tests(path: Path) -> tuple[int, int]:
    """Прошедшие и упавшие тесты отчёта JUnit: пропущенные не идут ни туда, ни туда.

    У pytest и vitest отчёты устроены одинаково: <testcase> на тест, внутри <failure> или <error> — упал,
    <skipped> — пропущен (у pytest так же пишется xfail), пусто — прошёл.
    """
    passed = failed = 0
    for case in ET.parse(path).getroot().iter("testcase"):
        tags = {child.tag for child in case}
        if tags & {"failure", "error"}:
            failed += 1
        elif "skipped" not in tags:
            passed += 1
    return passed, failed


def tests_badge(reports: dict[str, Path], results: dict[str, str]) -> Badge:
    """Сумма по отчётам JUnit всех шагов с тестами; results — итоги этих шагов.

    Зелёный — только если каждый шаг успешен и его отчёт прочитан без упавших тестов.
    """
    passed = failed = 0
    no_report: list[str] = []  # шаг не успешен, а отчёта нет: тесты не запускались или оборвались на полпути
    silent: list[str] = []  # шаг упал, а в отчёте ни одного упавшего теста: ошибка вне тестов
    in_doubt = False  # шаг успешен, а отчёта нет или он битый: сумме всё равно не верить
    for name, path in reports.items():
        result = results[name]
        if not ran(result):
            warn(f"tests: шаг {name} {result}, отчёта этого прогона нет")
            no_report.append(name)
            continue
        try:
            step_passed, step_failed = count_tests(path)
        except _BROKEN as error:
            warn(f"tests: отчёт {name} ({path}) не прочитать: {error}")
            if result == SUCCESS:
                in_doubt = True
            else:
                no_report.append(name)
            continue
        passed += step_passed
        failed += step_failed
        if result != SUCCESS and not step_failed:
            silent.append(name)
    if failed or no_report or silent:
        parts = [f"{passed} passed"] if passed else []
        if failed:
            parts.append(f"{failed} failed")
        if silent:
            parts.append(f"{', '.join(silent)} failed")
        if no_report:
            parts.append(f"no report from {', '.join(no_report)}")
        return Badge("tests", ", ".join(parts), "red")
    if in_doubt:
        return unknown("tests")
    if not passed:
        warn("tests: в отчётах ни одного прошедшего теста")
        return unknown("tests")
    return Badge("tests", f"{passed} passed", "brightgreen")


def _pair(text: str) -> tuple[str, str]:
    name, sep, value = text.partition("=")
    if not sep or not name:
        raise argparse.ArgumentTypeError(f"нужно ИМЯ=ЗНАЧЕНИЕ, а не {text!r}")
    return name, value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SVG-бейджи тестов и покрытия для README.")
    parser.add_argument("out_dir", type=Path, help="куда положить SVG")
    parser.add_argument("--backend-coverage", type=Path, help="отчёт `coverage json`")
    parser.add_argument("--frontend-coverage", type=Path, help="json-summary от vitest --coverage")
    parser.add_argument(
        "--junit", type=_pair, nargs="+", default=[], metavar="ИМЯ=ПУТЬ", help="отчёты JUnit XML"
    )
    parser.add_argument(
        "--status", type=_pair, nargs="+", metavar="ИМЯ=ИТОГ", help="итоги шагов, давших входы"
    )
    args = parser.parse_args(argv)

    reports = {name: Path(path) for name, path in args.junit}
    if len(reports) != len(args.junit):
        parser.error("имена в --junit повторяются")
    inputs = list(reports)
    if args.backend_coverage:
        inputs.append(BACKEND_COVERAGE)
    if args.frontend_coverage:
        inputs.append(FRONTEND_COVERAGE)
    if not inputs:
        parser.error("не дано ни одного входа: нечего рисовать")

    if args.status is None:
        results = dict.fromkeys(inputs, SUCCESS)
    else:
        results = dict(args.status)
        # Итог, которого не дали, не додумывается: опечатка в workflow не должна красить бейдж зелёным.
        if len(results) != len(args.status) or sorted(results) != sorted(inputs):
            given = ", ".join(name for name, _ in args.status)
            parser.error(f"--status нужен ровно по разу для {', '.join(inputs)}, а дан для {given}")
        if wrong := sorted(f"{name}={value}" for name, value in results.items() if value not in RESULTS):
            parser.error(f"итог шага — {', '.join(sorted(RESULTS))}, а не {', '.join(wrong)}")

    badges: dict[str, Badge] = {}
    if reports:
        badges["tests.svg"] = tests_badge(reports, results)
    if args.backend_coverage:
        badges["coverage-backend.svg"] = coverage_from_report(
            "backend coverage", args.backend_coverage, backend_lines, results[BACKEND_COVERAGE]
        )
    if args.frontend_coverage:
        badges["coverage-frontend.svg"] = coverage_from_report(
            "frontend coverage", args.frontend_coverage, frontend_lines, results[FRONTEND_COVERAGE]
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, badge in badges.items():
        (args.out_dir / name).write_text(badge.svg(), encoding="utf-8")
        print(f"{name}: {badge.label}: {badge.value} ({badge.color})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
