import json

import httpx
import pytest

from app.domain.enums import EventType, Transport
from app.llm.client import LlmError, ToolCall, parse_json_actions
from app.llm.interpret import interpret
from app.llm.prompt import build_messages
from app.llm.tools import TOOL_SPECS, json_mode_instruction
from tests.llm_helpers import MESSAGES, ScriptedProvider, completion, ids, named_session, tool_call
from tests.planning_helpers import context

TRANSPORT_CHANGE = {
    "engineer_id": "Арташкин",
    "transport": "bike",
    "time": "13:00",
    "rationale": "Машина сломалась, пересел на велосипед",
}


def test_transport_change_tool_spec():
    spec = TOOL_SPECS["propose_engineer_transport_change"]
    assert spec["description"] == (
        "Предложить сменить тип транспорта инженера с указанного времени: машина сломалась, "
        "пересел на велосипед, выдали автомобиль и т.п."
    )
    properties = spec["parameters"]["properties"]
    assert set(properties) == {"engineer_id", "transport", "time", "rationale"}
    assert properties["transport"]["enum"] == ["car", "foot", "bike", "public"]
    assert spec["parameters"]["required"] == ["engineer_id", "transport", "rationale"]
    schemas = json.loads(json_mode_instruction().split("\n", 1)[1])
    assert schemas["propose_engineer_transport_change"]["properties"]["transport"]["enum"] == [
        "car",
        "foot",
        "bike",
        "public",
    ]


@pytest.mark.parametrize("mode", ["tools", "json"])
def test_transport_change_goes_from_provider_to_pending_draft(mode):
    if mode == "tools":
        provider = ScriptedProvider(
            completion(tool_calls=[tool_call("propose_engineer_transport_change", TRANSPORT_CHANGE)])
        )
    else:
        action = {"actions": [{"tool": "propose_engineer_transport_change", "arguments": TRANSPORT_CHANGE}]}
        provider = ScriptedProvider(
            httpx.Response(400, json={"error": {"message": "tools are not supported"}}),
            completion(content=json.dumps(action, ensure_ascii=False)),
        )
    ctx = context()
    session = named_session(ctx)
    result = provider.client().complete(build_messages("Арташкин пересел на велосипед", session))

    assert result.mode == mode
    interpretation = interpret(result, session, ctx, ids())
    assert interpretation.clarifications == []
    [draft] = interpretation.drafts
    assert (draft.event.type, draft.event.engineer_id, draft.event.transport, draft.event.time) == (
        EventType.ENGINEER_TRANSPORT_CHANGED,
        "E1",
        Transport.BIKE,
        780,
    )
    assert draft.event.previous_transport == Transport.CAR and draft.error is None
    last = provider.bodies()[-1]
    if mode == "tools":
        assert "propose_engineer_transport_change" in [tool["function"]["name"] for tool in last["tools"]]
    else:
        assert "tools" not in last
        assert '"propose_engineer_transport_change"' in last["messages"][0]["content"]
        assert "смена транспорта" in last["messages"][0]["content"]


def test_tools_mode_sends_six_tools_and_parses_calls():
    provider = ScriptedProvider(
        completion(
            tool_calls=[
                tool_call(
                    "propose_cancel", {"request_id": "R2", "time": "13:00", "rationale": "Клиент отказался"}
                ),
                tool_call("ask_clarification", {"question": "Кто именно заболел?"}, "call_2"),
            ]
        )
    )
    result = provider.client(mode="tools").complete(MESSAGES)

    assert result.mode == "tools"
    assert result.calls == [
        ToolCall("propose_cancel", {"request_id": "R2", "time": "13:00", "rationale": "Клиент отказался"}),
        ToolCall("ask_clarification", {"question": "Кто именно заболел?"}),
    ]
    request = provider.requests[0]
    body = provider.bodies()[0]
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert body["model"] == "test-model" and body["temperature"] == 0 and body["tool_choice"] == "auto"
    assert [tool["function"]["name"] for tool in body["tools"]] == [
        "propose_urgent_request",
        "propose_cancel",
        "propose_restore",
        "propose_engineer_unavailable",
        "propose_engineer_transport_change",
        "ask_clarification",
    ]
    assert body["messages"] == MESSAGES


def test_json_mode_parses_fenced_actions_and_sends_no_tools():
    content = (
        "Вот предложения:\n```json\n"
        '{"actions": [{"tool": "propose_engineer_unavailable", '
        '"arguments": {"engineer_id": "E1", "time": "14:00", "rationale": "Заболел"}}]}\n```'
    )
    provider = ScriptedProvider(completion(content=content))
    result = provider.client(mode="json").complete(MESSAGES)

    assert result.mode == "json"
    assert result.calls == [
        ToolCall(
            "propose_engineer_unavailable", {"engineer_id": "E1", "time": "14:00", "rationale": "Заболел"}
        )
    ]
    body = provider.bodies()[0]
    assert "tools" not in body
    assert len(body["messages"]) == 2 and '"actions"' in body["messages"][0]["content"]
    assert '"propose_engineer_transport_change"' in body["messages"][0]["content"]


def test_auto_mode_falls_back_to_json_when_provider_rejects_tools():
    provider = ScriptedProvider(
        httpx.Response(400, json={"error": {"message": "tools are not supported"}}),
        completion(content='{"actions": []}'),
    )
    result = provider.client().complete(MESSAGES)
    first, second = provider.bodies()
    assert result.mode == "json" and result.calls == []
    assert "tools" in first and "tools" not in second


def test_tools_mode_reports_provider_errors_in_russian():
    provider = ScriptedProvider(httpx.Response(400, json={"error": {"message": "bad"}}))
    with pytest.raises(LlmError, match="ответил ошибкой 400"):
        provider.client(mode="tools").complete(MESSAGES)

    provider = ScriptedProvider(httpx.Response(401, json={"error": {"message": "no key"}}))
    with pytest.raises(LlmError, match="LLM_API_KEY"):
        provider.client().complete(MESSAGES)


def test_connection_failure_becomes_llm_error():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    provider = ScriptedProvider(refuse)
    with pytest.raises(LlmError, match="нет связи"):
        provider.client().complete(MESSAGES)


def test_bad_arguments_and_plain_text_are_returned_not_raised():
    provider = ScriptedProvider(completion(tool_calls=[tool_call("propose_cancel", "{not json")]))
    call = provider.client(mode="tools").complete(MESSAGES).calls[0]
    assert call.arguments is None and "не являются JSON" in call.error

    provider = ScriptedProvider(completion(content="Уточните, какую заявку отменить?"))
    result = provider.client(mode="tools").complete(MESSAGES)
    assert result.calls == [] and result.text == "Уточните, какую заявку отменить?"


def test_parse_json_actions_edge_cases():
    assert parse_json_actions("просто текст") is None
    assert parse_json_actions('{"answer": 1}') is None
    calls = parse_json_actions(
        '{"actions": [{"arguments": {}}, {"tool": "propose_cancel", "arguments": [1]}]}'
    )
    assert [(call.name, call.arguments) for call in calls] == [("", None), ("propose_cancel", None)]
