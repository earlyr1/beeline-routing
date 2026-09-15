from app.domain.enums import EventType, ReasonCode, RequestStatus, Skill
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
        "Инженер уже выехал к заявке, поэтому она не переназначается. Отменить заявку можно до начала работы."
    ]
    assert explanation.alternatives == []
    later = _explain(updated, "C")
    assert later.summary.startswith(f"Исполнитель Инженер {busy}")
    assert "в пути" not in later.summary and "начал работу" not in later.summary


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
