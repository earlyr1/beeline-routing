"""Слой фактов дня (app/planning/facts.py): единственное место, где важен тип события."""

import re
from pathlib import Path

import pytest

from app.domain.enums import EventType
from app.domain.models import Event
from app.planning import facts
from app.planning.facts import (
    EventRejected,
    event_facts,
    named_engineer,
    named_request,
    new_request,
    subject_request_id,
)
from tests.helpers import req
from tests.planning_helpers import context, new_session

APP = Path(__file__).resolve().parents[1] / "app"
# Модули одного потока событий: тип события в них не проверяется, его знает только слой фактов.
UNIFIED = [
    "planning/session.py",
    "planning/timeline.py",
    "planning/variants.py",
    "planning/delay.py",
    "api/timeline.py",
    "api/routes.py",
    "api/proposals.py",
]


def test_every_event_type_has_a_facts_handler():
    assert set(facts._KINDS) == set(EventType)


@pytest.mark.parametrize("module", UNIFIED)
def test_the_unified_flow_does_not_branch_on_the_event_type(module):
    source = (APP / module).read_text(encoding="utf-8")
    assert "EventType" not in source
    assert not re.search(r"\.type\s*(==|!=|in\b|not in\b)", source)


def test_what_an_event_names():
    urgent = Event(type=EventType.URGENT, time="12:00", request=req("U1", 0, 0, "13:00", "15:00"))
    reassigned = Event(type=EventType.REQUEST_REASSIGNED, time="12:00", request_id="R1", engineer_id="E2")
    delayed = Event(type=EventType.ENGINEER_DELAYED, time="12:00", engineer_id="E1", delay_min=30)
    cancelled = Event(type=EventType.CANCEL, time="12:00", request_id="R2")

    assert [named_engineer(event) for event in (urgent, reassigned, delayed, cancelled)] == [
        None,
        "E2",
        "E1",
        None,
    ]
    assert [named_request(event) for event in (urgent, reassigned, delayed, cancelled)] == [
        None,
        "R1",
        None,
        "R2",
    ]
    assert new_request(urgent) == urgent.request and new_request(cancelled) is None
    assert [subject_request_id(event) for event in (urgent, reassigned, delayed, cancelled)] == [
        "U1",
        "R1",
        None,
        "R2",
    ]


def test_facts_of_an_event_leave_the_session_as_it_was():
    ctx = context()
    session = new_session(ctx)
    edited = session.request("R3").model_copy(update={"duration_min": 45})
    event = Event(type=EventType.REQUEST_UPDATED, time="12:00", request_id="R3", request=edited)

    result = event_facts(session, event, ctx)

    assert next(request for request in result.requests if request.id == "R3").duration_min == 45
    assert session.request("R3").duration_min == 30
    assert result.event.previous_request == session.request("R3")
    # Изменённую заявку солвер планирует заново, даже если бригада к ней уже едет; бригаде её можно отдать.
    assert (result.released, result.subject_request_id) == (frozenset({"R3"}), "R3")
    assert (result.adjust, result.check, result.annotate) == (None, None, None)


def test_only_the_facts_layer_rejects_an_event():
    ctx = context()
    with pytest.raises(EventRejected, match="Заявка NOPE не найдена."):
        event_facts(new_session(ctx), Event(type=EventType.CANCEL, time="12:00", request_id="NOPE"), ctx)
