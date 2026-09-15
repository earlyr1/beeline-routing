from app.domain.enums import EventType, ReasonCode, RequestStatus, Skill
from app.domain.models import Event, Request
from app.planning.explain import build_explanation
from app.planning.session import apply_event
from tests.helpers import eng, req
from tests.planning_helpers import busy_engineer, context, day_requests, new_session


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
