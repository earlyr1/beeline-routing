"""OpenAI-совместимый провайдер на httpx.MockTransport: настоящий SDK, никакой сети."""

import json

import httpx

from app.llm.client import OpenAiLlmClient
from tests.helpers import eng
from tests.planning_helpers import day_requests, new_session

MESSAGES = [{"role": "system", "content": "правила"}, {"role": "user", "content": "сообщение"}]


def named_session(ctx):
    """День с бригадами по фамилиям: помощник называет инженеров так же, как диспетчер."""
    engineers = [
        eng("E1").model_copy(update={"name": "Бригада Арташкин"}),
        eng("E2").model_copy(update={"name": "Бригада Белузин"}),
    ]
    requests = day_requests()
    requests[1] = requests[1].model_copy(update={"address": "Город Москва, ул.Дубининская, д. 59 к 2"})
    return new_session(ctx=ctx, requests=requests, engineers=engineers)


def ids():
    numbers = iter(range(1, 100))
    return lambda: f"URG-AI-{next(numbers):03d}"


def tool_call(name, arguments, call_id="call_1"):
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": raw}}


def completion(*, tool_calls=None, content=None):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1789400000,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class ScriptedProvider:
    """Отдаёт ответы по очереди: dict как JSON 200, httpx.Response как есть, callable(request) для ошибок сети."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if callable(response):
            return response(request)
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, json=response)

    def bodies(self):
        return [json.loads(request.content) for request in self.requests]

    def models(self):
        """Какой модели шёл каждый запрос, по порядку."""
        return [body["model"] for body in self.bodies()]

    def client(self, mode="auto", models=("test-model",), max_retries=0):
        """max_retries=None — повторы SDK по умолчанию клиента: у пула их нет, у одной модели один с паузой."""
        return OpenAiLlmClient(
            base_url="http://llm.test/v1",
            models=models,
            api_key="test-key",
            mode=mode,
            http_client=httpx.Client(transport=httpx.MockTransport(self.handler)),
            max_retries=max_retries,
        )
