from app.domain.enums import ReasonCode, Skill, Transport
from app.solvers.reasons import unassigned_reason
from tests.helpers import eng, problem_of, req


def _reason(requests, engineers, request_id, sequences=None):
    problem = problem_of(requests, engineers)
    return unassigned_reason(problem, request_id, sequences or {})


def test_no_skill():
    reason = _reason(
        [req("R1", 1, 0, "10:00", "12:00", skill=Skill.EMERGENCY)], [eng("E1", skills=[Skill.LOCAL])], "R1"
    )
    assert reason.reason_code == ReasonCode.NO_SKILL
    assert reason.reason_text == "Нет инженера с навыком «Аварийные работы»."


def test_no_transport():
    reason = _reason(
        [req("R1", 1, 0, "10:00", "12:00", transport=Transport.CAR)],
        [eng("E1", transport=Transport.PUBLIC)],
        "R1",
    )
    assert reason.reason_code == ReasonCode.NO_TRANSPORT


def test_all_suitable_engineers_unavailable():
    reason = _reason([req("R1", 1, 0, "10:00", "12:00")], [eng("E1", available=False)], "R1")
    assert reason.reason_code == ReasonCode.NO_FREE_ENGINEER
    assert "недоступны" in reason.reason_text


def test_does_not_fit_window_even_alone():
    reason = _reason([req("R1", 40, 0, "09:00", "09:30")], [eng("E1")], "R1")
    assert reason.reason_code == ReasonCode.DOES_NOT_FIT
    assert "окно 09:00–09:30" in reason.reason_text


def test_no_free_engineer_when_busy():
    requests = [
        req("R1", 1, 0, "10:00", "10:10", duration=120),
        req("R2", 1, 0, "10:00", "10:10", duration=30),
    ]
    reason = _reason(requests, [eng("E1")], "R2", sequences={"E1": ["R1"]})
    assert reason.reason_code == ReasonCode.NO_FREE_ENGINEER
    assert "освобождается Инженер E1 в 12:00" in reason.reason_text


def test_fixed_request_is_explained_by_its_brigade_only():
    requests = [
        req("R1", 1, 0, "10:00", "10:10", duration=120),
        req("R2", 1, 0, "10:00", "10:10").model_copy(update={"fixed_engineer_id": "E1"}),
    ]
    # E2 свободен, но заявку закрепили за E1.
    reason = _reason(requests, [eng("E1"), eng("E2")], "R2", sequences={"E1": ["R1"]})
    assert reason.reason_code == ReasonCode.NO_FREE_ENGINEER
    assert reason.reason_text == (
        "Заявку закрепил диспетчер за Инженер E1. Нет свободных исполнителей на окно 10:00–10:10: подходящие "
        "инженеры (1) заняты другими заявками. Раньше всех освобождается Инженер E1 в 12:00."
    )


def test_fixed_request_of_a_brigade_missing_from_the_day_is_explained_by_everyone():
    fixed = req("R1", 40, 0, "09:00", "09:30").model_copy(update={"fixed_engineer_id": "E9"})
    reason = _reason([fixed], [eng("E1")], "R1")
    assert reason.reason_code == ReasonCode.DOES_NOT_FIT
    assert reason.reason_text.startswith("Работа не помещается")
