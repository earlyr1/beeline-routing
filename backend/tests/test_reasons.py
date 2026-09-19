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


def test_nobody_reaches_the_request_on_a_bicycle_or_public_transport():
    reason = _reason(
        [req("R1", 60, 0, "10:00", "16:00")],
        [eng("E1", transport=Transport.BIKE), eng("E2", transport=Transport.PUBLIC)],
        "R1",
    )
    assert reason.reason_code == ReasonCode.NO_TRANSPORT
    assert reason.reason_text == (
        "Нет инженера, который доедет: ближайшая подходящая бригада в 78 км от заявки, "
        "а «Велосипед» не дальше 15 км, «Общественный транспорт и пешком» не дальше 25 км."
    )


def test_request_reachable_only_through_its_own_stop_is_not_explained_by_the_window():
    """До R 47 км от точки бригады и 23 км от её заявки X.

    Прогон «даже без других заявок» поехал бы по запрещённому плечу и показал бы время поездки, которой не будет.
    """
    reason = _reason(
        [req("X", 18, 0, "10:00", "16:00"), req("R", 36, 0, "10:00", "12:00")],
        [eng("E1", transport=Transport.PUBLIC)],
        "R",
        {"E1": ["X"]},
    )
    assert reason.reason_code == ReasonCode.NO_TRANSPORT
    assert reason.reason_text == (
        "Нет инженера, который доедет: ближайшая подходящая бригада в 47 км от заявки, "
        "а «Общественный транспорт и пешком» не дальше 25 км. "
        "По пути от своих заявок доехать можно, но вместе с ними заявка не помещается."
    )


def test_no_equipment_left_in_any_brigade():
    """Заявку с оборудованием взять некому: утренний запас бригад уже разобран."""
    reason = _reason(
        [req("R1", 1, 0, "10:00", "16:00", equipment=True)], [eng("E1", equipment_stock=0)], "R1"
    )
    assert reason.reason_code == ReasonCode.NO_FREE_ENGINEER
    assert reason.reason_text == (
        "Ни у одной подходящей бригады не осталось оборудования: утренний запас разобран (Инженер E1)."
    )


def test_equipment_spent_by_the_route_counts_as_spent():
    requests = [req(f"R{k}", 1, k / 10, "10:00", "16:00", duration=30, equipment=True) for k in range(2)]
    problem = problem_of(requests, [eng("E1", equipment_stock=1)])
    reason = unassigned_reason(problem, "R1", {"E1": ["R0"]})
    assert reason.reason_code == ReasonCode.NO_FREE_ENGINEER
    assert "не осталось оборудования" in reason.reason_text
