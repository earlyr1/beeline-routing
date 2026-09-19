from app.domain.enums import EventType, ReasonCode, RequestStatus, Skill, Transport
from app.domain.models import Event, Request
from app.planning.explain import build_explanation
from app.planning.session import apply_event
from app.solvers.assemble import build_plan
from tests.helpers import eng, problem_of, req
from tests.planning_helpers import (
    IN_TRANSIT_TO_B,
    busy_engineer,
    context,
    day_requests,
    new_session,
    other_engineer,
    transit_session,
)


def _explain(session, request_id):
    return build_explanation(session.problem, session.plan, session.request(request_id))


def test_assigned_request_lists_constraints_alternatives_and_factors():
    session = new_session(engineers=[eng("E1"), eng("E2"), eng("E3", skills=[Skill.EMERGENCY])])
    explanation = _explain(session, "R1")
    assert explanation.status == "assigned"
    assert explanation.engineer_id == busy_engineer(session.plan)
    assert [(c.name, c.ok) for c in explanation.constraints] == [
        ("Навык", True),
        ("Транспорт", True),
        ("Временное окно", True),
        ("Смена", True),
    ]
    by_engineer = {a.engineer_id: a for a in explanation.alternatives}
    assert by_engineer["E3"].feasible is False and by_engineer["E3"].reason == "Нет навыка «Локальные работы»"
    idle = [a for a in explanation.alternatives if a.feasible]
    assert idle and "ещё одного инженера" in idle[0].reason
    assert any("ещё одного исполнителя" in factor for factor in explanation.factors)
    assert explanation.summary.startswith("Исполнитель Инженер E")


def test_unassigned_request_reuses_reason_and_checks_constraints():
    requests = day_requests() + [req("X1", 1, 0, "10:00", "12:00", skill=Skill.EMERGENCY)]
    session = new_session(requests=requests, engineers=[eng("E1", skills=[Skill.LOCAL])])
    explanation = _explain(session, "X1")
    assert explanation.status == "unassigned"
    assert explanation.unassigned.reason_code == ReasonCode.NO_SKILL
    assert explanation.constraints[0].ok is False
    assert explanation.alternatives[0].reason == "Нет навыка «Аварийные работы»"


def test_cancelled_request():
    ctx = context()
    session = apply_event(new_session(), Event(type=EventType.CANCEL, time="13:00", request_id="R2"), ctx)
    explanation = _explain(session, "R2")
    assert explanation.status == "cancelled" and session.request("R2").status == RequestStatus.CANCELLED


def test_pinned_visit_is_explained_as_started():
    session = apply_event(
        new_session(), Event(type=EventType.CANCEL, time="13:00", request_id="R2"), context()
    )
    explanation = _explain(session, "R1")
    assert explanation.visit.pinned
    assert "уже началась" in explanation.factors[0]


def test_request_without_coordinates():
    lost = Request(
        id="L1", address="нигде", duration_min=30, window_start="10:00", window_end="12:00", skill=Skill.LOCAL
    )
    session = new_session(requests=day_requests() + [lost])
    explanation = _explain(session, "L1")
    assert explanation.unassigned.reason_code == ReasonCode.ADDRESS_NOT_FOUND
    assert explanation.alternatives == [] and explanation.constraints == []


def test_visit_on_the_way_is_explained_as_departed():
    session = transit_session()
    busy = busy_engineer(session.plan)
    updated = apply_event(
        session,
        Event(type=EventType.ENGINEER_UNAVAILABLE, time=IN_TRANSIT_TO_B, engineer_id=other_engineer(busy)),
        context(),
    )
    explanation = _explain(updated, "B")
    assert not explanation.visit.pinned
    assert explanation.summary == f"Исполнитель Инженер {busy} уже в пути к заявке, работа начнётся в 09:59."
    assert explanation.factors == [
        "Инженер уже выехал к заявке, поэтому она не переназначается. Отменить заявку можно до начала работы.",
        "Обед 12:00–12:45.",
    ]
    assert explanation.alternatives == []
    later = _explain(updated, "C")
    assert later.summary.startswith(f"Исполнитель Инженер {busy}")
    assert "в пути" not in later.summary and "начал работу" not in later.summary


def test_started_car_visit_stays_valid_after_engineer_changes_to_bike():
    ctx = context()
    requests = [
        req("R1", 1, 0, "10:00", "12:00", transport=Transport.CAR),
        req("R3", -1, 0, "15:00", "17:00"),
    ]
    session = new_session(ctx=ctx, requests=requests, engineers=[eng("E1")])
    updated = apply_event(
        session,
        Event(type=EventType.ENGINEER_TRANSPORT_CHANGED, time="13:00", engineer_id="E1", transport="bike"),
        ctx,
    )
    transport = next(c for c in _explain(updated, "R1").constraints if c.name == "Транспорт")
    assert transport.ok is True
    assert transport.detail == (
        "Нужен «Автомобиль», работа запланирована до смены транспорта, сейчас у инженера «Велосипед»"
    )
    later = next(c for c in _explain(updated, "R3").constraints if c.name == "Транспорт")
    assert later.detail == "Требований к транспорту нет, у инженера «Велосипед»"


def _shortcut_problem():
    """Дорожные расстояния OSRM не всегда подчиняются неравенству треугольника: R1 -> R3 напрямую дальше, чем через R2."""
    problem = problem_of(day_requests(), [eng("E1"), eng("E2")])
    r1, r3 = problem.request_node("R1"), problem.request_node("R3")
    problem.travel.base.road_km[r1][r3] = problem.travel.base.road_km[r3][r1] = 20.0
    return problem


def test_summary_never_shows_negative_extra_km():
    problem = _shortcut_problem()
    plan = build_plan(problem, "ortools", {"E1": ["R1", "R2", "R3"]})
    explanation = build_explanation(problem, plan, problem.request("R2"))
    assert explanation.summary.endswith("заявка почти не удлиняет маршрут.")
    assert "-" not in explanation.summary


def test_alternative_with_negative_extra_km_reads_as_almost_no_extra_mileage():
    problem = _shortcut_problem()
    plan = build_plan(problem, "ortools", {"E1": ["R2"], "E2": ["R1", "R3"]})
    explanation = build_explanation(problem, plan, problem.request("R2"))
    alternative = next(a for a in explanation.alternatives if a.engineer_id == "E2")
    assert alternative.feasible and alternative.extra_km == 0.0
    assert alternative.reason == "Может взять: пробег почти не растёт, начало 14:00"
    assert explanation.summary.endswith("заявка добавляет к маршруту 1.6 км.")


def test_request_fixed_by_the_dispatcher_explains_only_that_the_dispatcher_chose_the_brigade():
    fixed = req("R3", -1, 0, "15:00", "17:00").model_copy(update={"fixed_engineer_id": "E2"})
    problem = problem_of(
        [*day_requests()[:2], fixed], [eng("E1"), eng("E2"), eng("E3", skills=[Skill.EMERGENCY])]
    )

    assigned = build_explanation(
        problem, build_plan(problem, "ortools", {"E1": ["R1", "R2"], "E2": ["R3"]}), fixed
    )

    # Разбора выбора нет: бригаду выбрал не планировщик, сравнивать её с другими незачем.
    assert (
        assigned.summary == "Бригаду выбрал диспетчер вручную: Инженер E2. Планировщик заявку не переносит."
    )
    assert (assigned.factors, assigned.alternatives) == ([], [])
    # Проверки остаются: диспетчер видит, что ручное назначение не ломает окно и смену.
    assert [(check.name, check.ok) for check in assigned.constraints] == [
        ("Навык", True),
        ("Транспорт", True),
        ("Временное окно", True),
        ("Смена", True),
    ]

    unassigned = build_explanation(problem, build_plan(problem, "ortools", {"E1": ["R1", "R2"]}), fixed)

    assert unassigned.status == "unassigned"
    assert unassigned.alternatives == []
    assert unassigned.summary.startswith("Заявку закрепил диспетчер за Инженер E2. ")


def test_alternative_too_far_for_its_transport_names_the_distance():
    problem = problem_of(
        [req("R1", 60, 0, "10:00", "16:00")],
        [eng("E1", start=(58, 0)), eng("E2", transport=Transport.BIKE)],
    )
    explanation = build_explanation(
        problem, build_plan(problem, "ortools", {"E1": ["R1"], "E2": []}), problem.request("R1")
    )
    alternative = next(a for a in explanation.alternatives if a.engineer_id == "E2")
    assert alternative.feasible is False
    assert alternative.reason == (
        "Слишком далеко для транспорта «Велосипед»: 78 км до заявки при пределе 15 км"
    )


def test_unassigned_beyond_the_leg_limit_fails_the_transport_check():
    problem = problem_of([req("R1", 60, 0, "10:00", "16:00")], [eng("E1", transport=Transport.BIKE)])
    explanation = build_explanation(
        problem, build_plan(problem, "ortools", {"E1": []}), problem.request("R1")
    )
    assert explanation.unassigned.reason_code == ReasonCode.NO_TRANSPORT
    assert [(c.name, c.ok) for c in explanation.constraints][:2] == [("Навык", True), ("Транспорт", False)]
    assert "ближайшая бригада в 78 км, это дальше предела плеча" in explanation.constraints[1].detail


def test_unreachable_request_does_not_get_a_green_window_check():
    """До R 47 км от точки бригады и 23 км от её заявки X: причина одна — предел плеча, и тексты не спорят."""
    problem = problem_of(
        [req("X", 18, 0, "10:00", "16:00"), req("R", 36, 0, "10:00", "11:30")],
        [eng("E1", transport=Transport.PUBLIC)],
    )
    explanation = build_explanation(
        problem, build_plan(problem, "ortools", {"E1": ["X"]}), problem.request("R")
    )
    [alternative] = explanation.alternatives
    assert alternative.reason == "Не помещается в окно или смену вместе со своими заявками"
    assert [(c.name, c.ok) for c in explanation.constraints] == [
        ("Навык", True),
        ("Транспорт", False),
        ("Временное окно", False),
        ("Смена", False),
    ]
    assert explanation.constraints[2].detail == (
        "До заявки не доезжает ни одна подходящая бригада: успеть к окну 10:00–11:30 некому"
    )


def test_equipment_check_counts_what_is_left_after_the_plan_not_the_morning_stock():
    """Две заявки с оборудованием на бригаду с запасом 1: строка проверки не спорит с причиной над собой.

    Запас у бригады есть только с утра, и R0 его забрал. Причина неназначения, альтернатива и строка
    «Оборудование» должны говорить об одном и том же — что везти нечего.
    """
    requests = [req(f"R{k}", 1, k / 10, "10:00", "16:00", duration=30, equipment=True) for k in range(2)]
    problem = problem_of(requests, [eng("E1", equipment_stock=1)])
    explanation = build_explanation(
        problem, build_plan(problem, "ortools", {"E1": ["R0"]}), problem.request("R1")
    )
    assert "не осталось оборудования" in explanation.summary
    assert [(c.name, c.ok) for c in explanation.constraints] == [
        ("Навык", True),
        ("Транспорт", True),
        ("Временное окно", True),
        ("Смена", True),
        ("Оборудование", False),
    ]
    assert explanation.constraints[4].detail == (
        "Нужна одна единица; подходящих бригад, у которых она ещё осталась: 0"
    )
    assert explanation.alternatives[0].reason == "Оборудование кончилось: утром бригада взяла 1 ед."


def test_equipment_check_stays_green_while_the_brigade_still_has_a_unit():
    """Та же пара заявок, но запас 2: R1 без инженера не из-за оборудования, и строка остаётся зелёной."""
    requests = [req(f"R{k}", 1, k / 10, "10:00", "16:00", duration=30, equipment=True) for k in range(2)]
    problem = problem_of(requests, [eng("E1", equipment_stock=2)])
    explanation = build_explanation(
        problem, build_plan(problem, "ortools", {"E1": ["R0"]}), problem.request("R1")
    )
    assert [(c.name, c.ok) for c in explanation.constraints][4] == ("Оборудование", True)
    assert explanation.constraints[4].detail == (
        "Нужна одна единица; подходящих бригад, у которых она ещё осталась: 1"
    )
