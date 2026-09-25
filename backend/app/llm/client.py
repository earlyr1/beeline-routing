"""Клиент OpenAI-совместимого API: вызов инструментов с запасным JSON-режимом и пул моделей по порядку."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

import httpx
import openai
from openai import OpenAI

from app.llm.tools import json_mode_instruction, openai_tools

Message = dict[str, str]

logger = logging.getLogger(__name__)

# Коды, которыми провайдеры без поддержки tools отвечают на запрос с инструментами. 404 бывает и «модели нет»,
# и «у модели нет инструментов» (OpenRouter: No endpoints found that support tool use): в режиме auto сначала
# JSON-режим той же модели, и только его 404 значит, что модели нет.
_FALLBACK_STATUSES = (400, 404, 422)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
# Одна модель: попытка до минуты и один повтор SDK, как было до пула.
SINGLE_TIMEOUT_S = 60.0
SINGLE_MAX_RETRIES = 1
# Пул: повтор — это следующая модель. Повтор SDK на той же удвоил бы ожидание на недоступной (на 429 SDK ещё и
# ждёт, сколько скажет провайдер), а nginx фронта ждёт ответа backend 180 секунд на весь перебор. Короткая
# попытка — у всех моделей, кроме последней: после неё спросить некого, и 30 секунд лишь превратили бы медленный
# ответ в отказ, поэтому она ждёт, как одна модель. Если обе модели пула из двух не ответили вовремя,
# это 30 + 60 секунд.
POOL_TIMEOUT_S = 30.0
POOL_MAX_RETRIES = 0
# Сколько символов ответа провайдера об ошибке попадает в лог.
_DETAIL_CHARS = 300


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
    # Какая модель пула ответила; None у результатов, собранных не клиентом (тесты разбора).
    model: str | None = None


class LlmError(RuntimeError):
    """Провайдер LLM недоступен или ответил ошибкой. Текст показывается диспетчеру (HTTP 503)."""


class _Unavailable(Exception):
    """Модель не ответила, а следующая в пуле может: нет связи, таймаут, 404, 429, 5xx, пустой ответ.

    Текст исключения — для диспетчера, detail — для лога.
    """

    def __init__(self, text: str, detail: str | None = None) -> None:
        super().__init__(text)
        self.detail = detail or text


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


def _model_unavailable(status: int) -> bool:
    """Код, после которого стоит спросить следующую модель: модели нет, она занята или сломалась.

    401/403 — ключ, он у всех моделей один. 400/422 — сам запрос, другая модель его тоже не примет.
    """
    return status in (404, 408, 429) or status >= 500


def _detail(error: openai.APIStatusError) -> str:
    return f"{_status_text(error)} {error.message[:_DETAIL_CHARS]}"


class OpenAiLlmClient:
    """Модели пула спрашиваются по порядку: следующая — только если предыдущая недоступна.

    Отказ ключа (401/403) и ошибка в самом запросе (400/422, которую не обошёл и JSON-режим) перебор не
    продолжают: у всех моделей пула один ключ и один запрос.
    """

    def __init__(
        self,
        *,
        base_url: str,
        models: Sequence[str],
        api_key: str | None = None,
        mode: str = "auto",
        http_client: httpx.Client | None = None,
        timeout_s: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        if mode not in ("auto", "tools", "json"):
            raise ValueError(f"неизвестный режим LLM: {mode}")
        if not models:
            raise ValueError("нужна хотя бы одна модель LLM")
        if max_retries is None:
            max_retries = POOL_MAX_RETRIES if len(models) > 1 else SINGLE_MAX_RETRIES
        self._models = tuple(models)
        # Таймаут попытки у каждой модели по порядку; timeout_s задаёт один на всех.
        if timeout_s is None:
            self._timeouts = (POOL_TIMEOUT_S,) * (len(models) - 1) + (SINGLE_TIMEOUT_S,)
        else:
            self._timeouts = (timeout_s,) * len(models)
        self._mode = mode
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",
            http_client=http_client,
            max_retries=max_retries,
        )

    @property
    def models(self) -> tuple[str, ...]:
        return self._models

    def complete(self, messages: list[Message]) -> LlmResult:
        """Ответ первой доступной модели. Если не ответила ни одна — LlmError с причиной отказа последней."""
        failure: _Unavailable | None = None
        for model, timeout_s in zip(self._models, self._timeouts, strict=True):
            try:
                result = self._complete_model(model, timeout_s, messages)
            except _Unavailable as error:
                logger.warning("Помощник: модель %s не ответила: %s", model, error.detail)
                failure = error
                continue
            logger.info("Помощник: ответила модель %s (%s)", model, result.mode)
            return replace(result, model=model)
        assert failure is not None  # в пуле хотя бы одна модель
        raise LlmError(str(failure)) from failure

    def _complete_model(self, model: str, timeout_s: float, messages: list[Message]) -> LlmResult:
        """Ответ одной модели; её недоступность — _Unavailable, остальные ошибки — LlmError."""
        try:
            if self._mode == "json":
                return self._complete_json(model, timeout_s, messages)
            try:
                return self._complete_tools(model, timeout_s, messages)
            except openai.APIStatusError as error:
                if self._mode == "auto" and error.status_code in _FALLBACK_STATUSES:
                    return self._complete_json(model, timeout_s, messages)
                raise
        except openai.APIStatusError as error:
            if _model_unavailable(error.status_code):
                raise _Unavailable(_status_text(error), _detail(error)) from error
            logger.warning("Помощник: модель %s отказала: %s", model, _detail(error))
            raise LlmError(_status_text(error)) from error

    def _create(self, model: str, timeout_s: float, **kwargs: Any):
        try:
            completion = self._client.chat.completions.create(
                model=model, temperature=0, timeout=timeout_s, **kwargs
            )
        except openai.APITimeoutError as error:
            raise _Unavailable(
                "Помощник не ответил вовремя. Попробуйте ещё раз.", f"таймаут {timeout_s:g} с"
            ) from error
        except openai.APIConnectionError as error:
            raise _Unavailable(
                "Помощник недоступен: нет связи с провайдером LLM.", f"нет связи ({error.__cause__ or error})"
            ) from error
        if not completion.choices:
            raise _Unavailable("Провайдер LLM вернул пустой ответ.", "пустой ответ (нет choices)")
        return completion.choices[0].message

    def _complete_tools(self, model: str, timeout_s: float, messages: list[Message]) -> LlmResult:
        message = self._create(model, timeout_s, messages=messages, tools=openai_tools(), tool_choice="auto")
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

    def _complete_json(self, model: str, timeout_s: float, messages: list[Message]) -> LlmResult:
        system, *rest = messages
        prompt = [{"role": "system", "content": f"{system['content']}\n\n{json_mode_instruction()}"}, *rest]
        message = self._create(model, timeout_s, messages=prompt)
        parsed = parse_json_actions(message.content)
        if parsed is None:
            return LlmResult(calls=[], text=message.content, mode="json")
        return LlmResult(calls=parsed, text=None, mode="json")
