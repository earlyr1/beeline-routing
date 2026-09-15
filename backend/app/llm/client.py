"""Клиент OpenAI-совместимого API: вызов инструментов с запасным JSON-режимом."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import openai
from openai import OpenAI

from app.llm.tools import json_mode_instruction, openai_tools

Message = dict[str, str]

# Коды, которыми провайдеры без поддержки tools отвечают на запрос с инструментами
_FALLBACK_STATUSES = (400, 404, 422)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] | None
    error: str | None = None


@dataclass(frozen=True)
class LlmResult:
    calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    mode: str = "tools"


class LlmError(RuntimeError):
    """Провайдер LLM недоступен или ответил ошибкой. Текст показывается диспетчеру (HTTP 503)."""


class LlmClient(Protocol):
    def complete(self, messages: list[Message]) -> LlmResult: ...


def _parse_arguments(raw: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if not raw:
        return {}, None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        return None, f"аргументы не являются JSON ({error.msg})"
    if not isinstance(value, dict):
        return None, "аргументы должны быть JSON-объектом"
    return value, None


def parse_json_actions(content: str | None) -> list[ToolCall] | None:
    """Разбирает ответ вида {"actions": [...]}. None, если такого JSON в тексте нет."""
    if not content:
        return None
    text = content.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    actions = data.get("actions") if isinstance(data, dict) else None
    if not isinstance(actions, list):
        return None
    calls = []
    for item in actions:
        if not isinstance(item, dict) or not isinstance(item.get("tool"), str):
            calls.append(ToolCall(name="", arguments=None, error="действие без имени"))
            continue
        arguments = item.get("arguments", {})
        if not isinstance(arguments, dict):
            calls.append(ToolCall(item["tool"], None, "аргументы должны быть JSON-объектом"))
            continue
        calls.append(ToolCall(item["tool"], arguments))
    return calls


def _status_text(error: openai.APIStatusError) -> str:
    if error.status_code in (401, 403):
        return "Провайдер LLM отклонил ключ доступа. Проверьте LLM_API_KEY."
    if error.status_code == 429:
        return "Провайдер LLM ограничил частоту запросов. Попробуйте через минуту."
    return f"Провайдер LLM ответил ошибкой {error.status_code}."


class OpenAiLlmClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        mode: str = "auto",
        http_client: httpx.Client | None = None,
        timeout_s: float = 60.0,
        max_retries: int = 1,
    ) -> None:
        if mode not in ("auto", "tools", "json"):
            raise ValueError(f"неизвестный режим LLM: {mode}")
        self._model = model
        self._mode = mode
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",
            http_client=http_client,
            timeout=timeout_s,
            max_retries=max_retries,
        )

    def complete(self, messages: list[Message]) -> LlmResult:
        if self._mode == "json":
            return self._complete_json(messages)
        try:
            return self._complete_tools(messages)
        except openai.APIStatusError as error:
            if self._mode == "auto" and error.status_code in _FALLBACK_STATUSES:
                return self._complete_json(messages)
            raise LlmError(_status_text(error)) from error

    def _create(self, **kwargs: Any):
        try:
            completion = self._client.chat.completions.create(model=self._model, temperature=0, **kwargs)
        except openai.APITimeoutError as error:
            raise LlmError("Помощник не ответил вовремя. Попробуйте ещё раз.") from error
        except openai.APIConnectionError as error:
            raise LlmError("Помощник недоступен: нет связи с провайдером LLM.") from error
        if not completion.choices:
            raise LlmError("Провайдер LLM вернул пустой ответ.")
        return completion.choices[0].message

    def _complete_tools(self, messages: list[Message]) -> LlmResult:
        message = self._create(messages=messages, tools=openai_tools(), tool_choice="auto")
        calls = []
        for call in message.tool_calls or []:
            function = getattr(call, "function", None)
            if function is None:
                continue
            arguments, error = _parse_arguments(function.arguments)
            calls.append(ToolCall(function.name, arguments, error))
        if not calls:
            parsed = parse_json_actions(message.content)
            if parsed is not None:
                return LlmResult(calls=parsed, text=None, mode="tools")
        return LlmResult(calls=calls, text=message.content, mode="tools")

    def _complete_json(self, messages: list[Message]) -> LlmResult:
        system, *rest = messages
        prompt = [{"role": "system", "content": f"{system['content']}\n\n{json_mode_instruction()}"}, *rest]
        try:
            message = self._create(messages=prompt)
        except openai.APIStatusError as error:
            raise LlmError(_status_text(error)) from error
        parsed = parse_json_actions(message.content)
        if parsed is None:
            return LlmResult(calls=[], text=message.content, mode="json")
        return LlmResult(calls=parsed, text=None, mode="json")
