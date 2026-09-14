# План 4: помощник диспетчера и рекомендуемые изменения Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Текстовое окно, где диспетчер пишет ситуацию своими словами, а модель через OpenAI-совместимый API только предлагает события дня; предложения собираются во вкладке «Рекомендуемые изменения» и применяются кнопками «Применить» или «Применить все» через тот же конвейер событий, что и ручные кнопки.

**Architecture:** В backend появляется пакет `app/llm`: описание пяти инструментов, клиент поверх `openai` SDK с запасным JSON-режимом, промпт с состоянием дня и слой проверки, который превращает ответ модели в черновики событий без участия модели. Проверка использует новую функцию `check_event` из `app/planning/session.py`, поэтому правила те же, что у `apply_event`. Предложения живут в `ProposalStore` в памяти процесса, роутер `app/api/proposals.py` отдаёт эндпоинты контракта, а применение идёт только через `apply_event`. Во frontend отдельный стор `useProposalsStore` ходит в новые функции клиента и после применения вызывает `useAppStore.setPlanningState`, поэтому карта, списки и баннер изменений обновляются так же, как после ручного события. Вкладка добавляется одной строкой в `PANEL_TABS`.

**Tech Stack:** Python 3.12, uv 0.12.13, FastAPI, `openai` 2.x (проверено на 2.54.0), httpx `MockTransport`, pytest, ruff; React 18.3.1, TypeScript 5.9.3, zustand 5.0.15, Vitest 3.2.7, Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-15-field-service-routing-design.md` (раздел 11), `docs/superpowers/specs/2026-09-15-api-contract.md` (Proposal, чат и предложения), `docs/superpowers/plans/2026-09-15-plan-2-replan-explain-api-infra.md` (разделы «Отклонения от контракта» и «Точки расширения для Плана 4»), `docs/superpowers/plans/2026-09-15-plan-3-frontend.md` (Задача 8, реестр вкладок).

## Global Constraints

- Планы 1–3 выполнены. Их правила действуют: backend через `uv` из каталога `backend`, frontend через `npm` из каталога `frontend`, время `HH:MM` в JSON и минуты в коде, тексты для диспетчера на русском, ошибки API телом `{"detail": "..."}`, тесты не ходят в сеть.
- Модель никогда не меняет план. Единственные места, где предложение превращается в изменение плана, это эндпоинты `approve` и `approve-all`, и они вызывают `apply_event` из Плана 2.
- Предложение проверяется функцией `check_event(session, event, ctx)` с теми же правилами, что `apply_event`. Отдельных копий проверок в `app/llm` нет.
- Провайдер любой OpenAI-совместимый: `LLM_BASE_URL`, `LLM_API_KEY` (может быть пустым), `LLM_MODEL`, `LLM_TOOL_MODE` = `auto` | `tools` | `json`. Без `LLM_BASE_URL` и `LLM_MODEL` чат отвечает 503, остальной сервис работает.
- Зависимость `openai>=2.0,<3`. Тесты backend вызывают настоящий SDK поверх `httpx.Client(transport=httpx.MockTransport(...))`, frontend-тесты подменяют `src/api/client.ts`.
- Изменения файлов Планов 2 и 3 ограничены перечисленными в задачах местами. Раскладка frontend из контракта не меняется: типы в `types.ts`, запросы в `client.ts`, вкладка в `PANEL_TABS`.
- Перед каждым коммитом backend: `uv run ruff format app tests scripts` и `uv run ruff check app tests scripts`. Перед каждым коммитом frontend: `npm test` и `npm run build`.
- Сообщение каждого коммита заканчивается строками:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` и
  `Claude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ`.

## Отклонения от контракта

1. `POST /proposals/{id}/approve` для предложения, которое конфликтует с состоянием дня, отвечает 200: `proposal.status = "failed"`, в `proposal.error` текст `EventRejected`, `state` не меняется.
2. Предложение, созданное до других событий, применяется не раньше текущего `now`: если время события меньше `now`, оно сдвигается на `now`, и в сохранённом `proposal.event` уже новое время.
3. `approve-all` и `reject-all` возвращают все предложения датасета, а не только затронутые.
4. Повторный `approve` или `reject` уже обработанного предложения даёт 409 с текстом «Предложение pr_1 уже применено.»; неизвестный id даёт 404.
5. `ClientConfig.llm_enabled` равен `true`, когда у backend есть клиент LLM (`LLM_BASE_URL` и `LLM_MODEL` заданы). `LLM_API_KEY` необязателен.
6. Тело чата `{"text": "..."}`: не длиннее 2000 символов, пустое после обрезки пробелов даёт 422 «Некорректный запрос: text: сообщение пустое».
7. Срочные заявки из чата получают номера `URG-AI-001`, `URG-AI-002`… без повторов внутри датасета; координаты берутся только из геокодера, модель их не передаёт.
8. Новая переменная окружения `LLM_TOOL_MODE`.

## Карта файлов

| Файл | Ответственность |
|---|---|
| `backend/app/planning/session.py` (изменение) | `check_event`: проверка события без пересчёта плана |
| `backend/app/settings.py` (изменение) | `llm_tool_mode` из `LLM_TOOL_MODE` |
| `backend/app/llm/tools.py` | пять инструментов и описание JSON-режима |
| `backend/app/llm/client.py` | `OpenAiLlmClient`, `LlmResult`, `ToolCall`, `LlmError`, разбор JSON-ответа |
| `backend/app/llm/prompt.py` | правила и состояние дня для модели |
| `backend/app/llm/interpret.py` | разбор аргументов, поиск инженеров и заявок, проверка через `check_event` |
| `backend/app/llm/schemas.py` | `Proposal`, `ChatRequest`, `ChatResponse` |
| `backend/app/llm/store.py` | предложения по датасетам в памяти |
| `backend/app/api/proposals.py` | эндпоинты чата и предложений |
| `backend/app/api/deps.py`, `routes.py`, `app.py` (изменения) | клиент LLM и хранилище в зависимостях, `llm_enabled`, подключение роутера |
| `backend/scripts/smoke_proposals.py` | проверка помощника на живом backend |
| `backend/tests/llm_helpers.py` | фейковый провайдер на `MockTransport` |
| `frontend/src/api/types.ts`, `client.ts` (изменения) | ответы и запросы чата и предложений |
| `frontend/src/lib/proposals.ts` | подписи статусов, подробности предложения, сводка изменений |
| `frontend/src/store/useProposalsStore.ts` | предложения, отправка, применение с обновлением общего состояния |
| `frontend/src/components/panel/ProposalsTab.tsx` | вкладка «Рекомендуемые изменения» |
| `frontend/src/components/panel/tabs.ts`, `styles.css` (изменения) | регистрация вкладки и стили карточек |
| `.env.example`, `README.md` (изменения) | настройка провайдера и сценарий |

## Проверено при планировании

- Backend Плана 2 скопирован, изменения этого плана внесены и прогнаны в Python 3.12 с `openai` 2.54.0: `136 passed` (112 тестов Планов 1–2 и 24 новых), `ruff check` и `ruff format --check` чистые.
- `uv add "openai>=2.0,<3"` в uv 0.12.13: изменяет `pyproject.toml` ровно как в Задаче 1, в `uv.lock` 45 пакетов.
- Живой прогон по HTTP: uvicorn с этим backend, бандл Востока из Плана 1 и фейковый OpenAI-совместимый сервер на `127.0.0.1:9100`. `scripts/smoke_proposals.py --approve` получил от провайдера вызовы инструментов, нашёл бригаду по фамилии («Арташкин» → `E01`), для неоднозначной улицы вернул уточнение со списком трёх заявок, применил предложение: версия плана 2, 7 инженеров, 162.14 км, 0 неназначенных. Промпт для 66 заявок занял около 20 тысяч символов.
- Frontend Плана 3 скопирован, изменения внесены: `Test Files  21 passed (21)`, `Tests  90 passed (90)` (73 теста Плана 3 и 17 новых), `npm run build` проходит.
- С настоящим провайдером LLM не проверялось: ключа нет. Качество распознавания зависит от выбранной модели.

---

### Task 1: Проверка события без пересчёта и настройка режима LLM

**Files:**
- Modify: `backend/app/planning/session.py` (перед `apply_event`)
- Modify: `backend/app/settings.py`
- Modify: `backend/pyproject.toml`, `backend/uv.lock` (через `uv add`)
- Test: `backend/tests/test_session_check.py`, `backend/tests/test_llm_settings.py`

**Interfaces:**
- Consumes: `PlanningSession`, `PlanningContext`, `EventRejected`, `apply_event`, приватная `_apply_to_inputs(session, event, ctx) -> (requests, engineers, stored_event)` из `app/planning/session.py` (План 2); `Settings.from_env`; помощники `tests.planning_helpers.context`, `new_session`, `tests.helpers.req`.
- Produces:
  - `check_event(session: PlanningSession, event: Event, ctx: PlanningContext) -> Event`: проверяет время и конфликт с состоянием, у срочной заявки без координат вызывает `ctx.geocode`; возвращает событие в сохраняемом виде; бросает `EventRejected`; сессию не меняет.
  - `app.settings.LLM_TOOL_MODES = ("auto", "tools", "json")`, поле `Settings.llm_tool_mode: str = "auto"` (последнее поле dataclass, со значением по умолчанию, чтобы не ломать `tests/api_helpers.py`).

- [ ] **Step 1: Добавить зависимость**

Run: `cd backend && uv add "openai>=2.0,<3"`
Expected: в `pyproject.toml` в `dependencies` появляется строка `"openai>=2.0,<3",` между `httpx` и `ortools`; в конце вывода `+ openai==2.54.0` или новее в пределах 2.x.

- [ ] **Step 2: Написать падающие тесты**

`backend/tests/test_session_check.py`:

```python
import pytest

from app.domain.models import Event
from app.ingest.geocode import GeoResult
from app.planning.session import EventRejected, apply_event, check_event
from tests.helpers import req
from tests.planning_helpers import context, new_session


def test_check_event_geocodes_urgent_request_without_replanning():
    ctx = context(geocode=lambda address, district: GeoResult(55.7, 37.6, "street", address))
    session = new_session(ctx)
    urgent_request = req("U1", 0, 0, "13:00", "15:00").model_copy(update={"lat": None, "lon": None})

    stored = check_event(session, Event(type="urgent", time="13:00", request=urgent_request), ctx)

    assert (stored.request.lat, stored.request.lon, stored.request.geocode_precision) == (
        55.7,
        37.6,
        "street",
    )
    assert stored.request.priority == "urgent"
    assert session.version == 1 and session.request("U1") is None


def test_check_event_rejects_conflicts_like_apply_event():
    ctx = context()
    session = new_session(ctx)
    with pytest.raises(EventRejected, match="Заявка NOPE не найдена"):
        check_event(session, Event(type="cancel", time="13:00", request_id="NOPE"), ctx)

    later = apply_event(session, Event(type="cancel", time="13:00", request_id="R3"), ctx)
    with pytest.raises(EventRejected, match="раньше текущего времени плана 13:00"):
        check_event(later, Event(type="cancel", time="12:00", request_id="R2"), ctx)
    with pytest.raises(EventRejected, match="уже отменена"):
        check_event(later, Event(type="cancel", time="13:00", request_id="R3"), ctx)
```

`backend/tests/test_llm_settings.py` (в Задаче 4 файл дополняется):

```python
import pytest

from app.settings import Settings


def test_llm_tool_mode_default_and_validation():
    assert Settings.from_env({}).llm_tool_mode == "auto"
    assert Settings.from_env({"LLM_TOOL_MODE": "json"}).llm_tool_mode == "json"
    with pytest.raises(ValueError, match="LLM_TOOL_MODE"):
        Settings.from_env({"LLM_TOOL_MODE": "xml"})
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `cd backend && uv run pytest tests/test_session_check.py tests/test_llm_settings.py`
Expected: FAIL, сбор тестов прерывается на `ImportError: cannot import name 'check_event' from 'app.planning.session'`. Отдельный запуск `uv run pytest tests/test_llm_settings.py` падает с `AttributeError: 'Settings' object has no attribute 'llm_tool_mode'`.

- [ ] **Step 4: Добавить `check_event` в `backend/app/planning/session.py`**

Заменить начало функции `apply_event`:

```python
def apply_event(session: PlanningSession, event: Event, ctx: PlanningContext) -> PlanningSession:
    """Применяет одно событие дня и возвращает НОВУЮ сессию; входная не меняется.

    Бросает EventRejected, если событие противоречит текущему состоянию.
    """
    if event.time < session.now:
        raise EventRejected(
            f"Время события {fmt_hhmm(event.time)} раньше текущего времени плана {fmt_hhmm(session.now)}."
        )
    requests, engineers, stored_event = _apply_to_inputs(session, event, ctx)
```

на:

```python
def _check_time(session: PlanningSession, event: Event) -> None:
    if event.time < session.now:
        raise EventRejected(
            f"Время события {fmt_hhmm(event.time)} раньше текущего времени плана {fmt_hhmm(session.now)}."
        )


def check_event(session: PlanningSession, event: Event, ctx: PlanningContext) -> Event:
    """Проверяет событие против текущего состояния без пересчёта плана.

    Возвращает событие в том виде, в каком apply_event его сохранит (у срочной заявки появляются
    координаты из геокодера). Бросает EventRejected с текстом для диспетчера. Сессию не меняет.
    """
    _check_time(session, event)
    return _apply_to_inputs(session, event, ctx)[2]


def apply_event(session: PlanningSession, event: Event, ctx: PlanningContext) -> PlanningSession:
    """Применяет одно событие дня и возвращает НОВУЮ сессию; входная не меняется.

    Бросает EventRejected, если событие противоречит текущему состоянию.
    """
    _check_time(session, event)
    requests, engineers, stored_event = _apply_to_inputs(session, event, ctx)
```

- [ ] **Step 5: Добавить режим LLM в `backend/app/settings.py`**

После строки `GEOCODERS = ("nominatim", "cache-only")` добавить:

```python
LLM_TOOL_MODES = ("auto", "tools", "json")
```

Последним полем dataclass `Settings`, после `solver_time_limit_s: int`, добавить:

```python
    llm_tool_mode: str = "auto"
```

В `from_env` после проверки `GEOCODER` добавить:

```python
        llm_tool_mode = optional("LLM_TOOL_MODE") or "auto"
        if llm_tool_mode not in LLM_TOOL_MODES:
            raise ValueError(f"LLM_TOOL_MODE должен быть одним из: {', '.join(LLM_TOOL_MODES)}")
```

и последним аргументом `cls(...)` после `solver_time_limit_s=...`:

```python
            llm_tool_mode=llm_tool_mode,
```

- [ ] **Step 6: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_session_check.py tests/test_llm_settings.py tests/test_session.py tests/test_settings.py`
Expected: все зелёные; `tests/test_session_check.py` даёт ``2 passed``.

- [ ] **Step 7: Commit**

```bash
cd backend && uv run ruff format app tests scripts && uv run ruff check app tests scripts && cd ..
git add backend/app/planning/session.py backend/app/settings.py backend/pyproject.toml backend/uv.lock backend/tests/test_session_check.py backend/tests/test_llm_settings.py
git commit -m "feat(planning): check events without replanning and add LLM tool mode setting" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 2: Клиент OpenAI-совместимого API

Режим `tools` передаёт пять инструментов и разбирает `tool_calls`. Режим `json` дописывает в системное сообщение схему действий и разбирает `{"actions": [...]}` из текста, в том числе внутри блока ```json. Режим `auto` пробует инструменты и переключается на JSON, если провайдер ответил 400, 404 или 422. Ошибки сети, авторизации и лимитов становятся `LlmError` с русским текстом.

**Files:**
- Create: `backend/app/llm/__init__.py` (пустой), `backend/app/llm/tools.py`, `backend/app/llm/client.py`
- Test: `backend/tests/llm_helpers.py`, `backend/tests/test_llm_client.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач, кроме зависимости `openai`.
- Produces:
  - `TOOL_SPECS: dict[str, dict]` в порядке `propose_urgent_request`, `propose_cancel`, `propose_restore`, `propose_engineer_unavailable`, `ask_clarification`; `TOOL_NAMES`, `openai_tools() -> list[dict]`, `json_mode_instruction() -> str`.
  - `Message = dict[str, str]`, `ToolCall(name: str, arguments: dict | None, error: str | None = None)`, `LlmResult(calls: list[ToolCall], text: str | None, mode: str)`, `LlmError(RuntimeError)`, протокол `LlmClient.complete(messages: list[Message]) -> LlmResult`, `parse_json_actions(content: str | None) -> list[ToolCall] | None`.
  - `OpenAiLlmClient(*, base_url, model, api_key=None, mode="auto", http_client=None, timeout_s=60.0, max_retries=1)`.
  - `tests.llm_helpers`: `MESSAGES`, `tool_call(name, arguments, call_id="call_1")`, `completion(*, tool_calls=None, content=None)`, `ScriptedProvider(*responses)` с `.client(mode="auto")`, `.requests`, `.bodies()`.

- [ ] **Step 1: Написать помощник тестов**

`backend/tests/llm_helpers.py`:

```python
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
```

- [ ] **Step 2: Написать падающий тест**

`backend/tests/test_llm_client.py`:

````python
import httpx
import pytest

from app.llm.client import LlmError, ToolCall, parse_json_actions
from tests.llm_helpers import MESSAGES, ScriptedProvider, completion, tool_call


def test_tools_mode_sends_five_tools_and_parses_calls():
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
````

- [ ] **Step 3: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_llm_client.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.llm'`.

- [ ] **Step 4: Создать пакет и описание инструментов**

Run: `mkdir -p backend/app/llm && touch backend/app/llm/__init__.py`

`backend/app/llm/tools.py`:

```python
"""Инструменты, которыми модель предлагает изменения плана, и их описание для JSON-режима."""

from __future__ import annotations

import json
from typing import Any

_TIME = {
    "type": "string",
    "pattern": r"^\d{1,2}:\d{2}$",
    "description": "Время HH:MM. Если в сообщении времени нет, текущее время плана.",
}
_RATIONALE = {
    "type": "string",
    "description": "Одно-два предложения по-русски: какие слова сообщения привели к предложению.",
}

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "propose_urgent_request": {
        "description": "Предложить добавить срочную заявку.",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Адрес объекта, как в сообщении"},
                "window_start": {**_TIME, "description": "Начало окна визита HH:MM"},
                "window_end": {**_TIME, "description": "Конец окна визита HH:MM"},
                "duration_min": {"type": "integer", "minimum": 5, "maximum": 600},
                "skill": {"type": "string", "enum": ["local", "connection", "emergency"]},
                "transport_required": {
                    "type": "string",
                    "enum": ["car", "foot", "bike", "public", "none"],
                    "description": "none, если требований к транспорту нет",
                },
                "time": _TIME,
                "rationale": _RATIONALE,
            },
            "required": ["address", "window_start", "window_end", "duration_min", "skill", "rationale"],
        },
    },
    "propose_cancel": {
        "description": "Предложить отменить заявку.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "id заявки из состояния дня"},
                "time": _TIME,
                "rationale": _RATIONALE,
            },
            "required": ["request_id", "rationale"],
        },
    },
    "propose_restore": {
        "description": "Предложить вернуть в план ранее отменённую заявку.",
        "parameters": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "id отменённой заявки"},
                "time": _TIME,
                "rationale": _RATIONALE,
            },
            "required": ["request_id", "rationale"],
        },
    },
    "propose_engineer_unavailable": {
        "description": "Предложить отметить инженера недоступным с указанного времени до конца дня.",
        "parameters": {
            "type": "object",
            "properties": {
                "engineer_id": {"type": "string", "description": "id инженера из состояния дня"},
                "time": _TIME,
                "rationale": _RATIONALE,
            },
            "required": ["engineer_id", "rationale"],
        },
    },
    "ask_clarification": {
        "description": "Задать диспетчеру короткий уточняющий вопрос, если непонятно, кого или что менять.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
}

TOOL_NAMES = tuple(TOOL_SPECS)


def openai_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": name, "description": spec["description"], "parameters": spec["parameters"]},
        }
        for name, spec in TOOL_SPECS.items()
    ]


def json_mode_instruction() -> str:
    schemas = {
        name: {"description": spec["description"], **spec["parameters"]} for name, spec in TOOL_SPECS.items()
    }
    return (
        "Вызов инструментов недоступен. Ответь только JSON-объектом без текста вокруг: "
        '{"actions": [{"tool": "<имя действия>", "arguments": {...}}]}. '
        "Если изменений нет, верни пустой список actions. Действия и их аргументы (JSON Schema):\n"
        + json.dumps(schemas, ensure_ascii=False)
    )
```

- [ ] **Step 5: Реализовать клиент**

`backend/app/llm/client.py`:

````python
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
````

- [ ] **Step 6: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_llm_client.py`
Expected: ``7 passed``

- [ ] **Step 7: Commit**

```bash
cd backend && uv run ruff format app tests scripts && uv run ruff check app tests scripts && cd ..
git add backend/app/llm/__init__.py backend/app/llm/tools.py backend/app/llm/client.py backend/tests/llm_helpers.py backend/tests/test_llm_client.py
git commit -m "feat(llm): OpenAI-compatible client with tool calling and JSON fallback" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 3: Промпт и разбор ответа модели

Модель получает правила и состояние дня: текущее время, инженеров с навыками, транспортом и доступностью, заявки с окном, статусом, назначенным инженером и плановым началом. Разбор не доверяет модели: аргументы проходят pydantic-модели, инженер ищется по id или по части названия бригады, заявка по id или по части адреса, неоднозначность превращается в уточнение, событие проверяется `check_event`. Ошибка проверки даёт черновик со статусом `failed` и причиной, а не изменение плана.

**Files:**
- Create: `backend/app/llm/prompt.py`, `backend/app/llm/interpret.py`
- Test: `backend/tests/test_llm_interpret.py`

**Interfaces:**
- Consumes: `check_event` (Задача 1), `LlmResult`, `ToolCall`, `Message` (Задача 2), `PlanningSession.request(id)`, `.engineer(id)`, `.plan`, `.requests`, `.engineers`, `.now` (План 2), `SKILL_RU`, `TRANSPORT_RU`, `fmt_hhmm`, `HHMM` (План 1).
- Produces:
  - `system_rules(now: str) -> str`, `session_context(session) -> dict`, `build_messages(text: str, session) -> list[Message]` (два сообщения: system и user; user заканчивается текстом диспетчера).
  - `NOTHING_FOUND: str`, `ProposalDraft(event: Event, rationale: str, error: str | None = None)`, `Interpretation(drafts: list[ProposalDraft], clarifications: list[str])`, `Unresolved(ValueError)`, `resolve_engineer(session, value) -> str`, `resolve_request(session, value) -> str`, `interpret(result: LlmResult, session, ctx: PlanningContext, new_request_id: Callable[[], str]) -> Interpretation`.

- [ ] **Step 1: Написать падающий тест**

`backend/tests/test_llm_interpret.py`:

```python
from app.domain.enums import EventType, Priority, Skill, Transport
from app.ingest.geocode import GeoResult
from app.llm.client import LlmResult, ToolCall
from app.llm.interpret import interpret
from app.llm.prompt import build_messages
from tests.helpers import eng
from tests.planning_helpers import context, day_requests, new_session


def named_session(ctx):
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


def run(calls, ctx=None, session=None, text=None):
    ctx = ctx or context()
    session = session or named_session(ctx)
    return interpret(LlmResult(calls=calls, text=text), session, ctx, ids())


def test_cancel_by_id_and_by_street_become_pending_drafts():
    out = run(
        [
            ToolCall(
                "propose_cancel", {"request_id": "R1", "time": "09:00", "rationale": "Клиент отказался"}
            ),
            ToolCall("propose_cancel", {"request_id": "Дубининская", "time": "13:00", "rationale": "Отмена"}),
        ]
    )
    assert out.clarifications == []
    assert [(d.event.type, d.event.request_id, d.event.time, d.error) for d in out.drafts] == [
        (EventType.CANCEL, "R1", 540, None),
        (EventType.CANCEL, "R2", 780, None),
    ]
    assert out.drafts[0].rationale == "Клиент отказался"


def test_engineer_resolved_by_surname_and_time_defaults_to_now():
    out = run([ToolCall("propose_engineer_unavailable", {"engineer_id": "белузин", "rationale": "Заболел"})])
    draft = out.drafts[0]
    assert (draft.event.engineer_id, draft.event.time, draft.error) == ("E2", 0, None)


def test_ambiguous_or_unknown_names_become_clarifications():
    out = run(
        [
            ToolCall("propose_engineer_unavailable", {"engineer_id": "Бригада", "rationale": "?"}),
            ToolCall("propose_cancel", {"request_id": "Тверская", "rationale": "?"}),
        ]
    )
    assert out.drafts == []
    assert out.clarifications == [
        "Под «Бригада» подходят несколько инженеров: Бригада Арташкин, Бригада Белузин. Уточните, кого вы имеете в виду.",
        "Заявка «Тверская» не найдена.",
    ]


def test_conflicts_with_day_state_become_failed_drafts():
    out = run(
        [ToolCall("propose_restore", {"request_id": "R2", "time": "13:00", "rationale": "Снова в силе"})]
    )
    assert out.drafts[0].error == "Заявка R2 не отменена, возвращать нечего."


def test_urgent_request_is_geocoded_and_gets_generated_id():
    ctx = context(geocode=lambda address, district: GeoResult(55.75, 37.61, "house", address))
    arguments = {
        "address": "Москва, Дубининская улица, 59к2",
        "window_start": "13:00",
        "window_end": "15:00",
        "duration_min": 60,
        "skill": "emergency",
        "transport_required": "car",
        "time": "13:00",
        "rationale": "Авария на объекте",
    }
    out = run([ToolCall("propose_urgent_request", arguments)], ctx=ctx)
    request = out.drafts[0].event.request
    assert out.drafts[0].error is None
    assert (request.id, request.lat, request.lon, request.priority) == (
        "URG-AI-001",
        55.75,
        37.61,
        Priority.URGENT,
    )
    assert (request.skill, request.transport_required) == (Skill.EMERGENCY, Transport.CAR)

    no_transport = run(
        [ToolCall("propose_urgent_request", {**arguments, "transport_required": "none"})], ctx=ctx
    )
    assert no_transport.drafts[0].event.request.transport_required is None


def test_urgent_request_with_unknown_address_is_failed():
    ctx = context(geocode=lambda address, district: GeoResult(None, None, "none", None))
    arguments = {
        "address": "Нигде, д. 1",
        "window_start": "13:00",
        "window_end": "15:00",
        "duration_min": 60,
        "skill": "local",
        "rationale": "Срочно",
    }
    out = run([ToolCall("propose_urgent_request", arguments)], ctx=ctx)
    assert "не найден на карте" in out.drafts[0].error


def test_malformed_calls_duplicates_and_text_answers():
    out = run(
        [
            ToolCall(
                "propose_urgent_request",
                {
                    "address": "Москва, ул. Тестовая, 1",
                    "window_start": "15:00",
                    "window_end": "13:00",
                    "duration_min": 30,
                    "skill": "local",
                },
            ),
            ToolCall("propose_cancel", None, "аргументы не являются JSON (Expecting value)"),
            ToolCall("delete_everything", {}),
            ToolCall("ask_clarification", {"question": "Какую заявку отменить?"}),
            ToolCall("propose_cancel", {"request_id": "R3", "time": "13:00", "rationale": "Отмена"}),
            ToolCall("propose_cancel", {"request_id": "R3", "time": "13:00", "rationale": "Отмена ещё раз"}),
        ]
    )
    assert len(out.drafts) == 1
    assert out.clarifications[0].startswith("Не удалось разобрать предложение «propose_urgent_request»")
    assert out.clarifications[1:] == [
        "Не удалось разобрать предложение «propose_cancel»: аргументы не являются JSON (Expecting value).",
        "Помощник предложил неизвестное действие «delete_everything». Переформулируйте запрос.",
        "Какую заявку отменить?",
    ]
    assert run([], text="Что именно случилось?").clarifications == ["Что именно случилось?"]


def test_prompt_carries_now_engineers_and_assignments():
    ctx = context()
    session = named_session(ctx)
    system, user = build_messages("Арташкин заболел после обеда", session)
    assert "Текущее время плана: 00:00." in system["content"]
    assert "«после обеда» 14:00" in system["content"]
    assert '"name": "Бригада Арташкин"' in user["content"]
    assert '"address": "Город Москва, ул.Дубининская, д. 59 к 2"' in user["content"]
    assert '"planned_start": "' in user["content"] and user["content"].endswith(
        "Арташкин заболел после обеда"
    )
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd backend && uv run pytest tests/test_llm_interpret.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.llm.interpret'`.

- [ ] **Step 3: Реализовать промпт**

`backend/app/llm/prompt.py`:

```python
"""Сообщения для модели: правила и текущее состояние дня."""

from __future__ import annotations

import json
from typing import Any

from app.domain.enums import SKILL_RU, TRANSPORT_RU
from app.domain.timeutil import fmt_hhmm
from app.llm.client import Message
from app.planning.session import PlanningSession


def system_rules(now: str) -> str:
    return f"""Ты помощник диспетчера выездных инженеров. Ты не меняешь план сам: ты только предлагаешь изменения, а диспетчер подтверждает или отклоняет каждое.
Текущее время плана: {now}.
Правила:
1. Каждое изменение из сообщения оформляй отдельным вызовом инструмента. В одном сообщении может быть несколько изменений.
2. Используй только id инженеров и заявок из состояния дня. Инженера узнавай по фамилии в названии бригады, заявку по номеру, адресу или улице.
3. Время пиши в формате HH:MM и не раньше текущего времени плана. Если время не названо, используй текущее. «Утром» это 10:00, «в обед» 13:00, «после обеда» 14:00, «вечером» 18:00.
4. «Заболел», «сломалась машина», «не выйдет», «уехал» означает недоступность инженера. «Клиент отказался», «отмена» означает отмену заявки. «Вернуть», «снова в силе» означает возврат отменённой заявки. Новая авария или срочный вызов означает срочную заявку.
5. Для срочной заявки укажи адрес, окно, длительность и навык. Для аварии бери навык emergency и автомобиль, если не сказано иное. Если окно не названо, бери два часа от текущего времени.
6. Если непонятно, кого или что менять, вызови ask_clarification с коротким вопросом. Не выдумывай id.
7. В rationale одним-двумя предложениями по-русски объясни, какие слова сообщения привели к предложению."""


def session_context(session: PlanningSession) -> dict[str, Any]:
    assigned = {
        visit.request_id: (route.engineer_id, visit)
        for route in session.plan.routes
        for visit in route.visits
    }
    names = {engineer.id: engineer.name for engineer in session.engineers}
    requests = []
    for request in session.requests:
        engineer_id, visit = assigned.get(request.id, (None, None))
        requests.append(
            {
                "id": request.id,
                "address": request.address,
                "district": request.district,
                "window": f"{fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}",
                "priority": request.priority.value,
                "skill": request.skill.value,
                "status": request.status.value,
                "engineer_id": engineer_id,
                "engineer_name": names.get(engineer_id) if engineer_id else None,
                "planned_start": fmt_hhmm(visit.start) if visit else None,
            }
        )
    return {
        "now": fmt_hhmm(session.now),
        "skills": {skill.value: label for skill, label in SKILL_RU.items()},
        "transport": {transport.value: label for transport, label in TRANSPORT_RU.items()},
        "engineers": [
            {
                "id": engineer.id,
                "name": engineer.name,
                "skills": [skill.value for skill in engineer.skills],
                "transport": engineer.transport.value,
                "shift": f"{fmt_hhmm(engineer.shift_start)}–{fmt_hhmm(engineer.shift_end)}",
                "available": engineer.available,
                "unavailable_from": (
                    fmt_hhmm(engineer.unavailable_from) if engineer.unavailable_from is not None else None
                ),
            }
            for engineer in session.engineers
        ],
        "requests": requests,
    }


def build_messages(text: str, session: PlanningSession) -> list[Message]:
    context = json.dumps(session_context(session), ensure_ascii=False)
    return [
        {"role": "system", "content": system_rules(fmt_hhmm(session.now))},
        {"role": "user", "content": f"Состояние дня (JSON):\n{context}\n\nСообщение диспетчера:\n{text}"},
    ]
```

- [ ] **Step 4: Реализовать разбор**

`backend/app/llm/interpret.py`:

```python
"""Проверка ответа модели без участия модели: разбор аргументов, поиск id, конфликт с состоянием дня.

Ни одно предложение отсюда не меняет план: результат идёт в хранилище предложений.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.domain.enums import EventType, Priority, Skill, Transport
from app.domain.models import Event, Request
from app.domain.timeutil import HHMM
from app.llm.client import LlmResult, ToolCall
from app.planning.session import EventRejected, PlanningContext, PlanningSession, check_event

NOTHING_FOUND = (
    "Не нашёл в сообщении изменений плана. Опишите, что случилось: отмена или возврат заявки, "
    "срочная заявка или недоступность инженера."
)


class _TimedArgs(BaseModel):
    time: HHMM | None = None
    rationale: str = ""


class RequestArgs(_TimedArgs):
    request_id: str = Field(min_length=1)


class EngineerArgs(_TimedArgs):
    engineer_id: str = Field(min_length=1)


class UrgentArgs(_TimedArgs):
    address: str = Field(min_length=3)
    window_start: HHMM
    window_end: HHMM
    duration_min: int = Field(gt=0, le=600)
    skill: Skill
    transport_required: Literal["car", "foot", "bike", "public", "none"] | None = None


class ClarifyArgs(BaseModel):
    question: str = Field(min_length=1)


ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "propose_urgent_request": UrgentArgs,
    "propose_cancel": RequestArgs,
    "propose_restore": RequestArgs,
    "propose_engineer_unavailable": EngineerArgs,
    "ask_clarification": ClarifyArgs,
}


@dataclass
class ProposalDraft:
    event: Event
    rationale: str
    error: str | None = None


@dataclass
class Interpretation:
    drafts: list[ProposalDraft] = field(default_factory=list)
    clarifications: list[str] = field(default_factory=list)


class Unresolved(ValueError):
    """Модель назвала инженера или заявку, которых нельзя однозначно найти."""


def _norm(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def resolve_engineer(session: PlanningSession, value: str) -> str:
    if session.engineer(value) is not None:
        return value
    key = _norm(value)
    matches = [engineer for engineer in session.engineers if key and key in _norm(engineer.name)]
    if len(matches) == 1:
        return matches[0].id
    if not matches:
        raise Unresolved(f"Инженер «{value}» не найден.")
    names = ", ".join(engineer.name for engineer in matches[:5])
    raise Unresolved(f"Под «{value}» подходят несколько инженеров: {names}. Уточните, кого вы имеете в виду.")


def resolve_request(session: PlanningSession, value: str) -> str:
    if session.request(value) is not None:
        return value
    key = _norm(value)
    matches = [request for request in session.requests if key and key in _norm(request.address)]
    if len(matches) == 1:
        return matches[0].id
    if not matches:
        raise Unresolved(f"Заявка «{value}» не найдена.")
    listed = "; ".join(f"{request.id} ({request.address})" for request in matches[:5])
    raise Unresolved(f"Под «{value}» подходят несколько заявок: {listed}. Уточните номер.")


def _validation_text(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"]).removeprefix("Value error, ")
    return f"{location}: {message}" if location else message


def _build_event(
    name: str, args: BaseModel, session: PlanningSession, new_request_id: Callable[[], str]
) -> Event:
    time = args.time if args.time is not None else session.now
    if name == "propose_cancel":
        return Event(type=EventType.CANCEL, time=time, request_id=resolve_request(session, args.request_id))
    if name == "propose_restore":
        return Event(type=EventType.RESTORE, time=time, request_id=resolve_request(session, args.request_id))
    if name == "propose_engineer_unavailable":
        return Event(
            type=EventType.ENGINEER_UNAVAILABLE,
            time=time,
            engineer_id=resolve_engineer(session, args.engineer_id),
        )
    transport = None if args.transport_required in (None, "none") else Transport(args.transport_required)
    request = Request(
        id=new_request_id(),
        address=args.address.strip(),
        duration_min=args.duration_min,
        window_start=args.window_start,
        window_end=args.window_end,
        priority=Priority.URGENT,
        skill=args.skill,
        transport_required=transport,
        source_type_bk="Срочная заявка из чата",
    )
    return Event(type=EventType.URGENT, time=time, request=request)


def _interpret_call(
    call: ToolCall,
    session: PlanningSession,
    ctx: PlanningContext,
    new_request_id: Callable[[], str],
    out: Interpretation,
    seen: set[tuple],
) -> None:
    model = ARGUMENT_MODELS.get(call.name)
    if model is None:
        out.clarifications.append(
            f"Помощник предложил неизвестное действие «{call.name}». Переформулируйте запрос."
        )
        return
    if call.arguments is None:
        out.clarifications.append(f"Не удалось разобрать предложение «{call.name}»: {call.error}.")
        return
    try:
        args = model.model_validate(call.arguments)
    except ValidationError as error:
        out.clarifications.append(
            f"Не удалось разобрать предложение «{call.name}»: {_validation_text(error)}."
        )
        return
    if isinstance(args, ClarifyArgs):
        out.clarifications.append(args.question.strip())
        return
    try:
        event = _build_event(call.name, args, session, new_request_id)
    except Unresolved as error:
        out.clarifications.append(str(error))
        return
    except ValidationError as error:
        out.clarifications.append(
            f"Не удалось разобрать предложение «{call.name}»: {_validation_text(error)}."
        )
        return

    request = event.request
    key = (event.type, event.request_id, event.engineer_id, event.time, request.address if request else None)
    if key in seen:
        return
    seen.add(key)

    rationale = args.rationale.strip() or "Помощник не пояснил предложение."
    try:
        stored = check_event(session, event, ctx)
    except EventRejected as error:
        out.drafts.append(ProposalDraft(event=event, rationale=rationale, error=str(error)))
        return
    if stored.request is not None and (stored.request.lat is None or stored.request.lon is None):
        out.drafts.append(
            ProposalDraft(
                event=stored,
                rationale=rationale,
                error=f"Адрес «{stored.request.address}» не найден на карте. Уточните адрес или добавьте заявку вручную с точкой на карте.",
            )
        )
        return
    out.drafts.append(ProposalDraft(event=stored, rationale=rationale))


def interpret(
    result: LlmResult,
    session: PlanningSession,
    ctx: PlanningContext,
    new_request_id: Callable[[], str],
) -> Interpretation:
    out = Interpretation()
    seen: set[tuple] = set()
    for call in result.calls:
        _interpret_call(call, session, ctx, new_request_id, out, seen)
    if not result.calls and result.text and result.text.strip():
        out.clarifications.append(result.text.strip())
    return out
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_llm_interpret.py`
Expected: ``8 passed``

- [ ] **Step 6: Commit**

```bash
cd backend && uv run ruff format app tests scripts && uv run ruff check app tests scripts && cd ..
git add backend/app/llm/prompt.py backend/app/llm/interpret.py backend/tests/test_llm_interpret.py
git commit -m "feat(llm): prompt with day state and model-independent validation of proposals" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 4: Хранилище предложений и API

Чат читает сессию под `record.lock`, но зовёт модель без блокировки, чтобы медленный провайдер не держал датасет. Применение и отклонение идут под `record.lock`. `approve-all` применяет ожидающие предложения по порядку создания и продолжает после отказов.

**Files:**
- Create: `backend/app/llm/schemas.py`, `backend/app/llm/store.py`, `backend/app/api/proposals.py`, `backend/scripts/smoke_proposals.py`
- Modify: `backend/app/api/deps.py`, `backend/app/api/routes.py:client_config`, `backend/app/api/app.py:create_app`
- Test: `backend/tests/test_proposals_api.py`, `backend/tests/test_llm_settings.py` (полная версия)

**Interfaces:**
- Consumes: `interpret`, `NOTHING_FOUND`, `ProposalDraft` (Задача 3), `build_messages` (Задача 3), `OpenAiLlmClient`, `LlmClient`, `LlmError` (Задача 2), `apply_event`, `EventRejected` (План 2), `Deps`, `_record`, `_session` из `app/api/routes.py`, `to_planning_state`, `PlanningState` из `app/api/schemas.py`, `AppDeps`, `DatasetRecord`, `PlanDiff` (План 2), `tests.api_helpers.make_client`, `sample_bundle`, `upload`.
- Produces:
  - `Proposal(id, status, event, rationale, source_text, created_at_version, result_diff=None, error=None)`, `ProposalStatus`, `STATUS_DONE_RU`, `ChatRequest(text)`, `ChatResponse(proposals, clarification)`.
  - `ProposalStore()` с `create(dataset_id, drafts, source_text, version) -> list[Proposal]` (id `pr_1`, `pr_2`…; черновик с ошибкой получает `failed`), `for_dataset(dataset_id)`, `get(dataset_id, proposal_id)`, `save(dataset_id, proposal)`, `urgent_id_factory(dataset_id, taken) -> Callable[[], str]`.
  - `AppDeps.llm: LlmClient | None = None`, `AppDeps.proposals: ProposalStore`, `build_llm(settings) -> LlmClient | None`.
  - Эндпоинты: `POST /api/datasets/{id}/chat`, `GET /api/datasets/{id}/proposals`, `POST /api/datasets/{id}/proposals/{proposal_id}/approve` → `ApproveResponse(proposal, state)`, `POST .../reject` → `Proposal`, `POST /api/datasets/{id}/proposals/approve-all` → `ApproveAllResponse(proposals, state)`, `POST .../reject-all` → `list[Proposal]`.

- [ ] **Step 1: Написать падающие тесты**

`backend/tests/test_proposals_api.py`:

```python
import httpx

from tests.api_helpers import make_client, sample_bundle, upload
from tests.llm_helpers import ScriptedProvider, completion, tool_call


def ready(client):
    dataset_id = upload(client, "bundle.json", sample_bundle().model_dump_json().encode())
    assert client.get(f"/api/datasets/{dataset_id}").json()["status"] == "ready"
    return f"/api/datasets/{dataset_id}"


def with_llm(tmp_path, *responses):
    client, deps = make_client(tmp_path)
    provider = ScriptedProvider(*responses)
    deps.llm = provider.client()
    return client, provider


def test_chat_is_503_without_llm_and_config_says_disabled(tmp_path):
    client, _ = make_client(tmp_path)
    base = ready(client)
    response = client.post(f"{base}/chat", json={"text": "Инженер E1 заболел"})
    assert response.status_code == 503 and "LLM_BASE_URL" in response.json()["detail"]
    assert client.get("/api/config").json()["llm_enabled"] is False


def test_chat_creates_proposals_and_approve_goes_through_event_pipeline(tmp_path):
    calls = [
        tool_call(
            "propose_cancel", {"request_id": "R2", "time": "13:00", "rationale": "Клиент отменил визит"}
        ),
        tool_call("propose_engineer_unavailable", {"engineer_id": "E9", "rationale": "Не выйдет"}, "call_2"),
    ]
    client, provider = with_llm(tmp_path, completion(tool_calls=calls))
    base = ready(client)
    assert client.get("/api/config").json()["llm_enabled"] is True

    response = client.post(f"{base}/chat", json={"text": "  Отмена по R2, а E9 не выйдет  "})
    body = response.json()
    assert response.status_code == 200, body
    assert body["clarification"] == "Инженер «E9» не найден."
    [proposal] = body["proposals"]
    assert proposal["id"] == "pr_1" and proposal["status"] == "pending"
    assert proposal["event"] == {
        "type": "cancel",
        "time": "13:00",
        "request": None,
        "request_id": "R2",
        "engineer_id": None,
    }
    assert proposal["source_text"] == "Отмена по R2, а E9 не выйдет" and proposal["created_at_version"] == 1
    messages = provider.bodies()[0]["messages"]
    assert "Текущее время плана: 00:00." in messages[0]["content"]
    assert messages[1]["content"].endswith("Отмена по R2, а E9 не выйдет")
    assert [p["id"] for p in client.get(f"{base}/proposals").json()] == ["pr_1"]

    approved = client.post(f"{base}/proposals/pr_1/approve")
    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["proposal"]["status"] == "approved"
    assert result["state"]["version"] == 2 and result["state"]["now"] == "13:00"
    assert result["proposal"]["result_diff"]["removed"][0]["request_id"] == "R2"
    assert client.get(f"{base}/state").json()["events"][0]["event"]["request_id"] == "R2"

    again = client.post(f"{base}/proposals/pr_1/approve")
    assert again.status_code == 409 and again.json()["detail"] == "Предложение pr_1 уже применено."
    assert client.post(f"{base}/proposals/pr_9/approve").status_code == 404


def test_urgent_proposal_is_geocoded_and_added_on_approve(tmp_path):
    arguments = {
        "address": "Город Москва, ул.Таганская, д. 3",
        "window_start": "13:00",
        "window_end": "16:00",
        "duration_min": 45,
        "skill": "local",
        "time": "13:00",
        "rationale": "Срочный вызов",
    }
    client, _ = with_llm(tmp_path, completion(tool_calls=[tool_call("propose_urgent_request", arguments)]))
    base = ready(client)
    [proposal] = client.post(f"{base}/chat", json={"text": "Срочно на Таганскую, 3"}).json()["proposals"]
    assert proposal["status"] == "pending" and proposal["event"]["request"]["id"] == "URG-AI-001"
    assert proposal["event"]["request"]["lat"] is not None

    state = client.post(f"{base}/proposals/{proposal['id']}/approve").json()["state"]
    assert "URG-AI-001" in {request["id"] for request in state["requests"]}
    assert state["last_diff"]["added"][0]["request_id"] == "URG-AI-001"


def test_approve_all_survives_stale_proposals_and_reject_endpoints(tmp_path):
    cancels = [
        tool_call("propose_cancel", {"request_id": "R3", "time": "13:00", "rationale": "Отказ клиента"}),
        tool_call(
            "propose_cancel", {"request_id": "R2", "time": "13:00", "rationale": "Отказ клиента"}, "call_2"
        ),
    ]
    restore = [tool_call("propose_restore", {"request_id": "R2", "rationale": "Снова в силе"})]
    client, _ = with_llm(
        tmp_path,
        completion(tool_calls=cancels),
        completion(tool_calls=restore),
        completion(tool_calls=restore),
    )
    base = ready(client)
    assert [
        p["id"] for p in client.post(f"{base}/chat", json={"text": "R3 и R2 отменены"}).json()["proposals"]
    ] == [
        "pr_1",
        "pr_2",
    ]
    manual = client.post(f"{base}/events", json={"type": "cancel", "time": "13:30", "request_id": "R3"})
    assert manual.status_code == 200

    result = client.post(f"{base}/proposals/approve-all").json()
    by_id = {p["id"]: p for p in result["proposals"]}
    assert (by_id["pr_1"]["status"], by_id["pr_1"]["error"]) == ("failed", "Заявка R3 уже отменена.")
    assert by_id["pr_2"]["status"] == "approved" and by_id["pr_2"]["event"]["time"] == "13:30"
    assert result["state"]["version"] == 3

    client.post(f"{base}/chat", json={"text": "R2 снова в силе"})
    client.post(f"{base}/chat", json={"text": "Верните R2"})
    rejected = client.post(f"{base}/proposals/pr_3/reject").json()
    assert rejected["status"] == "rejected"
    assert client.post(f"{base}/proposals/pr_3/reject").json()["detail"] == "Предложение pr_3 уже отклонено."
    after = client.post(f"{base}/proposals/reject-all").json()
    assert [(p["id"], p["status"]) for p in after] == [
        ("pr_1", "failed"),
        ("pr_2", "approved"),
        ("pr_3", "rejected"),
        ("pr_4", "rejected"),
    ]
    assert client.get(f"{base}/state").json()["version"] == 3


def test_chat_errors_are_russian(tmp_path):
    client, _ = with_llm(
        tmp_path, httpx.Response(401, json={"error": {"message": "no"}}), completion(content="")
    )
    base = ready(client)
    empty = client.post(f"{base}/chat", json={"text": "   "})
    assert (
        empty.status_code == 422 and empty.json()["detail"] == "Некорректный запрос: text: сообщение пустое"
    )
    failed = client.post(f"{base}/chat", json={"text": "Арташкин заболел"})
    assert failed.status_code == 503 and "LLM_API_KEY" in failed.json()["detail"]
    nothing = client.post(f"{base}/chat", json={"text": "Как дела?"}).json()
    assert nothing["proposals"] == [] and nothing["clarification"].startswith(
        "Не нашёл в сообщении изменений"
    )
```

Заменить `backend/tests/test_llm_settings.py` полной версией:

`backend/tests/test_llm_settings.py`:

```python
import pytest

from app.api.deps import build_llm
from app.llm.client import OpenAiLlmClient
from app.settings import Settings


def test_llm_tool_mode_default_and_validation():
    assert Settings.from_env({}).llm_tool_mode == "auto"
    assert Settings.from_env({"LLM_TOOL_MODE": "json"}).llm_tool_mode == "json"
    with pytest.raises(ValueError, match="LLM_TOOL_MODE"):
        Settings.from_env({"LLM_TOOL_MODE": "xml"})


def test_build_llm_only_when_base_url_and_model_are_set(tmp_path):
    assert (
        build_llm(Settings.from_env({"DATA_DIR": str(tmp_path), "LLM_BASE_URL": "http://llm.test/v1"}))
        is None
    )
    client = build_llm(
        Settings.from_env({"DATA_DIR": str(tmp_path), "LLM_BASE_URL": "http://llm.test/v1", "LLM_MODEL": "m"})
    )
    assert isinstance(client, OpenAiLlmClient)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd backend && uv run pytest tests/test_proposals_api.py tests/test_llm_settings.py`
Expected: FAIL, `ImportError: cannot import name 'build_llm' from 'app.api.deps'`; тесты чата падают на `assert 404 == 503` и `AttributeError`, потому что роутера и `deps.llm` ещё нет.

- [ ] **Step 3: Модели предложений**

`backend/app/llm/schemas.py`:

```python
"""Предложения изменений плана от помощника (объект Proposal из API-контракта)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.models import Event
from app.planning.models import PlanDiff

ProposalStatus = Literal["pending", "approved", "rejected", "failed"]

STATUS_DONE_RU = {"approved": "применено", "rejected": "отклонено", "failed": "не применилось"}


class Proposal(BaseModel):
    id: str
    status: ProposalStatus
    event: Event
    rationale: str
    source_text: str
    created_at_version: int
    result_diff: PlanDiff | None = None
    error: str | None = None


class ChatRequest(BaseModel):
    text: str = Field(max_length=2000)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("сообщение пустое")
        return value


class ChatResponse(BaseModel):
    proposals: list[Proposal]
    clarification: str | None = None
```

- [ ] **Step 4: Хранилище**

`backend/app/llm/store.py`:

```python
"""Предложения по датасетам в памяти процесса."""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection

from app.llm.interpret import ProposalDraft
from app.llm.schemas import Proposal


class ProposalStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, list[Proposal]] = {}
        self._urgent_numbers: dict[str, int] = {}

    def create(
        self, dataset_id: str, drafts: list[ProposalDraft], source_text: str, version: int
    ) -> list[Proposal]:
        with self._lock:
            items = self._items.setdefault(dataset_id, [])
            created = []
            for draft in drafts:
                proposal = Proposal(
                    id=f"pr_{len(items) + 1}",
                    status="failed" if draft.error else "pending",
                    event=draft.event,
                    rationale=draft.rationale,
                    source_text=source_text,
                    created_at_version=version,
                    error=draft.error,
                )
                items.append(proposal)
                created.append(proposal)
            return created

    def for_dataset(self, dataset_id: str) -> list[Proposal]:
        with self._lock:
            return list(self._items.get(dataset_id, []))

    def get(self, dataset_id: str, proposal_id: str) -> Proposal | None:
        with self._lock:
            return next((p for p in self._items.get(dataset_id, []) if p.id == proposal_id), None)

    def save(self, dataset_id: str, proposal: Proposal) -> None:
        with self._lock:
            items = self._items.setdefault(dataset_id, [])
            for index, existing in enumerate(items):
                if existing.id == proposal.id:
                    items[index] = proposal
                    return
            items.append(proposal)

    def urgent_id_factory(self, dataset_id: str, taken: Collection[str]) -> Callable[[], str]:
        """Номера срочных заявок из чата: URG-AI-001, URG-AI-002… без повторов в датасете."""

        def next_id() -> str:
            with self._lock:
                while True:
                    number = self._urgent_numbers.get(dataset_id, 0) + 1
                    self._urgent_numbers[dataset_id] = number
                    candidate = f"URG-AI-{number:03d}"
                    if candidate not in taken:
                        return candidate

        return next_id
```

- [ ] **Step 5: Роутер**

`backend/app/api/proposals.py`:

```python
"""Чат с помощником и рекомендуемые изменения: модель предлагает, диспетчер подтверждает."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import AppDeps
from app.api.registry import DatasetRecord
from app.api.routes import Deps, _record, _session
from app.api.schemas import PlanningState, to_planning_state
from app.domain.models import Event
from app.llm.client import LlmError
from app.llm.interpret import NOTHING_FOUND, interpret
from app.llm.prompt import build_messages
from app.llm.schemas import STATUS_DONE_RU, ChatRequest, ChatResponse, Proposal
from app.planning.session import EventRejected, PlanningSession, apply_event

router = APIRouter(prefix="/api/datasets/{dataset_id}")

LLM_NOT_CONFIGURED = "Помощник не настроен: задайте LLM_BASE_URL и LLM_MODEL в .env и перезапустите backend."


class ApproveResponse(BaseModel):
    proposal: Proposal
    state: PlanningState


class ApproveAllResponse(BaseModel):
    proposals: list[Proposal]
    state: PlanningState


def _pending(deps: AppDeps, dataset_id: str, proposal_id: str) -> Proposal:
    proposal = deps.proposals.get(dataset_id, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Предложение {proposal_id} не найдено.")
    if proposal.status != "pending":
        raise HTTPException(
            status_code=409, detail=f"Предложение {proposal_id} уже {STATUS_DONE_RU[proposal.status]}."
        )
    return proposal


def _refreshed(event: Event, session: PlanningSession) -> Event:
    """Предложение, созданное до других событий, применяется не раньше текущего времени плана."""
    return event if event.time >= session.now else event.model_copy(update={"time": session.now})


def _approve(deps: AppDeps, record: DatasetRecord, proposal: Proposal) -> Proposal:
    """Применяет одно предложение через общий конвейер событий. Вызывать под record.lock."""
    session = _session(record)
    event = _refreshed(proposal.event, session)
    try:
        updated = apply_event(session, event, deps.ingest.planning)
    except EventRejected as error:
        result = proposal.model_copy(update={"status": "failed", "event": event, "error": str(error)})
    else:
        record.session = updated
        result = proposal.model_copy(
            update={"status": "approved", "event": event, "result_diff": updated.last_diff, "error": None}
        )
    deps.proposals.save(record.dataset_id, result)
    return result


@router.post("/chat", response_model=ChatResponse)
def chat(dataset_id: str, body: ChatRequest, deps: Deps) -> ChatResponse:
    if deps.llm is None:
        raise HTTPException(status_code=503, detail=LLM_NOT_CONFIGURED)
    record = _record(deps, dataset_id)
    with record.lock:
        session = _session(record)
    try:
        result = deps.llm.complete(build_messages(body.text, session))
    except LlmError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    taken = {request.id for request in session.requests} | {
        p.event.request.id for p in deps.proposals.for_dataset(dataset_id) if p.event.request is not None
    }
    interpretation = interpret(
        result, session, deps.ingest.planning, deps.proposals.urgent_id_factory(dataset_id, taken)
    )
    proposals = deps.proposals.create(dataset_id, interpretation.drafts, body.text, session.version)
    clarification = "\n".join(interpretation.clarifications) or None
    if not proposals and clarification is None:
        clarification = NOTHING_FOUND
    return ChatResponse(proposals=proposals, clarification=clarification)


@router.get("/proposals", response_model=list[Proposal])
def list_proposals(dataset_id: str, deps: Deps) -> list[Proposal]:
    _record(deps, dataset_id)
    return deps.proposals.for_dataset(dataset_id)


@router.post("/proposals/approve-all", response_model=ApproveAllResponse)
def approve_all(dataset_id: str, deps: Deps) -> ApproveAllResponse:
    record = _record(deps, dataset_id)
    with record.lock:
        _session(record)
        for proposal in deps.proposals.for_dataset(dataset_id):
            if proposal.status == "pending":
                _approve(deps, record, proposal)
        return ApproveAllResponse(
            proposals=deps.proposals.for_dataset(dataset_id), state=to_planning_state(record.session)
        )


@router.post("/proposals/reject-all", response_model=list[Proposal])
def reject_all(dataset_id: str, deps: Deps) -> list[Proposal]:
    record = _record(deps, dataset_id)
    with record.lock:
        for proposal in deps.proposals.for_dataset(dataset_id):
            if proposal.status == "pending":
                deps.proposals.save(dataset_id, proposal.model_copy(update={"status": "rejected"}))
        return deps.proposals.for_dataset(dataset_id)


@router.post("/proposals/{proposal_id}/approve", response_model=ApproveResponse)
def approve(dataset_id: str, proposal_id: str, deps: Deps) -> ApproveResponse:
    record = _record(deps, dataset_id)
    with record.lock:
        _session(record)
        result = _approve(deps, record, _pending(deps, dataset_id, proposal_id))
        return ApproveResponse(proposal=result, state=to_planning_state(record.session))


@router.post("/proposals/{proposal_id}/reject", response_model=Proposal)
def reject(dataset_id: str, proposal_id: str, deps: Deps) -> Proposal:
    record = _record(deps, dataset_id)
    with record.lock:
        result = _pending(deps, dataset_id, proposal_id).model_copy(update={"status": "rejected"})
        deps.proposals.save(dataset_id, result)
        return result
```

- [ ] **Step 6: Клиент LLM и хранилище в `backend/app/api/deps.py`**

Строку `from dataclasses import dataclass` заменить на:

```python
from dataclasses import dataclass, field
```

После строки импорта `from app.ingest.geocode import ...` добавить:

```python
from app.llm.client import LlmClient, OpenAiLlmClient
from app.llm.store import ProposalStore
```

В конец полей `AppDeps` после `kv: KVCache` добавить поля и перед `build_deps` функцию:

```python
    llm: LlmClient | None = None
    proposals: ProposalStore = field(default_factory=ProposalStore)


def build_llm(settings: Settings) -> LlmClient | None:
    """Клиент OpenAI-совместимого API или None, если LLM_BASE_URL и LLM_MODEL не заданы."""
    if not settings.llm_enabled:
        return None
    return OpenAiLlmClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        mode=settings.llm_tool_mode,
    )
```

Последнюю строку `build_deps` заменить на:

```python
    return AppDeps(
        settings=settings,
        registry=DatasetRegistry(),
        ingest=ingest,
        osrm=osrm,
        kv=kv,
        llm=build_llm(settings),
    )
```

- [ ] **Step 7: `llm_enabled` по фактическому клиенту в `backend/app/api/routes.py`**

В `client_config` строку `llm_enabled=deps.settings.llm_enabled,` заменить на:

```python
        llm_enabled=deps.llm is not None,
```

- [ ] **Step 8: Подключить роутер в `backend/app/api/app.py`**

После `from app.api.deps import AppDeps, build_deps` добавить `from app.api.proposals import router as proposals_router`, а в `create_app` после `app.include_router(router)` добавить:

```python
    app.include_router(proposals_router)
```

- [ ] **Step 9: Скрипт проверки на живом backend**

`backend/scripts/smoke_proposals.py`:

```python
"""Проверка помощника на живом backend: загрузка бандла, план, сообщение, предложения, применение.

Внутри контейнера:  docker compose exec backend python scripts/smoke_proposals.py "Арташкин заболел после обеда"
Флаг --approve применяет все предложения. У backend должны быть заданы LLM_BASE_URL и LLM_MODEL.
Только стандартная библиотека Python.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from smoke_api import call, upload


def main(argv: list[str]) -> int:
    approve = "--approve" in argv
    args = [arg for arg in argv[1:] if arg != "--approve"]
    text = args[0] if args else "Первая бригада заболела после обеда"
    base = args[1] if len(args) > 1 else "http://127.0.0.1:8001/api"
    path = Path(args[2] if len(args) > 2 else "/app/data/bundles/east/bundle.json")

    if not call("GET", f"{base}/config")["llm_enabled"]:
        raise SystemExit("LLM не настроен: задайте LLM_BASE_URL и LLM_MODEL и перезапустите backend.")
    dataset_id = upload(base, path)
    while (status := call("GET", f"{base}/datasets/{dataset_id}"))["status"] == "processing":
        time.sleep(0.5)
    if status["status"] != "ready":
        raise SystemExit(f"Предподсчёт не удался: {status['error']}")
    call("POST", f"{base}/datasets/{dataset_id}/plan")

    started = time.monotonic()
    body = json.dumps({"text": text}).encode()
    reply = call("POST", f"{base}/datasets/{dataset_id}/chat", body, {"Content-Type": "application/json"})
    print(f"ответ помощника за {time.monotonic() - started:.1f} с")
    if reply["clarification"]:
        print("уточнение:", reply["clarification"])
    for proposal in reply["proposals"]:
        event = json.dumps(proposal["event"], ensure_ascii=False)
        error = f" | ошибка: {proposal['error']}" if proposal["error"] else ""
        print(f"{proposal['id']} [{proposal['status']}] {event} | {proposal['rationale']}{error}")

    if approve and any(proposal["status"] == "pending" for proposal in reply["proposals"]):
        result = call("POST", f"{base}/datasets/{dataset_id}/proposals/approve-all")
        for proposal in result["proposals"]:
            print(
                f"{proposal['id']} -> {proposal['status']}"
                + (f": {proposal['error']}" if proposal["error"] else "")
            )
        metrics = result["state"]["plan"]["metrics"]
        print(
            f"версия плана {result['state']['version']}, инженеров {metrics['engineers_used']}, "
            f"км {metrics['total_km']}, не назначено {metrics['unassigned']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 10: Убедиться, что тесты проходят**

Run: `cd backend && uv run pytest tests/test_proposals_api.py tests/test_llm_settings.py && uv run pytest`
Expected: ``5 passed`` и ``2 passed``, затем весь набор ``136 passed``. В выводе возможны два предупреждения Starlette об устаревании httpx в TestClient, они были и в Плане 2.

- [ ] **Step 11: Commit**

```bash
cd backend && uv run ruff format app tests scripts && uv run ruff check app tests scripts && cd ..
git add backend/app/llm/schemas.py backend/app/llm/store.py backend/app/api/proposals.py backend/app/api/deps.py backend/app/api/routes.py backend/app/api/app.py backend/scripts/smoke_proposals.py backend/tests/test_proposals_api.py backend/tests/test_llm_settings.py
git commit -m "feat(api): chat with assistant, proposal store and approve/reject endpoints" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 5: Frontend: запросы, подробности и стор предложений

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`
- Create: `frontend/src/lib/proposals.ts`, `frontend/src/store/useProposalsStore.ts`, `frontend/src/test/proposalFixtures.ts`
- Test: `frontend/src/api/proposalsClient.test.ts`, `frontend/src/lib/proposals.test.ts`, `frontend/src/store/useProposalsStore.test.ts`

**Interfaces:**
- Consumes: `Proposal`, `PlanDiff`, `PlanningState`, `ProposalStatus` из `types.ts`; `request`, `postJson`, `dataset`, `ApiError` из `client.ts`; `formatKm`, `formatWindow`, `shortAddress`, `SKILL_LABELS`, `TRANSPORT_LABELS`, `toMinutes` из `lib/format.ts`; `byId` из `lib/planView.ts`; `useAppStore.getState().datasetId` и `.setPlanningState(state)`; `makePlanningState` из `test/fixtures.ts`; `resetStore` из `test/store.ts` (всё План 3).
- Produces:
  - Типы `ChatResponse`, `ApproveResponse`, `ApproveAllResponse`.
  - `sendChat(datasetId, text)`, `getProposals(datasetId)`, `approveProposal(datasetId, proposalId)`, `rejectProposal(datasetId, proposalId)`, `approveAllProposals(datasetId)`, `rejectAllProposals(datasetId)`.
  - `PROPOSAL_STATUS_LABELS`, `plural(count, one, few, many)`, `proposalDetails(proposal, state) -> string[]`, `diffSummary(diff) -> string`, `upsertProposal(list, proposal)`.
  - `useProposalsStore` с данными `datasetId`, `proposals`, `clarification`, `sending`, `working`, `error` и действиями `load(datasetId)`, `send(text) -> Promise<boolean>`, `approve(id)`, `reject(id)`, `approveAll()`, `rejectAll()`; `initialProposalsData`.
  - `makeProposal(overrides)`, `makeUrgentProposal(overrides)` в `test/proposalFixtures.ts`.

- [ ] **Step 1: Фикстуры предложений**

`frontend/src/test/proposalFixtures.ts`:

```typescript
import type { Proposal } from '../api/types';
import { makePlanningState } from './fixtures';

export function makeProposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: 'pr_1',
    status: 'pending',
    event: { type: 'cancel', time: '13:30', request: null, request_id: '50104', engineer_id: null },
    rationale: 'В сообщении сказано, что клиент на Грайвороновской отказался от визита.',
    source_text: 'Клиент на Грайвороновской отказался',
    created_at_version: 4,
    result_diff: null,
    error: null,
    ...overrides,
  };
}

export function makeUrgentProposal(overrides: Partial<Proposal> = {}): Proposal {
  const request = { ...makePlanningState().requests[7], id: 'URG-AI-001', address: 'Город Москва, ул.Таганская, д. 3' };
  return makeProposal({
    id: 'pr_2',
    event: { type: 'urgent', time: '13:30', request, request_id: null, engineer_id: null },
    rationale: 'Диспетчер сообщил об аварии на Таганской.',
    source_text: 'Авария на Таганской, 3',
    ...overrides,
  });
}
```

- [ ] **Step 2: Написать падающие тесты**

`frontend/src/api/proposalsClient.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest';
import { approveAllProposals, approveProposal, getProposals, rejectAllProposals, rejectProposal, sendChat } from './client';

const reply = (status: number, body: unknown) =>
  ({ ok: status >= 200 && status < 300, status, json: async () => body }) as unknown as Response;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('proposals api client', () => {
  it('posts the chat text as JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { proposals: [], clarification: null }));
    vi.stubGlobal('fetch', fetchMock);
    await sendChat('d1', 'Арташкин заболел');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/datasets/d1/chat');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ text: 'Арташкин заболел' });
  });

  it('uses the proposal endpoints from the contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    await getProposals('d1');
    await approveProposal('d1', 'pr 1');
    await rejectProposal('d1', 'pr_2');
    await approveAllProposals('d1');
    await rejectAllProposals('d1');
    expect(fetchMock.mock.calls.map((call) => [call[0], (call[1] as RequestInit | undefined)?.method ?? 'GET'])).toEqual([
      ['/api/datasets/d1/proposals', 'GET'],
      ['/api/datasets/d1/proposals/pr%201/approve', 'POST'],
      ['/api/datasets/d1/proposals/pr_2/reject', 'POST'],
      ['/api/datasets/d1/proposals/approve-all', 'POST'],
      ['/api/datasets/d1/proposals/reject-all', 'POST'],
    ]);
  });

  it('surfaces the 503 detail when the assistant is not configured', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(503, { detail: 'Помощник не настроен' })));
    await expect(sendChat('d1', 'x')).rejects.toMatchObject({ status: 503, message: 'Помощник не настроен' });
  });
});
```

`frontend/src/lib/proposals.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { makePlanningState } from '../test/fixtures';
import { makeProposal, makeUrgentProposal } from '../test/proposalFixtures';
import { formatKm } from './format';
import { diffSummary, plural, proposalDetails, upsertProposal } from './proposals';

describe('proposals view helpers', () => {
  const state = makePlanningState();

  it('describes a cancel with the address and the current assignment', () => {
    expect(proposalDetails(makeProposal(), state)).toEqual([
      'ул.Грайвороновская, д. 10 к 2 · окно 14:00–16:00',
      'Сейчас в маршруте: Бригада Арташкин, начало 14:00',
    ]);
  });

  it('counts the visits an unavailable engineer would lose', () => {
    const proposal = makeProposal({ event: { type: 'engineer_unavailable', time: '13:30', request: null, request_id: null, engineer_id: 'E01' } });
    expect(proposalDetails(proposal, state)).toEqual(['В маршруте после 13:30: 2 заявки, их перераспределит оптимизатор']);
    const late = makeProposal({ event: { type: 'engineer_unavailable', time: '21:00', request: null, request_id: null, engineer_id: 'E01' } });
    expect(proposalDetails(late, state)).toEqual(['У Бригада Арташкин нет заявок после 21:00']);
  });

  it('describes an urgent request with window, skill and transport', () => {
    expect(proposalDetails(makeUrgentProposal(), state)).toEqual([
      'ул.Таганская, д. 3 · окно 13:00–15:00, 60 мин',
      'Аварийные работы',
      'Нужен транспорт: Автомобиль',
    ]);
  });

  it('summarises the diff of an applied proposal', () => {
    const diff = state.last_diff!;
    expect(diffSummary(diff)).toBe(
      `Новых назначений: 1 · перенесено: 1 · снято: 0 · инженеров 2 → 2 · пробег ${formatKm(diff.metrics_before.total_km)} → ${formatKm(diff.metrics_after.total_km)}`,
    );
  });

  it('pluralises Russian nouns and upserts proposals by id', () => {
    expect([1, 2, 5, 11, 22].map((n) => plural(n, 'заявка', 'заявки', 'заявок'))).toEqual(['заявка', 'заявки', 'заявок', 'заявок', 'заявки']);
    const first = makeProposal();
    const updated = { ...first, status: 'approved' as const };
    expect(upsertProposal([first], updated)).toEqual([updated]);
    expect(upsertProposal([first], makeUrgentProposal()).map((item) => item.id)).toEqual(['pr_1', 'pr_2']);
  });
});
```

`frontend/src/store/useProposalsStore.test.ts`:

```typescript
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    sendChat: vi.fn(),
    getProposals: vi.fn(),
    approveProposal: vi.fn(),
    rejectProposal: vi.fn(),
    approveAllProposals: vi.fn(),
    rejectAllProposals: vi.fn(),
  };
});

import * as api from '../api/client';
import { makePlanningState } from '../test/fixtures';
import { makeProposal, makeUrgentProposal } from '../test/proposalFixtures';
import { resetStore } from '../test/store';
import { useAppStore } from './useAppStore';
import { initialProposalsData, useProposalsStore } from './useProposalsStore';

const pristine = useProposalsStore.getState();

beforeEach(() => {
  vi.resetAllMocks();
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
  useProposalsStore.setState({ ...pristine, ...initialProposalsData }, true);
});

describe('useProposalsStore', () => {
  it('sends a message and keeps proposals and the clarification', async () => {
    vi.mocked(api.sendChat).mockResolvedValue({ proposals: [makeProposal()], clarification: 'Кто именно заболел?' });
    expect(await useProposalsStore.getState().send('Отмена на Грайвороновской')).toBe(true);
    expect(api.sendChat).toHaveBeenCalledWith('d_test', 'Отмена на Грайвороновской');
    expect(useProposalsStore.getState()).toMatchObject({ clarification: 'Кто именно заболел?', sending: false, datasetId: 'd_test' });
    expect(useProposalsStore.getState().proposals.map((item) => item.id)).toEqual(['pr_1']);
  });

  it('approves through the backend and refreshes the shared planning state', async () => {
    useProposalsStore.setState({ datasetId: 'd_test', proposals: [makeProposal()] });
    const state = makePlanningState({ version: 5 });
    vi.mocked(api.approveProposal).mockResolvedValue({
      proposal: makeProposal({ status: 'approved', result_diff: state.last_diff }),
      state,
    });
    await useProposalsStore.getState().approve('pr_1');
    expect(api.approveProposal).toHaveBeenCalledWith('d_test', 'pr_1');
    expect(useProposalsStore.getState().proposals[0].status).toBe('approved');
    expect(useAppStore.getState().state?.version).toBe(5);
    expect(useProposalsStore.getState().working).toBe(false);
  });

  it('reloads the list when a proposal was already processed', async () => {
    useProposalsStore.setState({ datasetId: 'd_test', proposals: [makeProposal()] });
    vi.mocked(api.approveProposal).mockRejectedValue(new api.ApiError(409, 'Предложение pr_1 уже применено.'));
    vi.mocked(api.getProposals).mockResolvedValue([makeProposal({ status: 'approved' })]);
    await useProposalsStore.getState().approve('pr_1');
    expect(useProposalsStore.getState()).toMatchObject({ error: 'Предложение pr_1 уже применено.' });
    expect(useProposalsStore.getState().proposals[0].status).toBe('approved');
  });

  it('approves all, rejects one and rejects all', async () => {
    useProposalsStore.setState({ datasetId: 'd_test', proposals: [makeProposal(), makeUrgentProposal()] });
    vi.mocked(api.rejectProposal).mockResolvedValue(makeProposal({ status: 'rejected' }));
    await useProposalsStore.getState().reject('pr_1');
    expect(useProposalsStore.getState().proposals.map((item) => item.status)).toEqual(['rejected', 'pending']);

    const state = makePlanningState({ version: 6 });
    vi.mocked(api.approveAllProposals).mockResolvedValue({
      proposals: [makeProposal({ status: 'rejected' }), makeUrgentProposal({ status: 'failed', error: 'Окно уже прошло' })],
      state,
    });
    await useProposalsStore.getState().approveAll();
    expect(useProposalsStore.getState().proposals[1].error).toBe('Окно уже прошло');
    expect(useAppStore.getState().state?.version).toBe(6);

    vi.mocked(api.rejectAllProposals).mockResolvedValue([makeProposal({ status: 'rejected' })]);
    await useProposalsStore.getState().rejectAll();
    expect(useProposalsStore.getState().proposals).toHaveLength(1);
  });

  it('resets the list when the dataset changes', async () => {
    useProposalsStore.setState({ datasetId: 'd_old', proposals: [makeProposal()], clarification: 'старое' });
    vi.mocked(api.getProposals).mockResolvedValue([]);
    await useProposalsStore.getState().load('d_test');
    expect(useProposalsStore.getState()).toMatchObject({ datasetId: 'd_test', proposals: [], clarification: null });
  });
});
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `cd frontend && npx vitest run src/api/proposalsClient.test.ts src/lib/proposals.test.ts src/store/useProposalsStore.test.ts`
Expected: FAIL, в `client.ts` нет `sendChat` и остальных функций, модули `./proposals` и `./useProposalsStore` не найдены.

- [ ] **Step 4: Типы ответов в `frontend/src/api/types.ts`**

Перед `export interface ClientConfig {` добавить:

```typescript
export interface ChatResponse {
  proposals: Proposal[];
  clarification: string | null;
}

export interface ApproveResponse {
  proposal: Proposal;
  state: PlanningState;
}

export interface ApproveAllResponse {
  proposals: Proposal[];
  state: PlanningState;
}
```

- [ ] **Step 5: Запросы в `frontend/src/api/client.ts`**

Импорт типов в начале файла заменить на:

```typescript
import type {
  ApproveAllResponse,
  ApproveResponse,
  ChatResponse,
  ClientConfig,
  DatasetStatus,
  Explanation,
  PlanEvent,
  PlanningState,
  Proposal,
  RouteGeometry,
} from './types';
```

В конец файла добавить:

```typescript
const proposal = (datasetId: string, proposalId: string) =>
  `${dataset(datasetId)}/proposals/${encodeURIComponent(proposalId)}`;

export const sendChat = (datasetId: string, text: string) =>
  request<ChatResponse>(`${dataset(datasetId)}/chat`, postJson({ text }));

export const getProposals = (datasetId: string) => request<Proposal[]>(`${dataset(datasetId)}/proposals`);

export const approveProposal = (datasetId: string, proposalId: string) =>
  request<ApproveResponse>(`${proposal(datasetId, proposalId)}/approve`, { method: 'POST' });

export const rejectProposal = (datasetId: string, proposalId: string) =>
  request<Proposal>(`${proposal(datasetId, proposalId)}/reject`, { method: 'POST' });

export const approveAllProposals = (datasetId: string) =>
  request<ApproveAllResponse>(`${dataset(datasetId)}/proposals/approve-all`, { method: 'POST' });

export const rejectAllProposals = (datasetId: string) =>
  request<Proposal[]>(`${dataset(datasetId)}/proposals/reject-all`, { method: 'POST' });
```

- [ ] **Step 6: Подробности предложений**

`frontend/src/lib/proposals.ts`:

```typescript
import type { PlanDiff, PlanningState, Proposal, ProposalStatus } from '../api/types';
import { formatKm, formatWindow, shortAddress, SKILL_LABELS, toMinutes, TRANSPORT_LABELS } from './format';
import { byId } from './planView';

export const PROPOSAL_STATUS_LABELS: Record<ProposalStatus, string> = {
  pending: 'Ждёт решения',
  approved: 'Применено',
  rejected: 'Отклонено',
  failed: 'Не применилось',
};

export function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** Подробности предложения языком диспетчера: что затронет изменение в текущем плане. */
export function proposalDetails(proposal: Proposal, state: PlanningState): string[] {
  const { event } = proposal;
  const requests = byId(state.requests);
  const engineers = byId(state.engineers);

  if (event.type === 'urgent' && event.request) {
    const request = event.request;
    const details = [
      `${shortAddress(request.address)} · окно ${formatWindow(request.window_start, request.window_end)}, ${request.duration_min} мин`,
      SKILL_LABELS[request.skill],
    ];
    if (request.transport_required) details.push(`Нужен транспорт: ${TRANSPORT_LABELS[request.transport_required]}`);
    return details;
  }

  if (event.type === 'engineer_unavailable') {
    const route = state.plan.routes.find((item) => item.engineer_id === event.engineer_id);
    const affected = route ? route.visits.filter((visit) => toMinutes(visit.start) >= toMinutes(event.time)).length : 0;
    const name = engineers.get(event.engineer_id ?? '')?.name ?? event.engineer_id;
    if (affected === 0) return [`У ${name} нет заявок после ${event.time}`];
    return [`В маршруте после ${event.time}: ${affected} ${plural(affected, 'заявка', 'заявки', 'заявок')}, их перераспределит оптимизатор`];
  }

  const request = requests.get(event.request_id ?? '');
  if (!request) return [];
  const details = [`${shortAddress(request.address)} · окно ${formatWindow(request.window_start, request.window_end)}`];
  for (const route of state.plan.routes) {
    const visit = route.visits.find((item) => item.request_id === request.id);
    if (visit) details.push(`Сейчас в маршруте: ${engineers.get(route.engineer_id)?.name ?? route.engineer_id}, начало ${visit.start}`);
  }
  return details;
}

export function diffSummary(diff: PlanDiff): string {
  return (
    `Новых назначений: ${diff.added.length} · перенесено: ${diff.moved.length} · снято: ${diff.removed.length} · ` +
    `инженеров ${diff.metrics_before.engineers_used} → ${diff.metrics_after.engineers_used} · ` +
    `пробег ${formatKm(diff.metrics_before.total_km)} → ${formatKm(diff.metrics_after.total_km)}`
  );
}

export function upsertProposal(list: Proposal[], proposal: Proposal): Proposal[] {
  const index = list.findIndex((item) => item.id === proposal.id);
  if (index < 0) return [...list, proposal];
  return list.map((item, position) => (position === index ? proposal : item));
}
```

- [ ] **Step 7: Стор предложений**

`frontend/src/store/useProposalsStore.ts`:

```typescript
import { create } from 'zustand';
import {
  ApiError,
  approveAllProposals,
  approveProposal,
  getProposals,
  rejectAllProposals,
  rejectProposal,
  sendChat,
} from '../api/client';
import type { Proposal } from '../api/types';
import { upsertProposal } from '../lib/proposals';
import { useAppStore } from './useAppStore';

export interface ProposalsData {
  datasetId: string | null;
  proposals: Proposal[];
  clarification: string | null;
  sending: boolean;
  working: boolean;
  error: string | null;
}

export interface ProposalsActions {
  load(datasetId: string): Promise<void>;
  send(text: string): Promise<boolean>;
  approve(proposalId: string): Promise<void>;
  reject(proposalId: string): Promise<void>;
  approveAll(): Promise<void>;
  rejectAll(): Promise<void>;
}

export type ProposalsState = ProposalsData & ProposalsActions;

export const initialProposalsData: ProposalsData = {
  datasetId: null,
  proposals: [],
  clarification: null,
  sending: false,
  working: false,
  error: null,
};

const message = (error: unknown) => (error instanceof Error ? error.message : 'Неизвестная ошибка');

export const useProposalsStore = create<ProposalsState>()((set, get) => {
  /** Датасет берётся из общего стора; при смене датасета список предложений сбрасывается. */
  function currentDataset(): string | null {
    const datasetId = useAppStore.getState().datasetId;
    if (datasetId !== get().datasetId) set({ ...initialProposalsData, datasetId });
    return datasetId;
  }

  async function mutate(run: (datasetId: string) => Promise<void>) {
    const datasetId = currentDataset();
    if (!datasetId) return;
    set({ working: true, error: null });
    try {
      await run(datasetId);
    } catch (error) {
      set({ error: message(error) });
      if (error instanceof ApiError && error.status === 409) {
        set({ proposals: await getProposals(datasetId).catch(() => get().proposals) });
      }
    } finally {
      set({ working: false });
    }
  }

  return {
    ...initialProposalsData,

    async load(datasetId) {
      if (datasetId !== get().datasetId) set({ ...initialProposalsData, datasetId });
      try {
        const proposals = await getProposals(datasetId);
        if (get().datasetId === datasetId) set({ proposals });
      } catch (error) {
        set({ error: message(error) });
      }
    },

    async send(text) {
      const datasetId = currentDataset();
      if (!datasetId) return false;
      set({ sending: true, error: null, clarification: null });
      try {
        const response = await sendChat(datasetId, text);
        set({
          proposals: response.proposals.reduce(upsertProposal, get().proposals),
          clarification: response.clarification,
        });
        return true;
      } catch (error) {
        set({ error: message(error) });
        return false;
      } finally {
        set({ sending: false });
      }
    },

    approve: (proposalId) =>
      mutate(async (datasetId) => {
        const response = await approveProposal(datasetId, proposalId);
        set({ proposals: upsertProposal(get().proposals, response.proposal) });
        useAppStore.getState().setPlanningState(response.state);
      }),

    reject: (proposalId) =>
      mutate(async (datasetId) => {
        set({ proposals: upsertProposal(get().proposals, await rejectProposal(datasetId, proposalId)) });
      }),

    approveAll: () =>
      mutate(async (datasetId) => {
        const response = await approveAllProposals(datasetId);
        set({ proposals: response.proposals });
        useAppStore.getState().setPlanningState(response.state);
      }),

    rejectAll: () =>
      mutate(async (datasetId) => {
        set({ proposals: await rejectAllProposals(datasetId) });
      }),
  };
});
```

- [ ] **Step 8: Убедиться, что тесты проходят**

Run: `cd frontend && npx vitest run src/api/proposalsClient.test.ts src/lib/proposals.test.ts src/store/useProposalsStore.test.ts`
Expected: `Tests  13 passed`

- [ ] **Step 9: Commit**

```bash
cd frontend && npm test && npm run build && cd ..
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/lib/proposals.ts frontend/src/store/useProposalsStore.ts frontend/src/test/proposalFixtures.ts frontend/src/api/proposalsClient.test.ts frontend/src/lib/proposals.test.ts frontend/src/store/useProposalsStore.test.ts
git commit -m "feat(frontend): proposals API client, helpers and store" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 6: Frontend: вкладка «Рекомендуемые изменения»

Вкладка показывает поле сообщения, уточнение помощника, карточки предложений от новых к старым и кнопки «Применить все» и «Отклонить все». Заголовок карточки берётся из `describeEvent` Плана 3, чтобы предложение и баннер изменений описывали событие одинаково. Пока показан план «До события», действия заблокированы. Без настроенного LLM вкладка объясняет, какие переменные задать.

**Files:**
- Create: `frontend/src/components/panel/ProposalsTab.tsx`
- Modify: `frontend/src/components/panel/tabs.ts`, `frontend/src/styles.css` (в конец файла), `frontend/src/components/panel/OtherTabs.test.tsx`, `frontend/src/App.test.tsx`
- Test: `frontend/src/components/panel/ProposalsTab.test.tsx`

**Interfaces:**
- Consumes: `useProposalsStore`, `initialProposalsData`, `proposalDetails`, `diffSummary`, `PROPOSAL_STATUS_LABELS`, фикстуры предложений (Задача 5); `describeEvent` из `lib/events.ts`, `byId`, `useAppStore` (`config.llm_enabled`, `datasetId`, `state`, `showPrevious`), `PANEL_TABS`, CSS-классы `note`, `muted`, `empty`, `error-text`, `field`, `btn`, `btn-small`, `btn-primary`, `badge`, `tab-toolbar` (План 3).
- Produces: `ProposalsTab` (компонент без пропсов) и запись `{ id: 'proposals', title: 'Рекомендуемые изменения', component: ProposalsTab }` последней в `PANEL_TABS`.

- [ ] **Step 1: Написать падающий тест**

`frontend/src/components/panel/ProposalsTab.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { initialProposalsData, useProposalsStore } from '../../store/useProposalsStore';
import { makePlanningState } from '../../test/fixtures';
import { makeProposal, makeUrgentProposal } from '../../test/proposalFixtures';
import { resetStore } from '../../test/store';
import { ProposalsTab } from './ProposalsTab';

const pristine = useProposalsStore.getState();
const enabled = { yandex_maps_api_key: null, llm_enabled: true, osrm_available: true };

function setup(patch = {}) {
  const actions = {
    load: vi.fn().mockResolvedValue(undefined),
    send: vi.fn().mockResolvedValue(true),
    approve: vi.fn().mockResolvedValue(undefined),
    reject: vi.fn().mockResolvedValue(undefined),
    approveAll: vi.fn().mockResolvedValue(undefined),
    rejectAll: vi.fn().mockResolvedValue(undefined),
  };
  useProposalsStore.setState({ ...pristine, ...initialProposalsData, datasetId: 'd_test', ...actions, ...patch }, true);
  return actions;
}

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState(), config: enabled });
});

describe('ProposalsTab', () => {
  it('explains how to enable the assistant when LLM is not configured', () => {
    setup();
    useAppStore.setState({ config: { ...enabled, llm_enabled: false } });
    render(<ProposalsTab />);
    expect(screen.getByText(/Помощник не настроен/)).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('sends the message and clears the field on success', async () => {
    const actions = setup();
    render(<ProposalsTab />);
    expect(actions.load).toHaveBeenCalledWith('d_test');
    const field = screen.getByLabelText('Сообщение помощнику');
    fireEvent.change(field, { target: { value: '  Арташкин заболел после обеда ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(actions.send).toHaveBeenCalledWith('Арташкин заболел после обеда'));
    await waitFor(() => expect(field).toHaveValue(''));
  });

  it('renders cards newest first with approve and reject for pending ones', () => {
    const actions = setup({
      proposals: [
        makeProposal({ status: 'approved', result_diff: makePlanningState().last_diff }),
        makeUrgentProposal(),
        makeProposal({ id: 'pr_3', status: 'failed', error: 'Заявка 50104 уже отменена.' }),
      ],
      clarification: 'Уточните, какую заявку вернуть?',
    });
    render(<ProposalsTab />);
    expect(screen.getByRole('status')).toHaveTextContent('Уточните, какую заявку вернуть?');

    const cards = screen.getAllByRole('listitem').filter((item) => item.classList.contains('proposal'));
    expect(cards.map((card) => card.querySelector('strong')?.textContent)).toEqual([
      'Отмена заявки 50104 в 13:30',
      'Срочная заявка URG-AI-001 в 13:30',
      'Отмена заявки 50104 в 13:30',
    ]);
    expect(within(cards[0]).getByText('Не применилось')).toBeInTheDocument();
    expect(within(cards[0]).getByText('Заявка 50104 уже отменена.')).toBeInTheDocument();
    expect(within(cards[2]).getByText(/^Новых назначений: 1/)).toBeInTheDocument();
    expect(within(cards[2]).queryByRole('button', { name: 'Применить' })).not.toBeInTheDocument();

    fireEvent.click(within(cards[1]).getByRole('button', { name: 'Применить' }));
    expect(actions.approve).toHaveBeenCalledWith('pr_2');
    fireEvent.click(within(cards[1]).getByRole('button', { name: 'Отклонить' }));
    expect(actions.reject).toHaveBeenCalledWith('pr_2');

    fireEvent.click(screen.getByRole('button', { name: 'Применить все (1)' }));
    expect(actions.approveAll).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Отклонить все' }));
    expect(actions.rejectAll).toHaveBeenCalled();
  });

  it('locks actions while the previous plan is shown', () => {
    setup({ proposals: [makeProposal()] });
    useAppStore.setState({ showPrevious: true });
    render(<ProposalsTab />);
    expect(screen.getByRole('button', { name: 'Применить' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Применить все (1)' })).toBeDisabled();
    expect(screen.getByText(/Переключитесь на план «После события»/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Обновить ожидания вкладок в тестах Плана 3**

В `frontend/src/components/panel/OtherTabs.test.tsx` заменить:

```typescript
  it('registers the four base tabs in order with an unassigned badge', () => {
    expect(PANEL_TABS.map((tab) => tab.id)).toEqual(['requests', 'timeline', 'unassigned', 'comparison']);
```

на:

```typescript
  it('registers the base tabs and the proposals tab in order with an unassigned badge', () => {
    expect(PANEL_TABS.map((tab) => tab.id)).toEqual(['requests', 'timeline', 'unassigned', 'comparison', 'proposals']);
```

В `frontend/src/App.test.tsx` заменить:

```typescript
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Заявки', 'Таймлайн', 'Неназначенные1', 'Сравнение']);
```

на:

```typescript
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Заявки', 'Таймлайн', 'Неназначенные1', 'Сравнение', 'Рекомендуемые изменения']);
```

- [ ] **Step 3: Убедиться, что тесты падают**

Run: `cd frontend && npx vitest run src/components/panel/ProposalsTab.test.tsx src/components/panel/OtherTabs.test.tsx src/App.test.tsx`
Expected: FAIL, модуль `./ProposalsTab` не найден, в списке вкладок нет `proposals`.

- [ ] **Step 4: Реализовать вкладку**

`frontend/src/components/panel/ProposalsTab.tsx`:

```tsx
import { useEffect, useState } from 'react';
import type { PlanningState, Proposal } from '../../api/types';
import { describeEvent } from '../../lib/events';
import { byId } from '../../lib/planView';
import { diffSummary, PROPOSAL_STATUS_LABELS, proposalDetails } from '../../lib/proposals';
import { useAppStore } from '../../store/useAppStore';
import { useProposalsStore } from '../../store/useProposalsStore';

const PLACEHOLDER = 'Например: Арташкин заболел после обеда, а заявку на Дубининской клиент отменил';

interface CardProps {
  proposal: Proposal;
  state: PlanningState;
  disabled: boolean;
  onApprove(proposalId: string): void;
  onReject(proposalId: string): void;
}

function ProposalCard({ proposal, state, disabled, onApprove, onReject }: CardProps) {
  const details = proposalDetails(proposal, state);
  return (
    <li className={`proposal proposal--${proposal.status}`}>
      <div className="proposal__head">
        <strong>{describeEvent(proposal.event, byId(state.engineers))}</strong>
        <span className={`badge proposal__status proposal__status--${proposal.status}`}>
          {PROPOSAL_STATUS_LABELS[proposal.status]}
        </span>
      </div>
      {details.length > 0 && (
        <ul className="proposal__details">
          {details.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
      )}
      <p className="proposal__rationale">{proposal.rationale}</p>
      <p className="muted proposal__source">Из сообщения: «{proposal.source_text}»</p>
      {proposal.error && <p className="error-text">{proposal.error}</p>}
      {proposal.result_diff && <p className="proposal__result">{diffSummary(proposal.result_diff)}</p>}
      {proposal.status === 'pending' && (
        <div className="proposal__actions">
          <button type="button" className="btn btn-small btn-primary" disabled={disabled} onClick={() => onApprove(proposal.id)}>
            Применить
          </button>
          <button type="button" className="btn btn-small" disabled={disabled} onClick={() => onReject(proposal.id)}>
            Отклонить
          </button>
        </div>
      )}
    </li>
  );
}

export function ProposalsTab() {
  const llmEnabled = useAppStore((s) => s.config?.llm_enabled ?? false);
  const datasetId = useAppStore((s) => s.datasetId);
  const state = useAppStore((s) => s.state);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const { proposals, clarification, sending, working, error, load, send, approve, reject, approveAll, rejectAll } =
    useProposalsStore();
  const [text, setText] = useState('');

  useEffect(() => {
    if (llmEnabled && datasetId) void load(datasetId);
  }, [llmEnabled, datasetId, load]);

  if (!llmEnabled) {
    return (
      <div className="proposals">
        <p className="note">
          Помощник не настроен. Задайте LLM_BASE_URL, LLM_API_KEY и LLM_MODEL в файле .env и перезапустите backend. Кнопки
          событий на панели работают и без помощника.
        </p>
      </div>
    );
  }
  if (!state) return null;

  const pending = proposals.filter((item) => item.status === 'pending');
  const locked = sending || working || showPrevious;

  return (
    <div className="proposals">
      <form
        className="proposals__chat"
        noValidate
        onSubmit={async (event) => {
          event.preventDefault();
          const message = text.trim();
          if (message && (await send(message))) setText('');
        }}
      >
        <label className="field">
          <span>Сообщение помощнику</span>
          <textarea value={text} rows={3} placeholder={PLACEHOLDER} onChange={(event) => setText(event.target.value)} />
        </label>
        <div className="proposals__actions">
          <span className="muted">Помощник только предлагает. План меняется после «Применить».</span>
          <button type="submit" className="btn btn-primary" disabled={locked || !text.trim()}>
            {sending ? 'Разбираю…' : 'Отправить'}
          </button>
        </div>
      </form>
      {clarification && (
        <p className="note proposals__clarification" role="status">
          {clarification}
        </p>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {showPrevious && <p className="muted">Переключитесь на план «После события», чтобы применять предложения.</p>}
      <div className="tab-toolbar proposals__bulk">
        <strong>Рекомендуемые изменения</strong>
        <button type="button" className="btn btn-small btn-primary" disabled={locked || pending.length === 0} onClick={() => void approveAll()}>
          Применить все ({pending.length})
        </button>
        <button type="button" className="btn btn-small" disabled={locked || pending.length === 0} onClick={() => void rejectAll()}>
          Отклонить все
        </button>
      </div>
      {proposals.length === 0 ? (
        <p className="empty">Пока нет предложений. Опишите ситуацию своими словами.</p>
      ) : (
        <ul className="proposal-list">
          {[...proposals].reverse().map((proposal) => (
            <ProposalCard
              key={proposal.id}
              proposal={proposal}
              state={state}
              disabled={locked}
              onApprove={(id) => void approve(id)}
              onReject={(id) => void reject(id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Зарегистрировать вкладку в `frontend/src/components/panel/tabs.ts`**

После `import { ComparisonTab } from './ComparisonTab';` добавить `import { ProposalsTab } from './ProposalsTab';`, а последней строкой массива `PANEL_TABS` после записи `comparison` добавить:

```typescript
  { id: 'proposals', title: 'Рекомендуемые изменения', component: ProposalsTab },
```

- [ ] **Step 6: Стили карточек в конец `frontend/src/styles.css`**

```css
/* Рекомендуемые изменения (План 4) */
.proposals {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.proposals__chat textarea {
  font: inherit;
  font-size: 14px;
  color: var(--text);
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  resize: vertical;
  min-height: 64px;
}

.proposals__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
}

.proposals__clarification {
  white-space: pre-line;
}

.proposals__bulk {
  display: flex;
  align-items: center;
  gap: 8px;
}

.proposals__bulk strong {
  margin-right: auto;
}

.proposal-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.proposal {
  border: 1px solid var(--border);
  border-left: 3px solid var(--primary);
  border-radius: var(--radius);
  padding: 10px 12px;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.proposal p {
  margin: 0;
}

.proposal--approved {
  border-left-color: var(--ok);
}

.proposal--failed {
  border-left-color: var(--danger);
}

.proposal--rejected {
  border-left-color: var(--border);
  opacity: 0.7;
}

.proposal__head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
}

.proposal__details {
  margin: 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 13px;
}

.proposal__status--pending {
  background: var(--primary-soft);
  color: var(--primary);
}

.proposal__status--approved {
  background: #e7f4ee;
  color: var(--ok);
}

.proposal__status--failed {
  background: var(--danger-soft);
  color: var(--danger);
}

.proposal__actions {
  display: flex;
  gap: 8px;
}
```

- [ ] **Step 7: Убедиться, что всё проходит**

Run: `cd frontend && npx vitest run src/components/panel/ProposalsTab.test.tsx src/components/panel/OtherTabs.test.tsx src/App.test.tsx && npm test && npm run build`
Expected: `Tests  11 passed` для трёх файлов, затем весь набор ``Test Files  21 passed (21)`, `Tests  90 passed (90)`` и `✓ built`.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/panel/ProposalsTab.tsx frontend/src/components/panel/ProposalsTab.test.tsx frontend/src/components/panel/tabs.ts frontend/src/components/panel/OtherTabs.test.tsx frontend/src/App.test.tsx frontend/src/styles.css
git commit -m "feat(frontend): recommended changes tab with approve and reject" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```

### Task 7: Настройка провайдера, документация и проверка стенда

**Files:**
- Modify: `.env.example`, `README.md`

**Interfaces:**
- Consumes: переменные `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TOOL_MODE` (Задачи 1 и 4), `scripts/smoke_proposals.py` (Задача 4), compose Плана 2 (backend читает `.env` через `env_file`).
- Produces: документированная настройка помощника и шаг сценария демонстрации.

- [ ] **Step 1: `.env.example`**

Блок

```
# OpenAI-совместимый API для вкладки «Рекомендуемые изменения» (План 4)
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
```

заменить на:

```
# OpenAI-совместимый API для вкладки «Рекомендуемые изменения».
# Примеры адресов: https://openrouter.ai/api/v1, https://api.deepseek.com, http://host.docker.internal:11434/v1 (Ollama).
# Имя модели берётся у провайдера. Без LLM_BASE_URL и LLM_MODEL помощник выключен, остальное работает.
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
# auto: инструменты, при отказе провайдера JSON; tools: только инструменты; json: только JSON в тексте ответа
LLM_TOOL_MODE=auto
```

- [ ] **Step 2: `README.md`, таблица переменных**

Строку

```
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | не заданы | OpenAI-совместимый API для рекомендаций |
```

заменить на:

```
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | не заданы | OpenAI-совместимый API помощника; ключ необязателен для локальных моделей |
| `LLM_TOOL_MODE` | `auto` | `tools` только вызов инструментов, `json` только JSON в ответе, `auto` инструменты с переходом на JSON |
```

- [ ] **Step 3: `README.md`, раздел о помощнике**

Перед заголовком `## Подготовка данных` вставить:

````markdown
## Помощник диспетчера

Вкладка «Рекомендуемые изменения» принимает сообщение обычным языком, например «Арташкин заболел после обеда, а клиент на Дубининской отказался». Backend отправляет модели правила и состояние дня, модель отвечает вызовами инструментов: срочная заявка, отмена, возврат, недоступность инженера или уточняющий вопрос. Модель ничего не меняет сама. Каждый ответ проверяется без участия модели: инженер и заявка должны однозначно найтись, время не раньше текущего, адрес срочной заявки находится геокодером, событие не противоречит плану. Прошедшие проверку предложения ждут решения диспетчера, остальные показываются с причиной. Кнопки «Применить» и «Применить все» отправляют событие в тот же пересчёт, что и ручные кнопки, поэтому карта, списки и баннер изменений обновляются одинаково.

Подойдёт любой OpenAI-совместимый провайдер. Для экономии выбирайте недорогую модель с поддержкой вызова инструментов; если провайдер инструменты не поддерживает, `LLM_TOOL_MODE=auto` сам перейдёт на JSON-ответ. Промпт растёт с числом заявок: для 66 заявок Востока это около 20 тысяч символов на сообщение.

Проверка без интерфейса:

```bash
docker compose exec backend python scripts/smoke_proposals.py "Арташкин заболел после обеда" --approve
```
````

- [ ] **Step 4: `README.md`, сценарий и ограничения**

В разделе «Сценарий демонстрации» после пункта 5 вставить строку:

```
   Альтернатива: написать то же событие во вкладке «Рекомендуемые изменения» и нажать «Применить».
```

В разделе «Известные ограничения» последним пунктом добавить:

```
- Предложения помощника хранятся в памяти backend вместе с датасетом. Качество распознавания зависит от выбранной модели; поиск заявки по части адреса простой, при нескольких совпадениях помощник просит уточнить номер.
```

- [ ] **Step 5: Проверить стенд**

Run:
```bash
docker compose up -d --build --wait
curl -s http://localhost:8000/api/config
```
Expected: JSON с `"llm_enabled": true`, если в `.env` заданы `LLM_BASE_URL` и `LLM_MODEL`, иначе `false`.

Run (только с настроенным провайдером): `docker compose exec backend python scripts/smoke_proposals.py "Арташкин заболел после обеда" --approve`
Expected: строка `ответ помощника за N с`, одно или несколько предложений `[pending]` или уточнение, затем `pr_1 -> approved` и новая версия плана. Если провайдер не настроен, скрипт завершается текстом «LLM не настроен…».

Вручную в браузере на http://localhost:8000: загрузить `data/raw/east_synthetic.csv`, нажать «Спланировать», открыть вкладку «Рекомендуемые изменения», отправить сообщение, применить предложение и убедиться, что появился баннер изменений и сдвинулась версия плана.

- [ ] **Step 6: Commit**

```bash
git add .env.example README.md
git commit -m "docs: configure OpenAI-compatible assistant and describe recommended changes" -m $'Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_01ES9WBRMChrK8yPZ6LyrqNQ'
```
