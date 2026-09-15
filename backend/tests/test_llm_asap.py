"""Помощник и срочная заявка «как можно скорее»: окно не названо, окно задаёт backend."""

import json

from app.domain.enums import EventType, Priority
from app.domain.models import Event
from app.ingest.geocode import GeoResult
from app.llm.client import LlmResult, ToolCall
from app.llm.interpret import interpret
from app.llm.prompt import build_messages
from app.llm.tools import TOOL_SPECS, json_mode_instruction
from app.planning.session import apply_event
from tests.llm_helpers import ids, named_session
from tests.planning_helpers import context

URGENT = {
    "address": "Москва, Дубининская улица, 59к2",
    "duration_min": 60,
    "skill": "emergency",
    "transport_required": "car",
    "time": "13:00",
    "rationale": "Авария, просят приехать как можно скорее",
}
MISSING_WINDOW = "Укажите окно визита или отметьте, что заявка как можно скорее."


def located():
    return context(geocode=lambda address, district: GeoResult(55.75, 37.61, "house", address))


def run(calls, ctx, session=None):
    return interpret(LlmResult(calls=calls), session or named_session(ctx), ctx, ids())


def test_urgent_request_tool_spec_allows_asap_without_window():
    spec = TOOL_SPECS["propose_urgent_request"]
    assert spec["description"] == (
        "Предложить добавить срочную заявку. Если окно не названо и нужно как можно скорее, "
        "передай asap=true без окна."
    )
    properties = spec["parameters"]["properties"]
    assert properties["asap"] == {
        "type": "boolean",
        "description": "true, если просят приехать как можно скорее и окно не названо",
    }
    assert {"window_start", "window_end"} <= set(properties)
    assert spec["parameters"]["required"] == ["address", "duration_min", "skill", "rationale"]
    update = TOOL_SPECS["propose_request_update"]["parameters"]
    assert update["properties"]["asap"]["type"] == "boolean"
    assert "asap" not in update["required"]
    schemas = json.loads(json_mode_instruction().split("\n", 1)[1])
    assert schemas["propose_urgent_request"]["required"] == ["address", "duration_min", "skill", "rationale"]
    assert schemas["propose_urgent_request"]["properties"]["asap"]["type"] == "boolean"
    assert schemas["propose_request_update"]["properties"]["asap"]["type"] == "boolean"


def test_urgent_request_asap_without_window_becomes_pending_draft():
    ctx = located()
    out = run([ToolCall("propose_urgent_request", {**URGENT, "asap": True})], ctx)

    assert out.clarifications == []
    [draft] = out.drafts
    request = draft.event.request
    assert (draft.event.type, draft.event.time, draft.error) == (EventType.URGENT, 780, None)
    assert (request.id, request.asap, request.priority) == ("URG-AI-001", True, Priority.URGENT)
    # Окно задаёт backend: от времени события до конца смен (у инженеров смены до 18:00).
    assert (request.window_start, request.window_end) == (780, 1080)
    assert draft.rationale == URGENT["rationale"]

    # Окно, названное вместе с asap=true, не используется.
    named = run(
        [
            ToolCall(
                "propose_urgent_request",
                {**URGENT, "asap": True, "window_start": "16:00", "window_end": "15:00"},
            )
        ],
        ctx,
    )
    [draft] = named.drafts
    assert (draft.error, draft.event.request.window_start, draft.event.request.window_end) == (
        None,
        780,
        1080,
    )


def test_urgent_request_without_window_and_without_asap_asks_for_window():
    ctx = located()
    out = run(
        [
            ToolCall("propose_urgent_request", URGENT),
            ToolCall("propose_urgent_request", {**URGENT, "asap": False, "window_start": "14:00"}),
            ToolCall("propose_urgent_request", {**URGENT, "asap": None, "window_end": "14:00"}),
        ],
        ctx,
    )
    assert out.drafts == []
    assert out.clarifications == [MISSING_WINDOW, MISSING_WINDOW, MISSING_WINDOW]

    with_window = run(
        [ToolCall("propose_urgent_request", {**URGENT, "window_start": "13:00", "window_end": "15:00"})], ctx
    )
    [draft] = with_window.drafts
    assert (draft.error, draft.event.request.asap) == (None, False)
    assert (draft.event.request.window_start, draft.event.request.window_end) == (780, 900)


def test_approved_asap_draft_is_normalized_again_at_the_approval_time():
    ctx = located()
    session = named_session(ctx)
    [draft] = run([ToolCall("propose_urgent_request", {**URGENT, "asap": True})], ctx, session).drafts
    later = apply_event(session, Event(type=EventType.CANCEL, time="13:30", request_id="R3"), ctx)

    updated = apply_event(later, draft.event.model_copy(update={"time": 810}), ctx)

    stored = updated.request("URG-AI-001")
    assert (stored.asap, stored.window_start, stored.window_end) == (True, 810, 1080)


def test_request_update_with_asap():
    ctx = context()
    session = named_session(ctx)
    before = session.request("R3")
    arguments = {"request_id": "R3", "time": "13:00", "rationale": "Просят приехать как можно скорее"}

    out = run(
        [
            ToolCall(
                "propose_request_update",
                {
                    **arguments,
                    "asap": True,
                    "priority": "urgent",
                    "window_start": "17:00",
                    "window_end": "16:00",
                },
            )
        ],
        ctx,
        session,
    )

    assert out.clarifications == []
    [draft] = out.drafts
    assert (draft.event.type, draft.event.request_id, draft.error) == (EventType.REQUEST_UPDATED, "R3", None)
    assert draft.event.request == before.model_copy(
        update={"asap": True, "priority": Priority.URGENT, "window_start": 780, "window_end": 1080}
    )
    assert draft.event.previous_request == before

    # Правки в то же время события: визит к R3 ещё не начат.
    asap_day = apply_event(session, draft.event, ctx)
    edits = run(
        [
            ToolCall("propose_request_update", {**arguments, "duration_min": 45}),
            ToolCall(
                "propose_request_update",
                {**arguments, "window_start": "16:00", "window_end": "17:30"},
            ),
            ToolCall("propose_request_update", {**arguments, "asap": False}),
        ],
        ctx,
        asap_day,
    )
    assert edits.clarifications == []
    kept, named, off = edits.drafts
    # Правка заявки «как можно скорее» не перезапускает часы ожидания.
    assert (kept.error, kept.event.request.asap, kept.event.request.window_start) == (None, True, 780)
    assert kept.event.request.duration_min == 45
    # Названное окно означает, что заявка больше не «как можно скорее».
    assert (named.error, named.event.request.asap) == (None, False)
    assert (named.event.request.window_start, named.event.request.window_end) == (960, 1050)
    assert (off.error, off.event.request.asap, off.event.previous_request.asap) == (None, False, True)


def test_prompt_mentions_asap_rule_and_asap_requests():
    ctx = located()
    session = named_session(ctx)
    system, _ = build_messages("Авария на Дубининской, приезжайте как можно скорее", session)
    assert "как можно скорее" in system["content"] and "asap=true" in system["content"]

    [draft] = run([ToolCall("propose_urgent_request", {**URGENT, "asap": True})], ctx, session).drafts
    updated = apply_event(session, draft.event, ctx)
    _, user = build_messages("Что с аварией?", updated)
    assert '"window": "как можно скорее с 13:00"' in user["content"]
    assert '"asap": true' in user["content"] and '"asap": false' in user["content"]
