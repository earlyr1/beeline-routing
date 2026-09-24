"""scripts/badges.py: бейджи README не бывают зелёными, если за ними провал или нет данных. Без сети."""

import json
import xml.etree.ElementTree as ET

import pytest

from scripts import badges

ALL_PASSED = '<testsuites><testsuite><testcase name="a"/><testcase name="b"/></testsuite></testsuites>'


def junit(tmp_path, name, body):
    path = tmp_path / f"junit-{name}.xml"
    path.write_text(body, encoding="utf-8")
    return path


def backend_report(tmp_path, covered, statements):
    path = tmp_path / "coverage-backend.json"
    path.write_text(json.dumps({"totals": {"covered_lines": covered, "num_statements": statements}}))
    return path


def frontend_report(tmp_path, covered, total):
    path = tmp_path / "coverage-summary.json"
    path.write_text(json.dumps({"total": {"lines": {"covered": covered, "total": total, "pct": 100}}}))
    return path


def run(tmp_path, *args):
    """Запуск как в CI; возвращает бейджи по именам файлов: подпись справа и цвет заливки."""
    out = tmp_path / "out"
    assert badges.main([str(out), *map(str, args)]) == 0
    result = {}
    for svg in sorted(out.glob("*.svg")):
        root = ET.fromstring(svg.read_text(encoding="utf-8"))  # SVG — корректный XML
        label, value = root.get("aria-label").split(": ", 1)
        fill = root.findall(".//{http://www.w3.org/2000/svg}rect")[2].get("fill")  # правая, цветная половина
        result[svg.name] = (label, value, fill)
    return result


BRIGHTGREEN, RED, GREY = "#4c1", "#e05d44", "#9f9f9f"


def test_green_run_draws_all_three_badges(tmp_path):
    drawn = run(
        tmp_path,
        "--backend-coverage", backend_report(tmp_path, 9553, 10000),
        "--frontend-coverage", frontend_report(tmp_path, 973, 1000),
        "--junit", f"backend={junit(tmp_path, 'backend', ALL_PASSED)}",
        f"frontend={junit(tmp_path, 'frontend', ALL_PASSED)}",
        "--status", "backend=success", "frontend=success", "backend-coverage=success",
        "frontend-coverage=success",
    )  # fmt: skip

    assert drawn == {
        "coverage-backend.svg": ("backend coverage", "95.5%", BRIGHTGREEN),
        "coverage-frontend.svg": ("frontend coverage", "97.3%", BRIGHTGREEN),
        "tests.svg": ("tests", "4 passed", BRIGHTGREEN),
    }


def test_failures_and_errors_turn_tests_red_and_skipped_count_nowhere(tmp_path):
    body = (
        "<testsuites><testsuite>"
        '<testcase name="ok"/><testcase name="ok2"><system-out>log</system-out></testcase>'
        '<testcase name="bad"><failure message="assert"/></testcase>'
        '<testcase name="broken"><error message="fixture"/></testcase>'
        '<testcase name="skip"><skipped/></testcase>'
        "</testsuite></testsuites>"
    )
    drawn = run(
        tmp_path, "--junit", f"backend={junit(tmp_path, 'backend', body)}", "--status", "backend=failure"
    )

    assert drawn["tests.svg"] == ("tests", "2 passed, 2 failed", RED)


def test_job_failed_without_report_turns_tests_red(tmp_path):
    drawn = run(
        tmp_path,
        "--junit", f"backend={junit(tmp_path, 'backend', ALL_PASSED)}", f"backend-db={tmp_path / 'none.xml'}",
        "--status", "backend=success", "backend-db=failure",
    )  # fmt: skip

    assert drawn["tests.svg"] == ("tests", "2 passed, no report from backend-db", RED)


def test_step_failed_with_a_clean_report_is_red_too(tmp_path):
    # Например, vitest прошёл все тесты, но упал на необработанной ошибке вне тестов.
    drawn = run(
        tmp_path,
        "--junit", f"frontend={junit(tmp_path, 'frontend', ALL_PASSED)}", "--status", "frontend=failure",
    )  # fmt: skip

    assert drawn["tests.svg"] == ("tests", "2 passed, frontend failed", RED)


def test_skipped_or_cancelled_step_does_not_trust_a_leftover_report(tmp_path):
    # Re-run: файлы прошлой попытки на месте, а шаг в этой попытке не запускался.
    drawn = run(
        tmp_path,
        "--backend-coverage", backend_report(tmp_path, 9900, 10000),
        "--frontend-coverage", frontend_report(tmp_path, 990, 1000),
        "--junit", f"backend={junit(tmp_path, 'backend', ALL_PASSED)}",
        "--status", "backend=cancelled", "backend-coverage=skipped", "frontend-coverage=skipped",
    )  # fmt: skip

    assert drawn == {
        "coverage-backend.svg": ("backend coverage", "unknown", GREY),
        "coverage-frontend.svg": ("frontend coverage", "unknown", GREY),
        "tests.svg": ("tests", "no report from backend", RED),
    }


def test_missing_reports_draw_grey_unknown_instead_of_skipping_the_badge(tmp_path):
    missing = tmp_path / "none.json"
    drawn = run(
        tmp_path,
        "--backend-coverage", missing, "--frontend-coverage", missing,
        "--junit", f"backend={tmp_path / 'none.xml'}",
    )  # fmt: skip

    assert drawn == {
        "coverage-backend.svg": ("backend coverage", "unknown", GREY),
        "coverage-frontend.svg": ("frontend coverage", "unknown", GREY),
        "tests.svg": ("tests", "unknown", GREY),
    }


def test_broken_report_is_unknown_too(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text('{"totals": ')
    drawn = run(
        tmp_path,
        "--backend-coverage",
        broken,
        "--junit",
        f"frontend={junit(tmp_path, 'frontend', '<testsuites')}",
    )

    assert drawn["coverage-backend.svg"] == ("backend coverage", "unknown", GREY)
    assert drawn["tests.svg"] == ("tests", "unknown", GREY)


def test_zero_totals_are_unknown_not_green(tmp_path):
    drawn = run(
        tmp_path,
        "--backend-coverage", backend_report(tmp_path, 0, 0),
        "--frontend-coverage", frontend_report(tmp_path, 0, 0),
        "--junit", f"frontend={junit(tmp_path, 'frontend', '<testsuites/>')}",
    )  # fmt: skip

    assert {name: value for name, (_, value, _) in drawn.items()} == dict.fromkeys(drawn, "unknown")
    assert {fill for _, _, fill in drawn.values()} == {GREY}


def test_failed_coverage_step_keeps_the_measured_value_but_red(tmp_path):
    drawn = run(
        tmp_path,
        "--backend-coverage", backend_report(tmp_path, 9310, 10000),
        "--frontend-coverage", frontend_report(tmp_path, 990, 1000),
        "--status", "backend-coverage=failure", "frontend-coverage=failure",
    )  # fmt: skip

    assert drawn["coverage-backend.svg"] == ("backend coverage", "93.1%", RED)
    assert drawn["coverage-frontend.svg"] == ("frontend coverage", "99.0%", RED)


@pytest.mark.parametrize(
    ("covered", "total", "value", "color"),
    [
        (9496, 10000, "94.9%", "green"),  # до 95 не дотянул: не brightgreen и не «95.0%»
        (8996, 10000, "89.9%", "yellowgreen"),
        (29, 50, "58.0%", "red"),  # ровно 58: округление вниз не съедает десятую
        (1, 3, "33.3%", "red"),
        (10000, 10000, "100.0%", "brightgreen"),
    ],
)
def test_percent_is_floored_to_a_tenth_and_colored_by_the_exact_value(covered, total, value, color):
    assert badges.coverage_badge("c", covered, total) == badges.Badge("c", value, color)


def test_status_must_cover_every_input_exactly(tmp_path):
    report = backend_report(tmp_path, 1, 1)
    with pytest.raises(SystemExit):  # опечатка в имени: итог пропущенного входа не додумывается
        badges.main([str(tmp_path), "--backend-coverage", str(report), "--status", "backend-coverge=success"])
    with pytest.raises(SystemExit):
        badges.main([str(tmp_path), "--backend-coverage", str(report), "--status", "backend-coverage=sucess"])
    with pytest.raises(SystemExit):
        badges.main([str(tmp_path)])
