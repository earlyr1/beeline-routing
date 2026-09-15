"""OpenAI-совместимый провайдер на httpx.MockTransport: настоящий SDK, никакой сети."""

import json

import httpx

from app.llm.client import OpenAiLlmClient

MESSAGES = [{"role": "system", "content": "правила"}, {"role": "user", "content": "сообщение"}]


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

    def client(self, mode="auto"):
        return OpenAiLlmClient(
            base_url="http://llm.test/v1",
            model="test-model",
            api_key="test-key",
            mode=mode,
            http_client=httpx.Client(transport=httpx.MockTransport(self.handler)),
            max_retries=0,
        )
