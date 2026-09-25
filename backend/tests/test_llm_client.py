import json
import logging

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
    assert properties["transport"]["enum"] == ["car", "bike", "public"]
    assert spec["parameters"]["required"] == ["engineer_id", "transport", "rationale"]
    schemas = json.loads(json_mode_instruction().split("\n", 1)[1])
    assert schemas["propose_engineer_transport_change"]["properties"]["transport"]["enum"] == [
        "car",
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


REQUEST_UPDATE = {
    "request_id": "Дубининская",
    "window_start": "18:00",
    "window_end": "20:00",
    "time": "13:00",
    "rationale": "Клиент просит перенести визит на вечер",
}


def test_request_update_tool_spec():
    spec = TOOL_SPECS["propose_request_update"]
    assert spec["description"] == (
        "Предложить изменить заявку: перенести окно, поменять длительность, адрес, навык, приоритет "
        "или требование к транспорту."
    )
    properties = spec["parameters"]["properties"]
    assert set(properties) == {
        "request_id",
        "address",
        "window_start",
        "window_end",
        "duration_min",
        "skill",
        "priority",
        "transport_required",
        "asap",
        "time",
        "rationale",
    }
    assert properties["skill"]["enum"] == ["local", "connection", "emergency"]
    assert properties["priority"]["enum"] == ["normal", "urgent"]
    assert properties["transport_required"]["enum"] == ["car", "bike", "public", "none"]
    assert spec["parameters"]["required"] == ["request_id", "rationale"]
    schemas = json.loads(json_mode_instruction().split("\n", 1)[1])
    assert schemas["propose_request_update"]["required"] == ["request_id", "rationale"]
    assert schemas["propose_request_update"]["description"] == spec["description"]


@pytest.mark.parametrize("mode", ["tools", "json"])
def test_request_update_goes_from_provider_to_pending_draft(mode):
    if mode == "tools":
        provider = ScriptedProvider(
            completion(tool_calls=[tool_call("propose_request_update", REQUEST_UPDATE)])
        )
    else:
        action = {"actions": [{"tool": "propose_request_update", "arguments": REQUEST_UPDATE}]}
        provider = ScriptedProvider(
            httpx.Response(400, json={"error": {"message": "tools are not supported"}}),
            completion(content=json.dumps(action, ensure_ascii=False)),
        )
    ctx = context()
    session = named_session(ctx)
    result = provider.client().complete(build_messages("Дубининскую перенести на вечер", session))

    assert result.mode == mode
    interpretation = interpret(result, session, ctx, ids())
    assert interpretation.clarifications == []
    [draft] = interpretation.drafts
    before = session.request("R2")
    assert (draft.event.type, draft.event.request_id, draft.event.time, draft.error) == (
        EventType.REQUEST_UPDATED,
        "R2",
        780,
        None,
    )
    assert draft.event.request == before.model_copy(update={"window_start": 1080, "window_end": 1200})
    assert draft.event.previous_request == before
    last = provider.bodies()[-1]
    if mode == "tools":
        assert "propose_request_update" in [tool["function"]["name"] for tool in last["tools"]]
    else:
        assert "tools" not in last
        assert '"propose_request_update"' in last["messages"][0]["content"]
        assert "изменение заявки" in last["messages"][0]["content"]


ENGINEER_DELAY = {
    "engineer_id": "Белузин",
    "delay_min": 40,
    "time": "13:00",
    "rationale": "Застрял в пробке на 40 минут",
}


def test_engineer_delay_tool_spec():
    spec = TOOL_SPECS["propose_engineer_delay"]
    assert spec["description"] == (
        "Предложить отметить задержку инженера: застрял в пробке, работа на объекте затянулась и т.п. "
        "delay_min — на сколько минут задерживается."
    )
    properties = spec["parameters"]["properties"]
    assert set(properties) == {"engineer_id", "delay_min", "time", "rationale"}
    assert (
        properties["delay_min"]["type"],
        properties["delay_min"]["minimum"],
        properties["delay_min"]["maximum"],
    ) == (
        "integer",
        5,
        480,
    )
    assert spec["parameters"]["required"] == ["engineer_id", "delay_min", "rationale"]
    schemas = json.loads(json_mode_instruction().split("\n", 1)[1])
    assert schemas["propose_engineer_delay"]["description"] == spec["description"]
    assert schemas["propose_engineer_delay"]["properties"]["delay_min"]["maximum"] == 480


@pytest.mark.parametrize("mode", ["tools", "json"])
def test_engineer_delay_goes_from_provider_to_pending_draft(mode):
    if mode == "tools":
        provider = ScriptedProvider(
            completion(tool_calls=[tool_call("propose_engineer_delay", ENGINEER_DELAY)])
        )
    else:
        action = {"actions": [{"tool": "propose_engineer_delay", "arguments": ENGINEER_DELAY}]}
        provider = ScriptedProvider(
            httpx.Response(400, json={"error": {"message": "tools are not supported"}}),
            completion(content=json.dumps(action, ensure_ascii=False)),
        )
    ctx = context()
    session = named_session(ctx)
    result = provider.client().complete(build_messages("Белузин застрял в пробке на 40 минут", session))

    assert result.mode == mode
    interpretation = interpret(result, session, ctx, ids())
    assert interpretation.clarifications == []
    [draft] = interpretation.drafts
    assert (
        draft.event.type,
        draft.event.engineer_id,
        draft.event.delay_min,
        draft.event.time,
        draft.error,
    ) == (
        EventType.ENGINEER_DELAYED,
        "E2",
        40,
        780,
        None,
    )
    last = provider.bodies()[-1]
    if mode == "tools":
        assert "propose_engineer_delay" in [tool["function"]["name"] for tool in last["tools"]]
    else:
        assert "tools" not in last
        assert '"propose_engineer_delay"' in last["messages"][0]["content"]
        assert "задержка инженера" in last["messages"][0]["content"]


def test_tools_mode_sends_eight_tools_and_parses_calls():
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
        "propose_engineer_unavailable",
        "propose_engineer_transport_change",
        "propose_request_update",
        "propose_engineer_delay",
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
    assert '"propose_request_update"' in body["messages"][0]["content"]
    assert '"propose_engineer_delay"' in body["messages"][0]["content"]


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


# Пул моделей: LLM_MODEL=первая,вторая — вторая спрашивается, только если первая недоступна.

POOL = ("gpt://folder/yandexgpt/rc", "gpt://folder/qwen3-235b-a22b-fp8/latest")
FIRST, SECOND = POOL


def status_error(status, message="недоступна"):
    return httpx.Response(status, json={"error": {"message": message}})


def empty_completion():
    return {**completion(content="ничего"), "choices": []}


def timeout(request):
    raise httpx.ReadTimeout("read timed out", request=request)


def refuse(request):
    raise httpx.ConnectError("connection refused", request=request)


def cancel_call():
    return completion(tool_calls=[tool_call("propose_cancel", {"request_id": "R2", "rationale": "Отказ"})])


UNAVAILABLE = [
    (status_error(429, "rate limit"), "частоту запросов"),
    (status_error(500), "ошибкой 500"),
    (status_error(503), "ошибкой 503"),
    (timeout, "таймаут 30 с"),
    (refuse, "нет связи (connection refused)"),
    (empty_completion(), "пустой ответ"),
]


# 404 в auto сначала ведёт к JSON-режиму той же модели — у него свой тест ниже.
@pytest.mark.parametrize(
    ("mode", "failure", "reason"),
    [("tools", status_error(404, "No endpoints found"), "404")]
    + [(mode, failure, reason) for mode in ("tools", "auto") for failure, reason in UNAVAILABLE],
)
def test_pool_moves_to_the_next_model_when_the_first_is_unavailable(mode, failure, reason, caplog):
    caplog.set_level(logging.INFO, logger="app.llm.client")
    # Повторы SDK — по умолчанию пула: их нет, иначе второй запрос ушёл бы той же первой модели.
    provider = ScriptedProvider(failure, cancel_call())
    result = provider.client(mode=mode, models=POOL, max_retries=None).complete(MESSAGES)

    # И в auto недоступная модель не спрашивается второй раз в JSON-режиме: вторая модель — сразу с инструментами.
    assert provider.models() == [FIRST, SECOND]
    assert all("tools" in body for body in provider.bodies())
    assert (result.model, result.mode) == (SECOND, "tools")
    assert [call.name for call in result.calls] == ["propose_cancel"]
    failed = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(failed) == 1 and FIRST in failed[0] and reason in failed[0]
    assert any(f"ответила модель {SECOND}" in record.getMessage() for record in caplog.records)


def test_pool_asks_the_next_model_after_404_in_json_mode_too():
    """404 на инструменты в auto — ещё не «модели нет»: так отвечают и модели без tools. Решает 404 JSON-режима."""
    provider = ScriptedProvider(
        status_error(404, "No endpoints found"), status_error(404, "No endpoints found"), cancel_call()
    )
    result = provider.client(models=POOL).complete(MESSAGES)

    assert provider.models() == [FIRST, FIRST, SECOND]
    first_tools, first_json, second = provider.bodies()
    assert "tools" in first_tools and "tools" not in first_json and "tools" in second
    assert (result.model, result.mode) == (SECOND, "tools")


def test_pool_in_json_mode_moves_to_the_next_model_too():
    provider = ScriptedProvider(
        status_error(404, "No endpoints found"), completion(content='{"actions": []}')
    )
    result = provider.client(mode="json", models=POOL, max_retries=None).complete(MESSAGES)

    assert provider.models() == [FIRST, SECOND]
    assert not any("tools" in body for body in provider.bodies())
    assert (result.model, result.mode, result.calls) == (SECOND, "json", [])


def test_pool_first_model_rejecting_tools_answers_in_json_mode():
    provider = ScriptedProvider(
        status_error(400, "tools are not supported"), completion(content='{"actions": []}')
    )
    result = provider.client(models=POOL).complete(MESSAGES)

    assert provider.models() == [FIRST, FIRST]
    assert (result.model, result.mode, result.calls) == (FIRST, "json", [])


def test_pool_json_mode_unavailable_moves_on_but_a_bad_request_does_not():
    provider = ScriptedProvider(
        status_error(400, "tools are not supported"), status_error(429, "rate limit"), cancel_call()
    )
    result = provider.client(models=POOL).complete(MESSAGES)
    assert provider.models() == [FIRST, FIRST, SECOND]
    assert (result.model, result.mode) == (SECOND, "tools")

    # 400 и в JSON-режиме — ошибка самого запроса: вторая модель его тоже не примет.
    provider = ScriptedProvider(status_error(400, "bad"), status_error(400, "bad"))
    with pytest.raises(LlmError, match="ответил ошибкой 400"):
        provider.client(models=POOL).complete(MESSAGES)
    assert provider.models() == [FIRST, FIRST]


@pytest.mark.parametrize("status", [401, 403])
def test_pool_stops_on_a_rejected_key(status, caplog):
    provider = ScriptedProvider(status_error(status, "Access denied by security policy"))
    with pytest.raises(LlmError, match="LLM_API_KEY"):
        provider.client(models=POOL).complete(MESSAGES)

    assert provider.models() == [FIRST]
    [record] = [record for record in caplog.records if record.name == "app.llm.client"]
    assert FIRST in record.getMessage() and "Access denied" in record.getMessage()


def test_pool_all_models_failed_reports_the_last_reason_and_logs_each(caplog):
    caplog.set_level(logging.INFO, logger="app.llm.client")
    provider = ScriptedProvider(status_error(429, "rate limit"), timeout)
    with pytest.raises(LlmError, match="не ответил вовремя"):
        provider.client(mode="tools", models=POOL).complete(MESSAGES)

    assert provider.models() == [FIRST, SECOND]
    failed = [record.getMessage() for record in caplog.records if record.name == "app.llm.client"]
    assert len(failed) == 2
    assert FIRST in failed[0] and "rate limit" in failed[0]
    # Последняя модель ждёт, как одна: после неё спросить некого.
    assert SECOND in failed[1] and "таймаут 60 с" in failed[1]


def test_pool_waits_less_per_model_than_a_single_model():
    provider = ScriptedProvider(cancel_call(), cancel_call())
    single = provider.client(max_retries=None).complete(MESSAGES)
    pool = provider.client(models=POOL, max_retries=None).complete(MESSAGES)

    assert (single.model, pool.model) == ("test-model", FIRST)
    assert [request.extensions["timeout"]["read"] for request in provider.requests] == [60.0, 30.0]


def test_pool_last_model_waits_as_long_as_a_single_model():
    """Короткая попытка нужна, чтобы не ждать на недоступной модели, а после последней спросить некого."""
    third = "gpt://folder/yandexgpt-lite/latest"
    provider = ScriptedProvider(timeout, status_error(503), cancel_call())
    result = provider.client(models=(*POOL, third), max_retries=None).complete(MESSAGES)

    assert (provider.models(), result.model) == ([FIRST, SECOND, third], third)
    assert [request.extensions["timeout"]["read"] for request in provider.requests] == [30.0, 30.0, 60.0]


def test_single_model_keeps_one_sdk_retry():
    # retry-after-ms: SDK повторяет через миллисекунду, а не через полсекунды своей паузы.
    busy = httpx.Response(503, headers={"retry-after-ms": "1"}, json={"error": {"message": "занята"}})
    provider = ScriptedProvider(busy, cancel_call())
    result = provider.client(max_retries=None).complete(MESSAGES)

    assert provider.models() == ["test-model", "test-model"]
    assert (result.model, [call.name for call in result.calls]) == ("test-model", ["propose_cancel"])


def test_single_model_failure_is_the_same_error_as_before():
    provider = ScriptedProvider(status_error(503))
    with pytest.raises(LlmError, match="ответил ошибкой 503"):
        provider.client().complete(MESSAGES)
    assert provider.models() == ["test-model"]
