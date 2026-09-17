# Варианты исправления при событии дня — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** На «ломающие» события дня сервер считает три варианта исправления (оптимально, минимум перестановок, ничего не менять), время плана не проходит событие без выбора, фронт ставит часы на паузу и показывает окно выбора с рекомендацией; выбор можно поменять с метки события.

**Architecture:** Стратегия хранится в событии шкалы (`TimelineEntry.variant`) и входит в ключ кэша шагов. `Timeline.walk` останавливается на «ломающем» событии без выбора (`Walk.awaiting`), `move_cached` ставит текущее время на его время, `planning_state` отдаёт `pending_choice` с тремя посчитанными вариантами. Сравнение и тексты «чем лучше / хуже» строятся правилами в `app/planning/variants.py`. Фронт открывает окно по `pending_choice`, отправляет выбор `PUT …/variant` и продолжает часы.

**Tech Stack:** Python 3.12, FastAPI, pydantic 2, OR-Tools; React 18, TypeScript, zustand, vitest.

**Spec:** `docs/superpowers/specs/2026-09-17-event-variants-design.md`

## Global Constraints

- Все тексты для диспетчера, комментарии и сообщения коммитов — на русском, в стиле окружающего кода.
- «Ломающие» события: `urgent`, `engineer_unavailable`, `engineer_transport_changed`, `engineer_delayed`. У остальных `variant` всегда `null`, поведение не меняется.
- Стратегии: `optimal`, `stable`, `keep`. Названия: «Оптимально по дню», «Минимум перестановок», «Ничего не менять». Суть: «Пересчитать остаток дня целиком», «Чужие маршруты почти не трогаем», «Оставить маршруты как есть».
- `STABLE_REASSIGNMENT = 500_000` (условные 500 км за перенос заявки к другому инженеру); остальные веса как у уровня нагрузки.
- Рекомендация: минимум по `(unassigned + late, engineers_used, moved, round(total_km, 1), порядок optimal < stable < keep)`.
- Старый `POST /datasets/{id}/events` и одобрение предложений помощника (`app/api/proposals.py`) создают событие сразу со стратегией `optimal` и не останавливаются на выборе.
- Бэкенд-тесты: `cd backend && ~/.local/bin/uv run pytest -q -o addopts= <files>`; линт: `~/.local/bin/uv run ruff check app tests scripts && ~/.local/bin/uv run ruff format app tests scripts`.
- Фронтенд: `cd frontend && npx vitest run <files>`, `npx tsc --noEmit`, `npm run build`.
- Никогда не коммитить `data/transit/*`, `.env`, PDF/zip/xlsx из корня репозитория.
- Каждое сообщение коммита заканчивается строкой `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

---

### Task 1: Стратегии и сравнение вариантов (бэкенд, без шкалы)

**Files:**
- Create: `backend/app/planning/variants.py`
- Modify: `backend/app/planning/models.py` (модели `EventVariant`, `VariantOption`, `EventChoice`)
- Modify: `backend/app/planning/session.py` (`_solve`, `apply_event`)
- Modify: `backend/tests/timeline_helpers.py` (`FcfsSolves.solve` принимает `variant`)
- Test: `backend/tests/test_variants.py`

**Interfaces:**
- Produces:
  - `app.planning.models.EventVariant = Literal["optimal", "stable", "keep"]`
  - `app.planning.models.VariantOption(variant, title, summary, metrics: Metrics, late: int, moved: int, pros: list[str], cons: list[str], recommended: bool)`
  - `app.planning.models.EventChoice(entry_id: str, event: Event, metrics_before: Metrics, late_before: int, variants: list[VariantOption], current: EventVariant | None)`
  - `app.planning.variants`: `VARIANTS`, `BREAKING_EVENTS`, `STABLE_REASSIGNMENT`, `VARIANT_TITLES`, `VARIANT_SUMMARIES`, `KEEP_TEXT`, `is_choosable(event: Event) -> bool`, `late_visits(plan: Plan) -> int`, `keep_plan(problem: Problem) -> Plan`, `Outcome(variant, plan, diff)`, `build_choice(entry_id, event, before: Plan, outcomes: Sequence[Outcome], current) -> EventChoice`
  - `app.planning.session.apply_event(session, event, ctx, *, version=None, variant: EventVariant = "optimal")`
  - `app.planning.session._solve(problem, workload_level, time_limit_s, variant: EventVariant = "optimal")`

- [ ] **Step 1: Модели в `backend/app/planning/models.py`**

После класса `AppliedEvent` добавить:

```python
EventVariant = Literal["optimal", "stable", "keep"]


class VariantOption(BaseModel):
    """Один вариант исправления плана на событие: итоги и отличия от рекомендованного."""

    variant: EventVariant
    title: str
    summary: str
    metrics: Metrics
    late: int  # визиты, которые начнутся позже конца окна
    moved: int  # заявки, переехавшие к другой бригаде относительно плана до события
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    recommended: bool = False


class EventChoice(BaseModel):
    """Выбор варианта для «ломающего» события шкалы: три варианта от одного плана до события."""

    entry_id: str
    event: Event
    metrics_before: Metrics
    late_before: int
    variants: list[VariantOption]
    current: EventVariant | None = None
```

- [ ] **Step 2: Написать падающие тесты `backend/tests/test_variants.py`**

```python
"""Варианты исправления плана на «ломающее» событие: стратегии, сравнение и тексты. Без шкалы и API."""

import pytest

from app.domain.enums import EventType, ReasonCode, Transport
from app.domain.models import Event, Metrics, Plan, Route, Visit
from app.planning import session as session_module
from app.planning.models import DiffMove, PlanDiff
from app.planning.session import apply_event
from app.planning.variants import (
    KEEP_TEXT,
    STABLE_REASSIGNMENT,
    VARIANTS,
    Outcome,
    build_choice,
    is_choosable,
    late_visits,
)
from tests.helpers import eng, req
from tests.planning_helpers import busy_engineer, context, new_session, routes
from tests.timeline_helpers import fcfs_solves


@pytest.fixture
def solves(monkeypatch):
    return fcfs_solves(monkeypatch)


def _upcoming(plan, engineer_id, minute):
    route = next(route for route in plan.routes if route.engineer_id == engineer_id)
    return [visit.request_id for visit in route.visits if visit.start >= minute]


def test_only_events_that_break_the_plan_are_choosable():
    choosable = {
        EventType.URGENT,
        EventType.ENGINEER_UNAVAILABLE,
        EventType.ENGINEER_TRANSPORT_CHANGED,
        EventType.ENGINEER_DELAYED,
    }
    for event_type in EventType:
        event = Event.model_construct(type=event_type, time=780)
        assert is_choosable(event) is (event_type in choosable)
    assert VARIANTS == ("optimal", "stable", "keep")


def test_keep_leaves_the_upcoming_visits_of_an_unavailable_engineer_without_an_engineer(solves):
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    upcoming = _upcoming(base.plan, busy, 13 * 60)
    assert upcoming, "в тестовом дне у занятого инженера должны быть визиты после 13:00"
    event = Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id=busy)

    kept = apply_event(base, event, ctx, variant="keep")

    before = routes(base.plan)
    after = routes(kept.plan)
    assert after[busy] == [rid for rid in before[busy] if rid not in upcoming]
    assert all(after[other] == before[other] for other in before if other != busy)
    reasons = {item.request_id: item for item in kept.plan.unassigned}
    assert set(upcoming) <= set(reasons)
    assert all(reasons[rid].reason_text == KEEP_TEXT for rid in upcoming)
    # Без решателя: FCFS в тестах подменяет _solve, и «ничего не менять» его не вызывает.
    assert solves == []


def test_keep_drops_visits_that_need_a_transport_the_engineer_no_longer_has(solves):
    ctx = context()
    requests = [
        req("R1", 1, 0, "10:00", "12:00"),
        req("R2", 1.2, 0, "14:00", "16:00", transport=Transport.CAR),
        req("R3", -1, 0, "15:00", "17:00"),
    ]
    base = new_session(ctx, requests=requests, engineers=[eng("E1"), eng("E2")])
    owner = next(route.engineer_id for route in base.plan.routes if "R2" in [v.request_id for v in route.visits])
    event = Event(type=EventType.ENGINEER_TRANSPORT_CHANGED, time="13:00", engineer_id=owner, transport=Transport.BIKE)

    kept = apply_event(base, event, ctx, variant="keep")

    reasons = {item.request_id: item for item in kept.plan.unassigned}
    assert reasons["R2"].reason_code == ReasonCode.NO_TRANSPORT
    assert reasons["R2"].reason_text.startswith(KEEP_TEXT)
    assert "R2" not in routes(kept.plan)[owner]


def test_keep_shifts_a_delayed_route_and_shows_the_lateness(solves):
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    event = Event(type=EventType.ENGINEER_DELAYED, time="10:05", engineer_id=busy, delay_min=300)

    kept = apply_event(base, event, ctx, variant="keep")

    assert late_visits(kept.plan) > 0
    kept_route = routes(kept.plan)[busy]
    assert kept_route == [rid for rid in routes(base.plan)[busy] if rid in kept_route]
    assert solves == []


def test_keep_leaves_an_urgent_request_without_an_engineer(solves):
    ctx = context()
    base = new_session(ctx)
    urgent = req("U1", 0.5, 0.5, "13:00", "15:00")
    event = Event(type=EventType.URGENT, time="12:00", request=urgent)

    kept = apply_event(base, event, ctx, variant="keep")

    assert {item.request_id: item.reason_text for item in kept.plan.unassigned}["U1"] == KEEP_TEXT
    assert all("U1" not in visits for visits in routes(kept.plan).values())


def test_stable_solves_with_an_expensive_reassignment_and_optimal_keeps_the_level_weights(monkeypatch):
    captured = []

    class Capture:
        def __init__(self, time_limit_s, weights):
            captured.append(weights)

        def solve(self, problem):
            return Plan(solver="ortools", routes=[], unassigned=[], metrics=Metrics(engineers_used=0, km_per_engineer={}, total_km=0, assigned=0, unassigned=0))

    monkeypatch.setattr(session_module, "OrToolsSolver", Capture)
    problem = new_session(context()).problem
    session_module._solve(problem, 1, 1)
    session_module._solve(problem, 1, 1, "stable")

    assert captured[0].reassignment == 20_000
    assert captured[1].reassignment == STABLE_REASSIGNMENT == 500_000
    assert captured[1].vehicle_fixed_cost == captured[0].vehicle_fixed_cost


def test_events_that_do_not_break_the_plan_ignore_the_variant(solves):
    ctx = context()
    base = new_session(ctx)
    cancel = Event(type=EventType.CANCEL, time="09:30", request_id="R2")
    assert apply_event(base, cancel, ctx, variant="keep").plan == apply_event(base, cancel, ctx).plan


def _plan(unassigned, engineers, km, late=0):
    visits = [Visit(request_id=f"L{k}", arrival=600, start=600, end=630, leg_km=1, leg_min=5, late_min=5) for k in range(late)]
    return Plan(
        solver="ortools",
        routes=[Route(engineer_id="E1", visits=visits, total_km=km, total_travel_min=0)],
        unassigned=[],
        metrics=Metrics(engineers_used=engineers, km_per_engineer={}, total_km=km, assigned=0, unassigned=unassigned),
    )


def _diff(before, after, moved):
    return PlanDiff(
        moved=[DiffMove(request_id=f"M{k}", from_engineer_id="E1", to_engineer_id="E2") for k in range(moved)],
        metrics_before=before.metrics,
        metrics_after=after.metrics,
    )


def test_choice_recommends_by_clients_then_brigades_then_moves_then_km_and_explains_differences():
    before = _plan(0, 5, 100.0)
    optimal = _plan(0, 5, 110.0)
    stable = _plan(0, 6, 130.5)
    keep = _plan(2, 5, 100.0, late=1)
    outcomes = [
        Outcome("optimal", optimal, _diff(before, optimal, 5)),
        Outcome("stable", stable, _diff(before, stable, 1)),
        Outcome("keep", keep, _diff(before, keep, 0)),
    ]
    event = Event(type=EventType.ENGINEER_UNAVAILABLE, time="13:00", engineer_id="E1")

    choice = build_choice("tl_1", event, before, outcomes, None)

    by_variant = {option.variant: option for option in choice.variants}
    assert [option.variant for option in choice.variants] == ["optimal", "stable", "keep"]
    assert [option.recommended for option in choice.variants] == [True, False, False]
    assert by_variant["optimal"].title == "Оптимально по дню"
    assert by_variant["keep"].summary == "Оставить маршруты как есть"
    assert (by_variant["keep"].late, by_variant["optimal"].moved) == (1, 5)
    assert (choice.metrics_before, choice.late_before, choice.current) == (before.metrics, 0, None)
    # Рекомендованный сравнивается со следующим по ключу («Минимум перестановок»).
    assert by_variant["optimal"].pros == ["на 1 бригаду меньше", "на 20,5 км меньше"]
    assert by_variant["optimal"].cons == ["на 4 заявки больше переезжает к другим бригадам"]
    assert by_variant["stable"].pros == ["на 4 заявки меньше переезжает к другим бригадам"]
    assert by_variant["stable"].cons == ["на 1 бригаду больше", "на 20,5 км больше"]
    assert by_variant["keep"].pros == ["на 5 заявок меньше переезжает к другим бригадам", "на 10,0 км меньше"]
    assert by_variant["keep"].cons == ["на 3 клиента без инженера или с опозданием больше"]


def test_choice_ties_go_to_the_earlier_strategy():
    before = _plan(0, 5, 100.0)
    same = _plan(0, 5, 100.0)
    outcomes = [Outcome(variant, same, _diff(before, same, 0)) for variant in ("keep", "stable", "optimal")]
    choice = build_choice("tl_2", Event(type=EventType.URGENT, time="12:00", request=req("U1", 0, 0, "12:00", "13:00")), before, outcomes, "stable")
    assert [option.variant for option in choice.variants if option.recommended] == ["optimal"]
    assert choice.current == "stable"
    assert all(option.pros == [] and option.cons == [] for option in choice.variants)
```

- [ ] **Step 3: Запустить и убедиться, что падают**

Run: `cd backend && ~/.local/bin/uv run pytest -q -o addopts= tests/test_variants.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.planning.variants'`.

- [ ] **Step 4: Создать `backend/app/planning/variants.py`**

```python
"""Варианты исправления плана на «ломающее» событие дня: стратегии, сравнение итогов и рекомендация.

Спецификация: docs/superpowers/specs/2026-09-17-event-variants-design.md. «Ломающие» события — те, после которых
есть разные способы спасти день: недоступность, смена транспорта, задержка инженера и срочная заявка. Отмена,
возврат и изменение заявки применяются одним планом, как раньше.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.domain.enums import TRANSPORT_RU, EventType, ReasonCode
from app.domain.models import Event, Plan, Unassigned
from app.planning.models import EventChoice, EventVariant, PlanDiff, VariantOption
from app.solvers.assemble import build_plan
from app.solvers.problem import Problem

VARIANTS: tuple[EventVariant, ...] = ("optimal", "stable", "keep")
BREAKING_EVENTS = frozenset(
    {
        EventType.URGENT,
        EventType.ENGINEER_UNAVAILABLE,
        EventType.ENGINEER_TRANSPORT_CHANGED,
        EventType.ENGINEER_DELAYED,
    }
)
# «Минимум перестановок»: условные 500 км за перенос заявки к другому инженеру вместо 20. Снять заявку всё равно
# дороже (drop_normal), поэтому заявки пострадавшей бригады уходят другим, а чужие маршруты почти не трогаются.
STABLE_REASSIGNMENT = 500_000
VARIANT_TITLES: dict[EventVariant, str] = {
    "optimal": "Оптимально по дню",
    "stable": "Минимум перестановок",
    "keep": "Ничего не менять",
}
VARIANT_SUMMARIES: dict[EventVariant, str] = {
    "optimal": "Пересчитать остаток дня целиком",
    "stable": "Чужие маршруты почти не трогаем",
    "keep": "Оставить маршруты как есть",
}
KEEP_TEXT = "Вариант «Ничего не менять»: план не пересчитан, заявку никто не забрал."
MAX_LINES = 3


def is_choosable(event: Event) -> bool:
    return event.type in BREAKING_EVENTS


def late_visits(plan: Plan) -> int:
    """Визиты плана, которые начнутся позже конца окна."""
    return sum(1 for route in plan.routes for visit in route.visits if visit.late_min > 0)


def keep_plan(problem: Problem) -> Plan:
    """«Ничего не менять»: прежние маршруты без решателя на задаче после события.

    Порядок заявок инженера — его несделанная часть плана до события (Problem.previous_order из pin_problem).
    Маршруты прогоняются обычной симуляцией: опоздания и переработки видны в визитах и нарушениях. Заявки
    инженера, которому больше нельзя работать (недоступен или задержан до конца смены), и заявки, которым нужен
    транспорт, которого у инженера теперь нет, остаются без инженера. Новая срочная заявка ни в чей маршрут
    не попадает.
    """
    open_ids = set(problem.open_request_ids)
    sequences: dict[str, list[str]] = {}
    fixed: dict[str, Unassigned] = {}
    for state in problem.states:
        engineer = state.engineer
        kept: list[str] = []
        for request_id in problem.previous_order.get(engineer.id, []):
            if request_id not in open_ids:
                continue
            request = problem.request(request_id)
            if state.available_from >= state.available_until:
                fixed[request_id] = Unassigned(
                    request_id=request_id, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=KEEP_TEXT
                )
            elif request.transport_required is not None and request.transport_required != engineer.transport:
                fixed[request_id] = Unassigned(
                    request_id=request_id,
                    reason_code=ReasonCode.NO_TRANSPORT,
                    reason_text=(
                        f"{KEEP_TEXT} Нужен транспорт «{TRANSPORT_RU[request.transport_required]}», "
                        f"у {engineer.name} «{TRANSPORT_RU[engineer.transport]}»."
                    ),
                )
            else:
                kept.append(request_id)
        sequences[engineer.id] = kept
    placed = {request_id for sequence in sequences.values() for request_id in sequence}
    for request_id in problem.open_request_ids:
        if request_id not in placed and request_id not in fixed:
            fixed[request_id] = Unassigned(
                request_id=request_id, reason_code=ReasonCode.NO_FREE_ENGINEER, reason_text=KEEP_TEXT
            )
    return build_plan(problem, "ortools", sequences, fixed_unassigned=fixed)


@dataclass(frozen=True)
class Outcome:
    """Итог одной стратегии: план после события и разница с планом до него."""

    variant: EventVariant
    plan: Plan
    diff: PlanDiff


def _clients(option: VariantOption) -> int:
    return option.metrics.unassigned + option.late


def _rank(option: VariantOption) -> tuple[int, int, int, float, int]:
    return (
        _clients(option),
        option.metrics.engineers_used,
        option.moved,
        round(option.metrics.total_km, 1),
        VARIANTS.index(option.variant),
    )


def _plural(count: int, one: str, few: str, many: str) -> str:
    mod10, mod100 = count % 10, count % 100
    if mod10 == 1 and mod100 != 11:
        return one
    if 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        return few
    return many


def _counted(delta: int, noun: Callable[[int], str], tail: str = "") -> tuple[int, str, str]:
    count = abs(delta)
    suffix = f" {tail}" if tail else ""
    return delta, f"на {count} {noun(count)} меньше{suffix}", f"на {count} {noun(count)} больше{suffix}"


def _clients_row(delta: int) -> tuple[int, str, str]:
    count = abs(delta)
    noun = _plural(count, "клиента", "клиента", "клиентов")
    return (
        delta,
        f"на {count} {noun} без инженера или с опозданием меньше",
        f"на {count} {noun} без инженера или с опозданием больше",
    )


def _compare(option: VariantOption, reference: VariantOption) -> tuple[list[str], list[str]]:
    """Чем вариант лучше и хуже reference: только ненулевые разницы, в порядке важности."""
    km = round(option.metrics.total_km - reference.metrics.total_km, 1)
    km_text = f"{abs(km):.1f}".replace(".", ",")
    rows = [
        _clients_row(_clients(option) - _clients(reference)),
        _counted(
            option.metrics.engineers_used - reference.metrics.engineers_used,
            lambda n: _plural(n, "бригаду", "бригады", "бригад"),
        ),
        _counted(
            option.moved - reference.moved,
            lambda n: _plural(n, "заявку", "заявки", "заявок"),
            "переезжает к другим бригадам",
        ),
    ]
    pros: list[str] = []
    cons: list[str] = []
    for delta, better, worse in rows:
        if delta < 0:
            pros.append(better)
        elif delta > 0:
            cons.append(worse)
    if km < 0:
        pros.append(f"на {km_text} км меньше")
    elif km > 0:
        cons.append(f"на {km_text} км больше")
    return pros[:MAX_LINES], cons[:MAX_LINES]


def build_choice(
    entry_id: str,
    event: Event,
    before: Plan,
    outcomes: Sequence[Outcome],
    current: EventVariant | None,
) -> EventChoice:
    """Варианты в порядке VARIANTS с рекомендацией и строками «лучше / хуже».

    Остальные варианты сравниваются с рекомендованным, рекомендованный — со следующим по ключу рекомендации.
    """
    options = [
        VariantOption(
            variant=outcome.variant,
            title=VARIANT_TITLES[outcome.variant],
            summary=VARIANT_SUMMARIES[outcome.variant],
            metrics=outcome.plan.metrics,
            late=late_visits(outcome.plan),
            moved=len(outcome.diff.moved),
        )
        for outcome in sorted(outcomes, key=lambda outcome: VARIANTS.index(outcome.variant))
    ]
    ranked = sorted(options, key=_rank)
    best = ranked[0]
    described = []
    for option in options:
        reference = ranked[1] if option is best and len(ranked) > 1 else best
        pros, cons = _compare(option, reference) if reference is not option else ([], [])
        described.append(option.model_copy(update={"pros": pros, "cons": cons, "recommended": option is best}))
    return EventChoice(
        entry_id=entry_id,
        event=event,
        metrics_before=before.metrics,
        late_before=late_visits(before),
        variants=described,
        current=current,
    )
```

- [ ] **Step 5: Стратегии в `backend/app/planning/session.py`**

Импорты (рядом с остальными `from app.planning…`):

```python
from app.planning.models import AppliedEvent, EventVariant, PlanDiff
from app.planning.variants import STABLE_REASSIGNMENT, is_choosable, keep_plan
```

Заменить `_solve`:

```python
def _solve(
    problem: Problem, workload_level: int, time_limit_s: int, variant: EventVariant = "optimal"
) -> tuple[Plan, Plan]:
    """Оптимизированный план с весами уровня нагрузки и базовый FCFS. Стоимость инженера FCFS не использует.

    variant="stable" делает перенос заявки к другому инженеру очень дорогим (вариант «Минимум перестановок»).
    """
    weights = workload_weights(workload_level)
    if variant == "stable":
        weights = replace(weights, reassignment=STABLE_REASSIGNMENT)
    optimizer = OrToolsSolver(time_limit_s=time_limit_s, weights=weights)
    return optimizer.solve(problem), FcfsSolver().solve(problem)
```

В `apply_event`: сигнатура `def apply_event(session, event, ctx, *, version=None, variant: EventVariant = "optimal")`, в docstring добавить абзац «variant — стратегия для «ломающего» события (app/planning/variants.py); у остальных событий не влияет». Строку `plan, baseline = _solve(problem, session.workload_level, ctx.time_limit_s)` заменить на:

```python
    strategy: EventVariant = variant if is_choosable(stored_event) else "optimal"
    if strategy == "keep":
        plan, baseline = keep_plan(problem), FcfsSolver().solve(problem)
    else:
        plan, baseline = _solve(problem, session.workload_level, ctx.time_limit_s, strategy)
```

- [ ] **Step 6: Тестовый солвер принимает стратегию (`backend/tests/timeline_helpers.py`)**

```python
    def __init__(self):
        super().__init__()
        self.gates: dict[str, tuple[threading.Event, threading.Event]] = {}
        # Стратегия каждого решения по порядку: optimal или stable.
        self.variants: list[str] = []

    def solve(self, problem, workload_level, time_limit_s, variant="optimal"):
        time = fmt_hhmm(problem.now)
        self.append(time)
        self.variants.append(variant)
        ...
```

И в `clear` ничего не менять: тесты чистят список времён через `solves.clear()`; `variants` проверяются только там, где их читают.

- [ ] **Step 7: Тесты и линт**

Run: `cd backend && ~/.local/bin/uv run pytest -q -o addopts= tests/test_variants.py tests/test_session.py tests/test_session_delay.py tests/test_session_transport.py tests/test_timeline.py`
Expected: PASS. Если пример в `test_keep_leaves_the_upcoming_visits…` не находит визитов после 13:00 у FCFS-плана тестового дня, подберите время события по реальному плану (`busy_engineer` и старты визитов), не ослабляя проверки.
Run: `~/.local/bin/uv run ruff check app tests && ~/.local/bin/uv run ruff format app tests`

- [ ] **Step 8: Commit**

```bash
git add backend/app/planning/variants.py backend/app/planning/models.py backend/app/planning/session.py backend/tests/timeline_helpers.py backend/tests/test_variants.py
git commit -m "Варианты исправления: стратегии «оптимально», «минимум перестановок», «ничего не менять» и сравнение

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Выбор на шкале (`app/planning/timeline.py`)

**Files:**
- Modify: `backend/app/planning/timeline.py`
- Test: `backend/tests/test_timeline.py`

**Interfaces:**
- Consumes: `VARIANTS`, `is_choosable`, `EventVariant`, `apply_event(..., variant=)`.
- Produces:
  - `TimelineStatus = Literal["applied", "pending", "rejected", "awaiting"]`
  - `TimelineEntry.variant: EventVariant | None = None`
  - `entry_token(entry, variant=None) -> str`
  - `Walk.awaiting: TimelineEntry | None = None`
  - `replay_step(prior, entry, ctx, version, variant: EventVariant | None = None) -> TimelineStep`
  - `Timeline.create(event, geo=None, *, checked=False, variant: EventVariant | None = None)`
  - `Timeline.step(walk, entry, variant) -> TimelineStep | None`
  - `Timeline.store(walk, entry, step, variant: EventVariant | None = None)`
  - `Timeline.set_variant(entry_id, variant) -> TimelineEntry | None`
  - `Timeline.pending_choice(base, cursor) -> tuple[Walk, TimelineEntry] | None`
  - `Timeline.view(base, cursor) -> tuple[list[TimelineView], bool]` — `ready` истинно и при остановке на выборе.

- [ ] **Step 1: Падающие тесты (дописать в `backend/tests/test_timeline.py`)**

```python
from app.planning.timeline import entry_token
from app.planning.variants import VARIANTS


def unavailable(engineer_id, time):
    return Event(type=EventType.ENGINEER_UNAVAILABLE, time=time, engineer_id=engineer_id)


def _variants_of(timeline, base, ctx, entry):
    """Как фоновый расчёт: три шага события без выбора после посчитанного прохода."""
    walk = timeline.walk(base)
    for variant in VARIANTS:
        timeline.store(walk, entry, replay_step(walk.session, entry, ctx, 99, variant), variant)
    return walk


def test_walk_stops_at_a_breaking_event_without_a_choice_until_its_variants_are_known(solves):
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    breaking, later = _added(timeline := Timeline(), unavailable(busy, "13:00"), cancel("R1", "16:00"))

    assert entry_token(breaking) == breaking.id and entry_token(breaking, "keep") == f"{breaking.id}@keep"
    walk = timeline.walk(base)
    assert (walk.done, walk.awaiting) == (0, None)

    _variants_of(timeline, base, ctx, breaking)
    walk = timeline.walk(base)
    assert (walk.done, walk.awaiting) == (0, breaking)
    assert timeline.pending_choice(base, 12 * 60) is None
    pending_walk, entry = timeline.pending_choice(base, 15 * 60)
    assert entry == breaking and pending_walk.session == base
    items, ready = timeline.view(base, 15 * 60)
    assert [item.status for item in items] == ["awaiting", "pending"] and ready is True
    items, _ = timeline.view(base, 12 * 60)
    assert [item.status for item in items] == ["pending", "pending"]


def test_chosen_variant_is_applied_and_changing_it_replays_later_events_with_their_choices(solves):
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    timeline = Timeline()
    breaking, later = _added(timeline, unavailable(busy, "13:00"), cancel("R1", "16:00"))
    _variants_of(timeline, base, ctx, breaking)

    chosen = timeline.set_variant(breaking.id, "keep")
    assert chosen.variant == "keep" and timeline.find(breaking.id) is chosen
    walk = replay_all(timeline, base, ctx)
    assert walk.awaiting is None and walk.done == 2
    assert walk.keys[0] == ((), f"{breaking.id}@keep")
    assert walk.keys[1] == ((f"{breaking.id}@keep",), later.id)
    kept_plan = walk.steps[0].session.plan

    timeline.set_variant(breaking.id, "optimal")
    walk = replay_all(timeline, base, ctx)
    assert walk.keys[1] == ((f"{breaking.id}@optimal",), later.id)
    assert walk.steps[0].session.plan != kept_plan
    assert timeline.set_variant("tl_404", "keep") is None


def test_rejected_breaking_event_needs_no_choice(solves):
    ctx = context()
    base = new_session(ctx)
    timeline = Timeline()
    (ghost,) = _added(timeline, unavailable("E404", "13:00"))
    walk = timeline.walk(base)
    timeline.store(walk, ghost, replay_step(walk.session, ghost, ctx, 2, "optimal"), "optimal")
    walk = timeline.walk(base)
    assert walk.awaiting is None and walk.done == 1 and walk.steps[0].reason is not None
    items, ready = timeline.view(base, 15 * 60)
    assert [item.status for item in items] == ["rejected"] and ready is True


def test_prune_keeps_the_other_variants_of_chosen_events_and_remove_drops_them(solves):
    ctx = context()
    base = new_session(ctx)
    busy = busy_engineer(base.plan)
    timeline = Timeline()
    (breaking,) = _added(timeline, unavailable(busy, "13:00"))
    _variants_of(timeline, base, ctx, breaking)
    timeline.set_variant(breaking.id, "stable")
    walk = replay_all(timeline, base, ctx)

    timeline.prune(walk)
    assert {key[1] for key in timeline.steps} == {f"{breaking.id}@{variant}" for variant in VARIANTS}
    timeline.remove(breaking.id)
    assert timeline.steps == {}


def test_entry_can_be_created_with_a_variant():
    entry = Timeline().create(unavailable("E1", "13:00"), variant="optimal")
    assert entry.variant == "optimal" and entry_token(entry) == "tl_1@optimal"
```

(`replay_step`, `busy_engineer`, `EventType`, `Event` импортировать в начале файла, если их ещё нет.)

- [ ] **Step 2: Запустить — падает**

Run: `cd backend && ~/.local/bin/uv run pytest -q -o addopts= tests/test_timeline.py`
Expected: FAIL — `ImportError: cannot import name 'entry_token'`.

- [ ] **Step 3: Реализация в `backend/app/planning/timeline.py`**

Импорты: `from dataclasses import dataclass, field, replace`; `from app.planning.models import AppliedEvent, EventVariant`; `from app.planning.variants import VARIANTS, is_choosable`.

Статус и событие:

```python
TimelineStatus = Literal["applied", "pending", "rejected", "awaiting"]
```

В `TimelineEntry` после `checked` добавить поле и дополнить docstring («variant — выбранная стратегия «ломающего» события, None — не выбрана или событие не «ломающее»»):

```python
    variant: EventVariant | None = None
```

Ключ шага:

```python
def entry_token(entry: TimelineEntry, variant: EventVariant | None = None) -> str:
    """Событие в ключе кэша шагов: у события со стратегией к номеру добавляется стратегия («tl_3@keep»)."""
    chosen = variant or entry.variant
    return f"{entry.id}@{chosen}" if chosen is not None else entry.id


def _token_entry(token: str) -> str:
    return token.split("@", 1)[0]
```

`Walk` получает поле (последним, со значением по умолчанию) и строку в docstring «awaiting — «ломающее» событие без выбора, на котором проход остановился; его варианты посчитаны»:

```python
    awaiting: TimelineEntry | None = None
```

`replay_step`:

```python
def replay_step(
    prior: PlanningSession,
    entry: TimelineEntry,
    ctx: PlanningContext,
    version: int,
    variant: EventVariant | None = None,
) -> TimelineStep:
    """Применяет событие к плану prior со стратегией variant (по умолчанию выбранной в событии, иначе optimal).

    Отклонённое событие оставляет план прежним и сохраняет причину.
    """
    event, replay_ctx = entry.replay_input(ctx)
    try:
        session = apply_event(prior, event, replay_ctx, version=version, variant=variant or entry.variant or "optimal")
    except EventRejected as error:
        return TimelineStep(session=prior, reason=str(error))
    return TimelineStep(session=session, applied=session.events[-1])
```

В `Timeline`:

```python
    def create(
        self,
        event: Event,
        geo: Mapping[str, GeoResult] | None = None,
        *,
        checked: bool = False,
        variant: EventVariant | None = None,
    ) -> TimelineEntry:
        """Новое событие со следующим номером; в таймлайн его добавляет insert. variant — стратегия сразу."""
        self.last_number += 1
        sent = event.model_copy(update={"previous_transport": None, "previous_request": None})
        return TimelineEntry(
            id=f"tl_{self.last_number}",
            seq=self.last_number,
            event=sent,
            geo=dict(geo or {}),
            checked=checked,
            variant=variant,
        )

    def remove(self, entry_id: str) -> TimelineEntry | None:
        """Убирает событие и шаги, в которых оно участвовало. Возвращает событие или None, если его нет."""
        entry = self.find(entry_id)
        if entry is None:
            return None
        self.entries.remove(entry)
        self.steps = {
            key: step
            for key, step in self.steps.items()
            if _token_entry(key[1]) != entry_id and entry_id not in {_token_entry(token) for token in key[0]}
        }
        self.revision += 1
        return entry

    def set_variant(self, entry_id: str, variant: EventVariant) -> TimelineEntry | None:
        """Выбор или смена стратегии события. Шаги с другой стратегией остаются в кэше под своими ключами."""
        entry = self.find(entry_id)
        if entry is None:
            return None
        chosen = replace(entry, variant=variant)
        self.entries[self.entries.index(entry)] = chosen
        self.revision += 1
        return chosen

    def walk(self, base: PlanningSession, count: int | None = None) -> Walk:
        """Проходит первые count событий (все, если count не задан) по кэшу, пока шаги посчитаны.

        «Ломающее» событие без выбора останавливает проход: если его шаги посчитаны для всех стратегий, оно
        становится awaiting. Событие, отклонённое при optimal, выбора не требует и проходится как отклонённое.
        """
        limit = len(self.entries) if count is None else min(count, len(self.entries))
        session, prefix = base, ()
        steps: list[TimelineStep] = []
        keys: list[StepKey] = []
        awaiting: TimelineEntry | None = None
        for entry in self.entries[:limit]:
            if is_choosable(entry.event) and entry.variant is None:
                probe_key = (prefix, entry_token(entry, "optimal"))
                probe = self.steps.get(probe_key)
                if probe is not None and probe.reason is not None:
                    steps.append(probe)
                    keys.append(probe_key)
                    continue
                if probe is not None and all(
                    (prefix, entry_token(entry, variant)) in self.steps for variant in VARIANTS
                ):
                    awaiting = entry
                break
            key = (prefix, entry_token(entry))
            step = self.steps.get(key)
            if step is None:
                break
            steps.append(step)
            keys.append(key)
            session = step.session
            if step.applied is not None:
                prefix = (*prefix, key[1])
        return Walk(session=session, prefix=prefix, steps=steps, keys=keys, awaiting=awaiting)

    def step(self, walk: Walk, entry: TimelineEntry, variant: EventVariant) -> TimelineStep | None:
        """Шаг события entry со стратегией variant после прохода walk, если он посчитан."""
        return self.steps.get((walk.prefix, entry_token(entry, variant)))

    def store(
        self, walk: Walk, entry: TimelineEntry, step: TimelineStep, variant: EventVariant | None = None
    ) -> None:
        """Сохраняет шаг события entry, следующего за проходом walk, со стратегией variant или выбранной."""
        self.steps[(walk.prefix, entry_token(entry, variant))] = step

    def prune(self, walk: Walk) -> None:
        """Оставляет в кэше шаги полного прохода и другие стратегии его событий: выбор можно поменять без пересчёта."""
        if walk.done != len(self.entries):
            return
        keep = set(walk.keys)
        for prefix, token in walk.keys:
            if "@" in token:
                keep.update((prefix, f"{_token_entry(token)}@{variant}") for variant in VARIANTS)
        self.steps = {key: step for key, step in self.steps.items() if key in keep}

    def pending_choice(self, base: PlanningSession, cursor: int) -> tuple[Walk, TimelineEntry] | None:
        """Событие, на котором стоит текущее время и которое ждёт выбора, и проход до него."""
        walk = self.walk(base, self.applied_count(cursor))
        if walk.awaiting is None or walk.awaiting.event.time > cursor:
            return None
        return walk, walk.awaiting

    def view(self, base: PlanningSession, cursor: int) -> tuple[list[TimelineView], bool]:
        """События для ответа API и признак, что считать больше нечего.

        Отклонённое событие видно сразу, как только посчитан его шаг, даже если оно позже текущего времени.
        Принятое событие не позже cursor применено; «ломающее» событие без выбора, до которого дошло время,
        ждёт выбора; остальные ждут своего времени или пересчёта. Остановка на выборе считается готовностью:
        дальше считать нельзя, пока диспетчер не выберет.
        """
        walk = self.walk(base)
        items: list[TimelineView] = []
        for k, entry in enumerate(self.entries):
            step = walk.steps[k] if k < walk.done else None
            if step is not None and step.reason is not None:
                items.append(TimelineView(entry, entry.event, "rejected", step.reason))
            elif step is not None and step.applied is not None and entry.event.time <= cursor:
                items.append(TimelineView(entry, step.applied.event, "applied"))
            elif entry is walk.awaiting and entry.event.time <= cursor:
                items.append(TimelineView(entry, entry.event, "awaiting"))
            else:
                items.append(TimelineView(entry, entry.event, "pending"))
        return items, walk.done == len(self.entries) or walk.awaiting is not None
```

Модуль-docstring дополнить абзацем: «Стратегия «ломающего» события (app/planning/variants.py) входит в ключ шага: у события с выбором номер в ключе вида tl_3@keep. Событие без выбора останавливает проход, пока диспетчер не выберет».

`replay_all` в `tests/timeline_helpers.py`: остановиться на выборе — после `walk = timeline.walk(base)` добавить `if walk.awaiting is not None: return walk`.

- [ ] **Step 4: Тесты**

Run: `cd backend && ~/.local/bin/uv run pytest -q -o addopts= tests/test_timeline.py tests/test_variants.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/planning/timeline.py backend/tests/test_timeline.py backend/tests/timeline_helpers.py
git commit -m "Шкала: стратегия события в ключе шага, проход останавливается на событии без выбора

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: API — остановка времени, варианты и выбор

**Files:**
- Modify: `backend/app/api/timeline.py`, `backend/app/api/routes.py`, `backend/app/api/proposals.py`, `backend/app/api/schemas.py`
- Modify: `docs/superpowers/specs/2026-09-15-api-contract.md` (пункт 25)
- Test: `backend/tests/test_timeline_api.py`

**Interfaces:**
- Consumes: всё из Task 1 и Task 2.
- Produces:
  - `schemas.TimelineItem.variant: EventVariant | None = None`, `schemas.TimelineItem.choosable: bool = False`
  - `schemas.PlanningState.pending_choice: EventChoice | None = None`
  - `schemas.VariantRequest(variant: EventVariant)`
  - `app.api.timeline.insert_and_replay(record, ctx, entry) -> TimelineStep | None` (None — событие ждёт выбора или выбора ждёт событие раньше)
  - `app.api.timeline.VariantUnavailable(status: int, text: str)`
  - `app.api.timeline.event_choice(record, ctx, entry_id) -> EventChoice`
  - `GET /api/datasets/{id}/timeline/events/{entry_id}/variants` → `EventChoice`
  - `PUT /api/datasets/{id}/timeline/events/{entry_id}/variant` → `PlanningState`

- [ ] **Step 1: Падающие тесты (дописать в `backend/tests/test_timeline_api.py`)**

```python
from app.domain.enums import EventType
from app.domain.models import Event


def unavailable(engineer_id, time):
    return Event(type=EventType.ENGINEER_UNAVAILABLE, time=time, engineer_id=engineer_id)


def busy_of(client, base):
    return next(route["engineer_id"] for route in state_of(client, base)["plan"]["routes"] if route["visits"])


def cursor_to(client, base, time):
    response = client.post(f"{base}/cursor", json={"time": time})
    assert response.status_code == 200, response.text
    return response.json()


def test_time_stops_at_a_breaking_event_until_a_variant_is_chosen(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    busy = busy_of(client, base)
    added(client, base, unavailable(busy, "13:00"))
    added(client, base, cancel("R1", "16:00"))
    background.run()
    ahead = state_of(client, base)
    assert ahead["timeline_ready"] is True and ahead["pending_choice"] is None
    assert [(item["status"], item["choosable"], item["variant"]) for item in ahead["timeline"]] == [
        ("pending", True, None),
        ("pending", False, None),
    ]

    stopped = cursor_to(client, base, "17:00")
    assert stopped["cursor"] == "13:00"
    assert [item["status"] for item in stopped["timeline"]] == ["awaiting", "pending"]
    choice = stopped["pending_choice"]
    assert choice["entry_id"] == "tl_1" and choice["current"] is None
    assert [option["variant"] for option in choice["variants"]] == ["optimal", "stable", "keep"]
    assert [option["title"] for option in choice["variants"]] == ["Оптимально по дню", "Минимум перестановок", "Ничего не менять"]
    assert sum(option["recommended"] for option in choice["variants"]) == 1
    assert stopped["plan"] == ahead["plan"]

    chosen = client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "keep"})
    assert chosen.status_code == 200, chosen.text
    chosen = chosen.json()
    assert (chosen["cursor"], chosen["pending_choice"]) == ("13:00", None)
    assert [(item["status"], item["variant"]) for item in chosen["timeline"]] == [("applied", "keep"), ("pending", None)]
    assert {item["request_id"] for item in chosen["plan"]["unassigned"]} >= {
        visit["request_id"]
        for route in ahead["plan"]["routes"] if route["engineer_id"] == busy
        for visit in route["visits"] if visit["start"] >= "13:00"
    }

    later = cursor_to(client, base, "17:00")
    assert [item["status"] for item in later["timeline"]] == ["applied", "applied"]

    changed = client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "optimal"}).json()
    assert (changed["cursor"], changed["timeline"][0]["variant"]) == ("17:00", "optimal")
    assert changed["plan"] != later["plan"]
    again = client.get(f"{base}/timeline/events/tl_1/variants")
    assert again.status_code == 200 and again.json()["current"] == "optimal"


def test_breaking_event_at_the_cursor_asks_for_a_variant_at_once(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    busy = busy_of(client, base)
    cursor_to(client, base, "12:00")
    state = added(client, base, unavailable(busy, "12:00"))
    assert state["cursor"] == "12:00"
    assert state["timeline"][0]["status"] == "awaiting"
    assert state["pending_choice"]["entry_id"] == "tl_1"


def test_variant_errors(tmp_path, solves):
    client, _, base, background = dataset(tmp_path, solves)
    added(client, base, cancel("R1", "16:00"))
    background.run()
    assert client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "keep"}).status_code == 409
    assert client.get(f"{base}/timeline/events/tl_1/variants").status_code == 409
    assert client.put(f"{base}/timeline/events/tl_9/variant", json={"variant": "keep"}).status_code == 404
    assert client.get(f"{base}/timeline/events/tl_9/variants").status_code == 404
    assert client.put(f"{base}/timeline/events/tl_1/variant", json={"variant": "best"}).status_code == 422


def test_legacy_events_and_proposals_apply_the_optimal_variant_without_asking(tmp_path, solves):
    client, _, base, _ = dataset(tmp_path, solves)
    busy = busy_of(client, base)
    response = client.post(f"{base}/events", json=body(unavailable(busy, "13:00")))
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["pending_choice"] is None
    assert [(item["status"], item["variant"]) for item in state["timeline"]] == [("applied", "optimal")]
```

- [ ] **Step 2: Запустить — падают**

Run: `cd backend && ~/.local/bin/uv run pytest -q -o addopts= tests/test_timeline_api.py`
Expected: FAIL (`KeyError: 'pending_choice'` / 405 на PUT).

- [ ] **Step 3: Схемы (`backend/app/api/schemas.py`)**

Импорт: `from app.planning.models import AppliedEvent, EventChoice, EventVariant, PlanDiff`.

```python
class TimelineItem(BaseModel):
    """Событие на шкале: применённое событие, если оно применено, иначе запланированное."""

    id: str
    event: Event
    status: TimelineStatus
    reason: str | None = None
    # Стратегия «ломающего» события; null — не выбрана или событие не «ломающее».
    variant: EventVariant | None = None
    # «Ломающее» событие: для него сервер предлагает варианты исправления.
    choosable: bool = False


class VariantRequest(BaseModel):
    """Тело PUT …/timeline/events/{id}/variant."""

    variant: EventVariant
```

В `PlanningState` после `timeline_ready`:

```python
    # «Ломающее» событие, на котором остановилось текущее время, и его варианты; null — выбирать нечего.
    pending_choice: EventChoice | None = None
```

В `to_planning_state` добавить параметр `pending_choice: EventChoice | None = None` и передать его в `PlanningState(...)`.

- [ ] **Step 4: `backend/app/api/timeline.py`**

Импорты: `from app.domain.timeutil import fmt_hhmm`; `from app.planning.models import EventChoice`; `from app.planning.timeline import TimelineEntry, TimelineStep, Walk, replay_step`; `from app.planning.variants import VARIANTS, Outcome, build_choice, is_choosable`.

```python
NOT_CHOOSABLE_TEXT = "Для этого события варианты не предлагаются."


class VariantUnavailable(Exception):
    """Варианты события получить нельзя: status — код ответа API, текст — для диспетчера."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status = status


def _choice(record: DatasetRecord, walk: Walk, entry: TimelineEntry) -> EventChoice:
    """Под record.lock: варианты события из посчитанных шагов его стратегий."""
    outcomes = []
    for variant in VARIANTS:
        step = record.timeline.step(walk, entry, variant)
        if step is not None and step.applied is not None and step.session.last_diff is not None:
            outcomes.append(Outcome(variant, step.session.plan, step.session.last_diff))
    return build_choice(entry.id, entry.event, walk.session.plan, outcomes, entry.variant)


def planning_state(record: DatasetRecord) -> PlanningState:
    """Состояние на текущее время плана с событиями таймлайна. Вызывать, когда план дня уже есть."""
    with record.lock:
        views, ready = record.timeline.view(record.base, record.cursor)
        timeline = [
            TimelineItem(
                id=view.entry.id,
                event=view.event,
                status=view.status,
                reason=view.reason,
                variant=view.entry.variant,
                choosable=is_choosable(view.entry.event),
            )
            for view in views
        ]
        pending = record.timeline.pending_choice(record.base, record.cursor)
        return to_planning_state(
            record.session,
            cursor=record.cursor,
            timeline=timeline,
            timeline_ready=ready,
            pending_choice=_choice(record, *pending) if pending is not None else None,
        )


def _replay_variants(record: DatasetRecord, ctx: PlanningContext, walk: Walk, entry: TimelineEntry) -> None:
    """Под record.timeline_lock: шаги события entry для всех стратегий после прохода walk, без record.lock.

    Посчитанные стратегии не пересчитываются. Отклонение не зависит от стратегии: после отклонённого optimal
    остальные не считаются. Все стратегии получают один номер плана: в цепочку попадёт только выбранная.
    """
    version = record.next_version()
    computed: dict[str, TimelineStep] = {}
    for variant in VARIANTS:
        with record.lock:
            known = record.timeline.step(walk, entry, variant)
        step = known or replay_step(walk.session, entry, ctx, version, variant)
        computed[variant] = step
        if step.reason is not None:
            break
    with record.lock:
        for variant, step in computed.items():
            record.timeline.store(walk, entry, step, variant)
        if any(step.applied is not None for step in computed.values()):
            record.use_version(version)


def compute_steps(record: DatasetRecord, ctx: PlanningContext, count: int) -> Walk:
    """Под record.timeline_lock: досчитывает шаги первых count событий по порядку и возвращает проход по ним.

    На «ломающем» событии без выбора считаются все его стратегии, и проход останавливается на нём.
    """
    while True:
        with record.lock:
            walk = record.timeline.walk(record.base, count)
            if walk.awaiting is not None or walk.done >= min(count, len(record.timeline.entries)):
                return walk
            entry = record.timeline.entries[walk.done]
        if is_choosable(entry.event) and entry.variant is None:
            _replay_variants(record, ctx, walk, entry)
        else:
            _replay_next(record, ctx, walk, entry)


def move_cached(record: DatasetRecord, cursor: int) -> bool:
    """Под record.lock: переносит текущее время, если шаги до него посчитаны, и ставит план на это время.

    Время не проходит «ломающее» событие без выбора: оно встаёт на время события, план — план до него.
    """
    count = record.timeline.applied_count(cursor)
    walk = record.timeline.walk(record.base, count)
    if walk.awaiting is not None:
        record.cursor = walk.awaiting.event.time
        record.session = walk.session
        return True
    if walk.done < count:
        return False
    record.cursor = cursor
    record.session = walk.session
    return True


def insert_and_replay(record: DatasetRecord, ctx: PlanningContext, entry: TimelineEntry) -> TimelineStep | None:
    """Под record.timeline_lock: ставит событие на шкалу и считает его шаг после предыдущих событий.

    Отклонённое событие убирается со шкалы, а шаги событий перед ним остаются в кэше. Текущее время не меняется.
    None — шага нет: событие ждёт выбора варианта или выбора ждёт событие раньше него.
    """
    with record.lock:
        position = record.timeline.insert(entry)
    walk = compute_steps(record, ctx, position + 1)
    step = walk.steps[position] if walk.done > position else None
    if step is not None and step.reason is not None:
        with record.lock:
            record.timeline.remove(entry.id)
    return step


def event_choice(record: DatasetRecord, ctx: PlanningContext, entry_id: str) -> EventChoice:
    """Варианты события шкалы для окна выбора: считает недостающие стратегии. Бросает VariantUnavailable."""
    with record.timeline_lock:
        with record.lock:
            entry = record.timeline.find(entry_id)
            if entry is None:
                raise VariantUnavailable(404, f"Событие {entry_id} не найдено.")
            if not is_choosable(entry.event):
                raise VariantUnavailable(409, NOT_CHOOSABLE_TEXT)
            position = record.timeline.entries.index(entry)
        walk = compute_steps(record, ctx, position)
        if walk.done < position:
            earlier = walk.awaiting.event.time if walk.awaiting is not None else entry.event.time
            raise VariantUnavailable(409, f"Сначала выберите вариант для события в {fmt_hhmm(earlier)}.")
        _replay_variants(record, ctx, walk, entry)
        with record.lock:
            optimal = record.timeline.step(walk, entry, "optimal")
            if optimal is not None and optimal.reason is not None:
                raise VariantUnavailable(409, f"Событие отклонено: {optimal.reason}")
            return _choice(record, walk, entry)
```

В `ensure_precompute` после блока `if walk.done == len(...): prune; return` добавить:

```python
        if walk.awaiting is not None:
            return
```

В `precompute` заменить тело цикла внутри `with record.timeline_lock:`:

```python
            with record.lock:
                if record.timeline.revision != revision:
                    return
                walk = record.timeline.walk(record.base)
                if walk.done == len(record.timeline.entries):
                    record.timeline.prune(walk)
                    return
                if walk.awaiting is not None:
                    return
                entry = record.timeline.entries[walk.done]
            if is_choosable(entry.event) and entry.variant is None:
                _replay_variants(record, ctx, walk, entry)
            else:
                _replay_next(record, ctx, walk, entry)
```

- [ ] **Step 5: Маршруты (`backend/app/api/routes.py`)**

Импорты: `VariantRequest` из схем; `VariantUnavailable, event_choice` из `app.api.timeline`; `EventChoice` из `app.planning.models`; `is_choosable` из `app.planning.variants`.

`post_event`: создавать событие со стратегией и обрабатывать отсутствие шага:

```python
                entry = record.timeline.create(
                    event, geo, variant="optimal" if is_choosable(event) else None
                )
            step = insert_and_replay(record, ctx, entry)
            if step is None:
                with record.lock:
                    record.timeline.remove(entry.id)
                    pending = record.timeline.pending_choice(record.base, record.cursor)
                when = fmt_hhmm(pending[1].event.time) if pending is not None else fmt_hhmm(cursor)
                raise HTTPException(status_code=409, detail=f"Сначала выберите вариант для события в {when}.")
            if step.reason is not None:
                raise HTTPException(status_code=422, detail=step.reason)
```

(`fmt_hhmm` импортировать из `app.domain.timeutil`.)

`add_timeline_event`: `if step is not None and step.reason is not None: raise HTTPException(422, step.reason)`.

Новые эндпоинты после `delete_timeline_event`:

```python
@router.get("/datasets/{dataset_id}/timeline/events/{entry_id}/variants", response_model=EventChoice)
def get_timeline_variants(dataset_id: str, entry_id: str, deps: Deps) -> EventChoice:
    """Варианты исправления для «ломающего» события шкалы: для окна выбора и смены выбора."""
    record = _record(deps, dataset_id)
    with record.lock:
        _session(record)
    try:
        return event_choice(record, deps.ingest.planning, entry_id)
    except VariantUnavailable as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error


@router.put("/datasets/{dataset_id}/timeline/events/{entry_id}/variant", response_model=PlanningState)
def put_timeline_variant(dataset_id: str, entry_id: str, body: VariantRequest, deps: Deps) -> PlanningState:
    """Выбор или смена стратегии события. План на текущее время пересчитывается с этого события."""
    record = _record(deps, dataset_id)
    ctx = deps.ingest.planning
    try:
        with record.timeline_lock:
            with record.lock:
                _session(record)
                entry = record.timeline.find(entry_id)
                if entry is None:
                    raise HTTPException(status_code=404, detail=f"Событие {entry_id} не найдено.")
                if not is_choosable(entry.event):
                    raise HTTPException(status_code=409, detail="Для этого события варианты не предлагаются.")
                record.timeline.set_variant(entry_id, body.variant)
            settle(record, ctx)
            return planning_state(record)
    finally:
        ensure_precompute(record, ctx, deps.run_background)
```

- [ ] **Step 6: Предложения помощника (`backend/app/api/proposals.py`)**

```python
        entry = record.timeline.create(
            event, checked=True, variant="optimal" if is_choosable(event) else None
        )
    step = insert_and_replay(record, ctx, entry)
    if step is None:
        with record.lock:
            record.timeline.remove(entry.id)
        result = proposal.model_copy(
            update={"status": "failed", "event": event, "error": "Сначала выберите вариант для события на шкале."}
        )
    elif step.applied is None:
        result = proposal.model_copy(update={"status": "failed", "event": event, "error": step.reason})
    else:
        ...  # как было
```

(`is_choosable` импортировать из `app.planning.variants`.)

- [ ] **Step 7: Контракт API (`docs/superpowers/specs/2026-09-15-api-contract.md`, пункт 25)**

Добавить пункт:

```markdown
25. Варианты исправления при событии (docs/superpowers/specs/2026-09-17-event-variants-design.md): у `TimelineItem` поля `variant` (`"optimal" | "stable" | "keep" | null`) и `choosable`; `status` может быть `"awaiting"` — «ломающее» событие без выбора, на котором остановилось текущее время. `PlanningState.pending_choice` — `EventChoice {entry_id, event, metrics_before, late_before, variants: VariantOption[3], current}` или `null`; `VariantOption {variant, title, summary, metrics, late, moved, pros, cons, recommended}`. `POST /cursor` и `POST /timeline/events` не проходят «ломающее» событие без выбора: `cursor` встаёт на его время. `GET /timeline/events/{id}/variants` → `EventChoice` (404 нет события, 409 не «ломающее», отклонено или выбора ждёт событие раньше). `PUT /timeline/events/{id}/variant` с `{"variant": …}` → `PlanningState` (404, 409 не «ломающее», 422 неизвестная стратегия). `POST /events` и одобрение предложений применяют `optimal` сразу.
```

- [ ] **Step 8: Тесты и линт**

Run: `cd backend && ~/.local/bin/uv run pytest -q -o addopts= tests/test_timeline_api.py tests/test_proposals_api.py tests/test_timeline.py tests/test_variants.py`
Expected: PASS.
Run: `~/.local/bin/uv run ruff check app tests scripts && ~/.local/bin/uv run ruff format app tests scripts`
Run (полный набор, ~5 мин): `~/.local/bin/uv run pytest -q -o addopts=`
Expected: всё зелёное.

- [ ] **Step 9: Commit**

```bash
git add backend/app/api backend/tests/test_timeline_api.py docs/superpowers/specs/2026-09-15-api-contract.md
git commit -m "API: время останавливается на событии без выбора, варианты и выбор стратегии

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Фронтенд — типы, клиент и стор

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`
- Create: `frontend/src/lib/variants.ts`
- Modify: `frontend/src/lib/timeBar.ts`
- Modify: `frontend/src/store/useAppStore.ts`
- Modify: `frontend/src/test/fixtures.ts` (`makeEventChoice`)
- Test: `frontend/src/store/useAppStore.test.ts`, `frontend/src/lib/timeBar.test.ts`

**Interfaces:**
- Produces:
  - TS: `EventVariant = 'optimal' | 'stable' | 'keep'`; `VariantOption`; `EventChoice`; `TimelineStatus` с `'awaiting'`; `TimelineItem.variant: EventVariant | null`, `TimelineItem.choosable: boolean`; `PlanningState.pending_choice?: EventChoice | null`.
  - client: `getTimelineVariants(datasetId, entryId): Promise<EventChoice>`, `setTimelineVariant(datasetId, entryId, variant): Promise<PlanningState>`.
  - `lib/variants.ts`: `CHOOSABLE_EVENTS: ReadonlySet<EventType>`, `VARIANT_TITLES: Record<EventVariant, string>`.
  - store data: `choice: EventChoice | null`, `choiceLoading: boolean`, `resumeAfterChoice: { time: HHMM; play: boolean } | null`, `dismissedChoice: string | null`.
  - store actions: `chooseVariant(variant: EventVariant): Promise<boolean>`, `openChoice(entryId: string): Promise<void>`, `closeChoice(): void`.

- [ ] **Step 1: Типы (`frontend/src/api/types.ts`)**

```ts
/** Стратегия исправления плана на «ломающее» событие. */
export type EventVariant = 'optimal' | 'stable' | 'keep';

/** Вариант исправления: итоги и отличия от рекомендованного. */
export interface VariantOption {
  variant: EventVariant;
  title: string;
  summary: string;
  metrics: Metrics;
  /** Визиты, которые начнутся позже конца окна. */
  late: number;
  /** Заявки, переехавшие к другой бригаде относительно плана до события. */
  moved: number;
  pros: string[];
  cons: string[];
  recommended: boolean;
}

/** Варианты исправления для «ломающего» события шкалы. */
export interface EventChoice {
  entry_id: string;
  event: PlanEvent;
  metrics_before: Metrics;
  late_before: number;
  variants: VariantOption[];
  current: EventVariant | null;
}
```

`TimelineStatus`: `'applied' | 'pending' | 'rejected' | 'awaiting'` (комментарий: «awaiting — «ломающее» событие без выбора, на котором остановилось время»). В `TimelineItem`: `variant: EventVariant | null; choosable: boolean;`. В `PlanningState`: `/** «Ломающее» событие, на котором стоит время, и его варианты; null — выбирать нечего. */ pending_choice?: EventChoice | null;`.

- [ ] **Step 2: Клиент (`frontend/src/api/client.ts`)**

```ts
const putJson = (body: unknown): RequestInit => ({ ...postJson(body), method: 'PUT' });

/** Варианты исправления для «ломающего» события шкалы. */
export const getTimelineVariants = (datasetId: string, entryId: string) =>
  request<EventChoice>(`${dataset(datasetId)}/timeline/events/${encodeURIComponent(entryId)}/variants`);

/** Выбрать или поменять стратегию события: план пересчитывается с этого события. */
export const setTimelineVariant = (datasetId: string, entryId: string, variant: EventVariant) =>
  request<PlanningState>(`${dataset(datasetId)}/timeline/events/${encodeURIComponent(entryId)}/variant`, putJson({ variant }));
```

(`putJson` объявить после `postJson`; типы импортировать.)

- [ ] **Step 3: `frontend/src/lib/variants.ts`**

```ts
import type { EventType, EventVariant } from '../api/types';

/** «Ломающие» события: для них сервер предлагает варианты исправления. */
export const CHOOSABLE_EVENTS: ReadonlySet<EventType> = new Set<EventType>([
  'urgent',
  'engineer_unavailable',
  'engineer_transport_changed',
  'engineer_delayed',
]);

export const VARIANT_TITLES: Record<EventVariant, string> = {
  optimal: 'Оптимально по дню',
  stable: 'Минимум перестановок',
  keep: 'Ничего не менять',
};
```

- [ ] **Step 4: Статусы шкалы (`frontend/src/lib/timeBar.ts`)**

```ts
export function timelineStatusText(item: TimelineItem): string {
  if (item.status === 'applied') return 'применено';
  if (item.status === 'pending') return 'впереди';
  if (item.status === 'awaiting') return 'ждёт выбора варианта';
  return item.reason ? `отклонено: ${item.reason}` : 'отклонено';
}

const STATUS_WEIGHT: Record<TimelineStatus, number> = { applied: 0, pending: 1, rejected: 2, awaiting: 3 };
```

Тест в `frontend/src/lib/timeBar.test.ts`:

```ts
it('names an event that waits for a variant and shows it on its pin first', () => {
  const awaiting = makeTimelineItem({ id: 'tl_9', status: 'awaiting', choosable: true, variant: null });
  expect(timelineStatusText(awaiting)).toBe('ждёт выбора варианта');
  const pins = timelinePins([makeTimelineItem({ id: 'tl_8', event: { ...awaiting.event } , status: 'rejected', reason: 'нет' }), awaiting], { min: 540, max: 1380 });
  expect(pins[0].status).toBe('awaiting');
});
```

Фикстура `makeTimelineItem` получает `variant: null, choosable: false` по умолчанию. Добавить в `frontend/src/test/fixtures.ts`:

```ts
export function makeEventChoice(overrides: Partial<EventChoice> = {}): EventChoice {
  const metrics = makePlanningState().plan.metrics;
  const option = (variant: EventVariant, patch: Partial<VariantOption> = {}): VariantOption => ({
    variant,
    title: { optimal: 'Оптимально по дню', stable: 'Минимум перестановок', keep: 'Ничего не менять' }[variant],
    summary: { optimal: 'Пересчитать остаток дня целиком', stable: 'Чужие маршруты почти не трогаем', keep: 'Оставить маршруты как есть' }[variant],
    metrics,
    late: 0,
    moved: 0,
    pros: [],
    cons: [],
    recommended: false,
    ...patch,
  });
  return {
    entry_id: 'tl_2',
    event: { type: 'engineer_unavailable', time: '13:00', request: null, request_id: null, engineer_id: 'E02' },
    metrics_before: metrics,
    late_before: 0,
    variants: [
      option('optimal', { recommended: true, moved: 3, pros: ['на 1 бригаду меньше'], cons: ['на 3 заявки больше переезжает к другим бригадам'] }),
      option('stable', { moved: 0, pros: ['на 3 заявки меньше переезжает к другим бригадам'], cons: ['на 1 бригаду больше'] }),
      option('keep', { late: 2, metrics: { ...metrics, unassigned: metrics.unassigned + 2 }, cons: ['на 4 клиента без инженера или с опозданием больше'] }),
    ],
    current: null,
    ...overrides,
  };
}
```

- [ ] **Step 5: Падающие тесты стора (дописать в `frontend/src/store/useAppStore.test.ts`)**

В `vi.mock` добавить `getTimelineVariants: vi.fn(), setTimelineVariant: vi.fn()`; импортировать `makeEventChoice`.

```ts
describe('choice of a variant for an event that breaks the plan', () => {
  const awaitingState = (cursor: string) =>
    at(cursor, { pending_choice: makeEventChoice(), timeline: [makeTimelineItem({ id: 'tl_2', status: 'awaiting', choosable: true, variant: null, event: makeEventChoice().event })] });

  it('pauses the playing clock at the event, opens the choice and plays on after the choice', async () => {
    vi.useFakeTimers();
    useAppStore.getState().setPlanningState(at('12:58'));
    vi.mocked(api.moveCursor).mockResolvedValue(awaitingState('13:00'));
    useAppStore.setState({ clock: '12:58' });
    useAppStore.setState({ state: { ...useAppStore.getState().state!, timeline: awaitingState('13:00').timeline } });
    useAppStore.getState().play();
    await vi.advanceTimersByTimeAsync(PLAY_TICK_MS * 3);

    expect(useAppStore.getState()).toMatchObject({ playing: false, clock: '13:00', choiceLoading: false });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
    expect(useAppStore.getState().resumeAfterChoice).toEqual({ time: '13:00', play: true });

    vi.mocked(api.setTimelineVariant).mockResolvedValue(at('13:00', { pending_choice: null }));
    vi.mocked(api.moveCursor).mockResolvedValue(at('13:00'));
    await useAppStore.getState().chooseVariant('stable');
    expect(api.setTimelineVariant).toHaveBeenCalledWith('d_test', 'tl_2', 'stable');
    expect(useAppStore.getState()).toMatchObject({ choice: null, resumeAfterChoice: null, playing: true });
    useAppStore.getState().stopPlayback();
  });

  it('after a slider release past the event commits to the released time once the variant is chosen', async () => {
    useAppStore.getState().setPlanningState(at('09:00'));
    vi.mocked(api.moveCursor).mockResolvedValueOnce(awaitingState('13:00')).mockResolvedValueOnce(at('17:00'));
    useAppStore.getState().startDrag();
    useAppStore.getState().setClock('17:00');
    useAppStore.getState().endDrag();
    await vi.waitFor(() => expect(useAppStore.getState().choice?.entry_id).toBe('tl_2'));
    expect(useAppStore.getState()).toMatchObject({ clock: '13:00', resumeAfterChoice: { time: '17:00', play: false } });

    vi.mocked(api.setTimelineVariant).mockResolvedValue(at('13:00'));
    await useAppStore.getState().chooseVariant('keep');
    expect(vi.mocked(api.moveCursor).mock.calls.at(-1)).toEqual(['d_test', '17:00']);
    expect(useAppStore.getState().clock).toBe('17:00');
  });

  it('closing without a choice keeps the clock at the event, and play asks again', () => {
    useAppStore.getState().setPlanningState(awaitingState('13:00'));
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
    useAppStore.getState().closeChoice();
    expect(useAppStore.getState()).toMatchObject({ choice: null, dismissedChoice: 'tl_2', clock: '13:00' });

    // Тот же ответ сервера окно заново не открывает, а «Запустить» открывает.
    useAppStore.getState().setPlanningState(awaitingState('13:00'));
    expect(useAppStore.getState().choice).toBeNull();
    useAppStore.getState().play();
    expect(useAppStore.getState()).toMatchObject({ playing: false, dismissedChoice: null, resumeAfterChoice: { time: '13:00', play: true } });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
  });

  it('opens a loading choice while a breaking event at the clock is added', async () => {
    useAppStore.getState().setPlanningState(at('12:00'));
    const response = deferred<PlanningState>();
    vi.mocked(api.addTimelineEvent).mockReturnValue(response.promise);
    const adding = useAppStore.getState().applyEvent({ type: 'engineer_unavailable', time: '12:00', request: null, request_id: null, engineer_id: 'E02' });
    await vi.waitFor(() => expect(useAppStore.getState().choiceLoading).toBe(true));
    response.resolve(awaitingState('12:00'));
    await adding;
    expect(useAppStore.getState()).toMatchObject({ choiceLoading: false });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
  });

  it('opens the choice of an applied event from its pin and changes it without moving the clock', async () => {
    useAppStore.getState().setPlanningState(at('15:00'));
    vi.mocked(api.getTimelineVariants).mockResolvedValue(makeEventChoice({ current: 'optimal' }));
    await useAppStore.getState().openChoice('tl_2');
    expect(useAppStore.getState()).toMatchObject({ choiceLoading: false, resumeAfterChoice: null });
    expect(useAppStore.getState().choice?.current).toBe('optimal');

    vi.mocked(api.setTimelineVariant).mockResolvedValue(at('15:00', { version: 9 }));
    expect(await useAppStore.getState().chooseVariant('keep')).toBe(true);
    expect(api.moveCursor).not.toHaveBeenCalled();
    expect(useAppStore.getState()).toMatchObject({ choice: null, clock: '15:00' });
    expect(useAppStore.getState().state?.version).toBe(9);
  });

  it('keeps the dialog open with an error when the choice fails and closes it on a new day', async () => {
    useAppStore.getState().setPlanningState(awaitingState('13:00'));
    vi.mocked(api.setTimelineVariant).mockRejectedValue(new api.ApiError(409, 'Для этого события варианты не предлагаются.'));
    expect(await useAppStore.getState().chooseVariant('keep')).toBe(false);
    expect(useAppStore.getState()).toMatchObject({ error: 'Для этого события варианты не предлагаются.', busy: false });
    expect(useAppStore.getState().choice?.entry_id).toBe('tl_2');
    useAppStore.getState().reset();
    expect(useAppStore.getState()).toMatchObject({ choice: null, choiceLoading: false, resumeAfterChoice: null, dismissedChoice: null });
  });
});
```

- [ ] **Step 6: Запустить — падают**

Run: `cd frontend && npx vitest run src/store/useAppStore.test.ts src/lib/timeBar.test.ts`
Expected: FAIL (нет `chooseVariant`, `choice`).

- [ ] **Step 7: Стор (`frontend/src/store/useAppStore.ts`)**

Импорты: `getTimelineVariants, setTimelineVariant` из клиента; типы `EventChoice, EventVariant`; `CHOOSABLE_EVENTS` из `../lib/variants`.

В `AppData`:

```ts
  /** Окно выбора варианта исправления; null — закрыто или ждёт варианты. */
  choice: EventChoice | null;
  /** Окно выбора открыто и ждёт варианты от сервера. */
  choiceLoading: boolean;
  /** Куда вернуть часы после выбора: время и шли ли часы до остановки на событии. */
  resumeAfterChoice: { time: HHMM; play: boolean } | null;
  /** Событие, окно которого закрыли без выбора: ответы сервера его не открывают, пока часы не пойдут дальше. */
  dismissedChoice: string | null;
```

В `initialAppData`: `choice: null, choiceLoading: false, resumeAfterChoice: null, dismissedChoice: null,`. В `AppActions`:

```ts
  /** Выбрать стратегию для открытого окна; часы возвращаются туда, откуда их остановило событие. */
  chooseVariant(variant: EventVariant): Promise<boolean>;
  /** Открыть окно выбора для события шкалы (смена выбора с метки). */
  openChoice(entryId: string): Promise<void>;
  /** Закрыть окно без выбора: часы стоят на событии. */
  closeChoice(): void;
```

Вспомогательная функция рядом с `withPeriod`:

```ts
const laterOf = (a: HHMM, b: HHMM): HHMM => (isValidTime(a) && isValidTime(b) && toMinutes(a) > toMinutes(b) ? a : b);
```

В `receive(next, syncClock)`: в начале деструктуризации взять `playing: wasPlaying` из `get()` (рядом с `clock`). В конце функции, после основного `set(...)` и до `schedulePoll`:

```ts
    // Время плана остановилось на «ломающем» событии без выбора: часы ждут на нём, открывается окно выбора.
    const pending = next.pending_choice ?? null;
    const { choice, dismissedChoice } = get();
    if (pending && pending.entry_id !== choice?.entry_id && pending.entry_id !== dismissedChoice) {
      stopTicking();
      set({
        playing: false,
        clock: next.cursor,
        choice: pending,
        choiceLoading: false,
        resumeAfterChoice: { time: laterOf(clock, next.cursor), play: wasPlaying },
      });
    }
```

`commitClock`: сразу после `if (!datasetId || !state) return Promise.resolve(true);` добавить:

```ts
      // Часы пытаются пройти событие, окно которого закрыли без выбора: сервер снова остановит их, окно откроется.
      if (state.pending_choice && isValidTime(clock) && toMinutes(clock) > toMinutes(state.cursor)) set({ dismissedChoice: null });
```

`play()`: после `if (!state || playing) return;`:

```ts
      if (state.pending_choice) {
        set({ dismissedChoice: null, choice: state.pending_choice, resumeAfterChoice: { time: state.cursor, play: true } });
        return;
      }
```

`applyEvent(event)`: перед `set({ busy: true, error: null })` вычислить и отметить загрузку:

```ts
      const { clock } = get();
      // «Ломающее» событие на время часов или раньше: сервер сразу попросит выбрать вариант, окно ждёт его ответа.
      const asks = CHOOSABLE_EVENTS.has(event.type) && isValidTime(event.time) && isValidTime(clock) && toMinutes(event.time) <= toMinutes(clock);
      set({ busy: true, error: null, ...(asks ? { choice: null, choiceLoading: true, dismissedChoice: null } : {}) });
```

и в `finally` добавить `if (isCurrent(current) && get().choiceLoading) set({ choiceLoading: false });`.

`upload()` и `plan()`: в их `set({...})` добавить `choice: null, choiceLoading: false, resumeAfterChoice: null, dismissedChoice: null`.

Новые действия (рядом с `deleteTimelineEvent`):

```ts
    async chooseVariant(variant) {
      const { datasetId, choice, resumeAfterChoice } = get();
      if (!datasetId || !choice) return false;
      const current = generation;
      set({ busy: true, error: null });
      let chosen = false;
      try {
        const state = await enqueue(current, () => setTimelineVariant(datasetId, choice.entry_id, variant));
        if (!isCurrent(current)) return false;
        set({ choice: null, choiceLoading: false, resumeAfterChoice: null, dismissedChoice: null });
        get().setPlanningState(state);
        chosen = true;
      } catch (error) {
        if (isCurrent(current) && !(error instanceof StaleSession)) set({ error: errorMessage(error) });
      } finally {
        if (isCurrent(current)) set({ busy: false });
      }
      if (!chosen || !isCurrent(current) || !resumeAfterChoice) return chosen;
      if (resumeAfterChoice.play) {
        get().play();
      } else if (resumeAfterChoice.time !== get().clock) {
        set({ clock: resumeAfterChoice.time });
        await get().commitClock();
      }
      return true;
    },

    async openChoice(entryId) {
      const { datasetId } = get();
      if (!datasetId) return;
      const current = generation;
      set({ choice: null, choiceLoading: true, resumeAfterChoice: null, error: null });
      try {
        const choice = await getTimelineVariants(datasetId, entryId);
        if (isCurrent(current)) set({ choice, choiceLoading: false });
      } catch (error) {
        if (isCurrent(current)) set({ choiceLoading: false, error: errorMessage(error) });
      }
    },

    closeChoice() {
      set({ dismissedChoice: get().choice?.entry_id ?? null, choice: null, choiceLoading: false, resumeAfterChoice: null });
    },
```

- [ ] **Step 8: Тесты, типы**

Run: `cd frontend && npx vitest run src/store/useAppStore.test.ts src/lib/timeBar.test.ts && npx tsc --noEmit`
Expected: PASS. Если тест «pauses the playing clock…» упирается в точную последовательность тиков, подстройте подготовку (часы на 12:59, событие на шкале в 13:00), не меняя проверяемого поведения.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/api frontend/src/lib/variants.ts frontend/src/lib/timeBar.ts frontend/src/lib/timeBar.test.ts frontend/src/store frontend/src/test/fixtures.ts
git commit -m "Фронтенд: выбор варианта в сторе — пауза часов на событии, выбор и продолжение

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Окно выбора и метки шкалы

**Files:**
- Create: `frontend/src/components/events/ChoiceDialog.tsx`
- Modify: `frontend/src/components/MainScreen.tsx`, `frontend/src/components/TimeBar.tsx`, `frontend/src/components/WhyPanel.tsx`, `frontend/src/styles.css`
- Test: `frontend/src/components/events/ChoiceDialog.test.tsx`, `frontend/src/components/TimeBar.test.tsx`

**Interfaces:**
- Consumes: стор `choice`, `choiceLoading`, `chooseVariant`, `openChoice`, `closeChoice`; `VARIANT_TITLES`; `describeEvent`; `formatKm`, `formatSigned`.
- Produces: `ChoiceDialog` (без пропсов).

- [ ] **Step 1: Падающие тесты окна `frontend/src/components/events/ChoiceDialog.test.tsx`**

```tsx
import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore } from '../../store/useAppStore';
import { makeEventChoice, makePlanningState } from '../../test/fixtures';
import { resetStore } from '../../test/store';
import { ChoiceDialog } from './ChoiceDialog';

const dialog = () => screen.getByRole('dialog', { name: /Бригада Белузин недоступ/ });
const card = (title: string) => within(dialog()).getByRole('article', { name: title });

beforeEach(() => {
  resetStore({ datasetId: 'd_test', state: makePlanningState() });
});

describe('ChoiceDialog', () => {
  it('shows three variants, highlights the recommended one and focuses its button', () => {
    useAppStore.setState({ choice: makeEventChoice() });
    render(<ChoiceDialog />);
    expect(within(dialog()).getAllByRole('article').map((item) => item.getAttribute('aria-label'))).toEqual([
      'Оптимально по дню',
      'Минимум перестановок',
      'Ничего не менять',
    ]);
    expect(card('Оптимально по дню')).toHaveClass('variant--recommended');
    expect(within(card('Оптимально по дню')).getByText('Рекомендуем')).toBeInTheDocument();
    expect(within(card('Минимум перестановок')).queryByText('Рекомендуем')).not.toBeInTheDocument();
    expect(document.activeElement).toBe(within(card('Оптимально по дню')).getByRole('button', { name: 'Выбрать' }));
    expect(within(card('Минимум перестановок')).getByText('✓ на 3 заявки меньше переезжает к другим бригадам')).toBeInTheDocument();
    expect(within(card('Ничего не менять')).getByText('✗ на 4 клиента без инженера или с опозданием больше')).toBeInTheDocument();
    expect(within(card('Ничего не менять')).getByText('+2', { selector: '.variant__delta' })).toBeInTheDocument();
  });

  it('chooses a variant and marks the current one', () => {
    const chooseVariant = vi.fn().mockResolvedValue(true);
    useAppStore.setState({ choice: makeEventChoice({ current: 'stable' }), chooseVariant });
    render(<ChoiceDialog />);
    expect(within(card('Минимум перестановок')).getByRole('button', { name: 'Выбрано' })).toBeDisabled();
    fireEvent.click(within(card('Ничего не менять')).getByRole('button', { name: 'Выбрать' }));
    expect(chooseVariant).toHaveBeenCalledWith('keep');
  });

  it('closes on ✕ and on Escape without a choice', () => {
    const closeChoice = vi.fn();
    useAppStore.setState({ choice: makeEventChoice(), closeChoice });
    render(<ChoiceDialog />);
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Закрыть выбор' }));
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(closeChoice).toHaveBeenCalledTimes(2);
  });

  it('says that variants are being computed and renders nothing when closed', () => {
    useAppStore.setState({ choiceLoading: true });
    const { rerender } = render(<ChoiceDialog />);
    expect(screen.getByRole('dialog')).toHaveTextContent('Считаем варианты…');
    useAppStore.setState({ choiceLoading: false });
    rerender(<ChoiceDialog />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
```

Фикстура: событие `makeEventChoice()` — `engineer_unavailable` для `E02` (Бригада Белузин). Если `describeEvent` даёт другой текст, поправить регулярку в `dialog()` под фактический текст `describeEvent`.

- [ ] **Step 2: Запустить — падает**

Run: `cd frontend && npx vitest run src/components/events/ChoiceDialog.test.tsx`
Expected: FAIL — нет модуля `./ChoiceDialog`.

- [ ] **Step 3: `frontend/src/components/events/ChoiceDialog.tsx`**

```tsx
import { useEffect } from 'react';
import type { EventVariant, Metrics, VariantOption } from '../../api/types';
import { describeEvent } from '../../lib/events';
import { formatKm, formatSigned } from '../../lib/format';
import { byId } from '../../lib/planView';
import { useAppStore } from '../../store/useAppStore';

interface NumberProps {
  label: string;
  value: number;
  before?: number;
  km?: boolean;
}

/** Крупная цифра варианта и сдвиг к плану до события. */
function VariantNumber({ label, value, before, km = false }: NumberProps) {
  const delta = before === undefined ? null : formatSigned(value - before, km ? 1 : 0);
  return (
    <div className="variant__number">
      <dt>{label}</dt>
      <dd>
        <strong>{km ? formatKm(value) : value}</strong>
        {delta !== null && delta !== '0' && <span className="variant__delta">{delta}</span>}
      </dd>
    </div>
  );
}

interface CardProps {
  option: VariantOption;
  before: Metrics;
  lateBefore: number;
  current: EventVariant | null;
  locked: boolean;
  onChoose(variant: EventVariant): void;
}

function VariantCard({ option, before, lateBefore, current, locked, onChoose }: CardProps) {
  const chosen = current === option.variant;
  return (
    <article className={`variant${option.recommended ? ' variant--recommended' : ''}`} aria-label={option.title}>
      <header className="variant__head">
        {option.recommended && <span className="badge badge--diff">Рекомендуем</span>}
        <h3>{option.title}</h3>
        <p className="muted">{option.summary}</p>
      </header>
      <dl className="variant__numbers">
        <VariantNumber label="Без инженера" value={option.metrics.unassigned} before={before.unassigned} />
        <VariantNumber label="Опоздания" value={option.late} before={lateBefore} />
        <VariantNumber label="Бригад" value={option.metrics.engineers_used} before={before.engineers_used} />
        <VariantNumber label="Переносов" value={option.moved} />
        <VariantNumber label="Пробег" value={option.metrics.total_km} before={before.total_km} km />
      </dl>
      {option.pros.length > 0 && (
        <ul className="variant__pros">
          {option.pros.map((text) => (
            <li key={text}>{`✓ ${text}`}</li>
          ))}
        </ul>
      )}
      {option.cons.length > 0 && (
        <ul className="variant__cons">
          {option.cons.map((text) => (
            <li key={text}>{`✗ ${text}`}</li>
          ))}
        </ul>
      )}
      <button
        type="button"
        className={`btn${option.recommended ? ' btn-primary' : ''} variant__choose`}
        // Рекомендованный вариант выбирается Enter сразу после открытия окна.
        autoFocus={option.recommended}
        disabled={locked || chosen}
        onClick={() => onChoose(option.variant)}
      >
        {chosen ? 'Выбрано' : 'Выбрать'}
      </button>
    </article>
  );
}

/**
 * Окно выбора варианта исправления при «ломающем» событии: три карточки, рекомендованная выделена.
 * Часы стоят, пока окно открыто; закрытое без выбора окно оставляет часы на событии.
 */
export function ChoiceDialog() {
  const state = useAppStore((s) => s.state);
  const choice = useAppStore((s) => s.choice);
  const loading = useAppStore((s) => s.choiceLoading);
  const busy = useAppStore((s) => s.busy);
  const chooseVariant = useAppStore((s) => s.chooseVariant);
  const closeChoice = useAppStore((s) => s.closeChoice);
  const open = loading || choice !== null;

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Окно выбора — верхний слой: Esc закрывает только его.
      event.preventDefault();
      closeChoice();
    };
    document.addEventListener('keydown', closeOnEscape, true);
    return () => document.removeEventListener('keydown', closeOnEscape, true);
  }, [open, closeChoice]);

  if (!open) return null;
  const engineers = byId(state?.engineers ?? []);
  const title = choice ? describeEvent(choice.event, engineers) : 'Событие дня';

  return (
    <div className="choice-overlay">
      <section className="choice" role="dialog" aria-modal="true" aria-labelledby="choice-title">
        <header className="choice__head">
          <div>
            <p className="choice__eyebrow">Как исправить план</p>
            <h2 id="choice-title">{title}</h2>
          </div>
          <button type="button" className="btn btn-ghost btn-small" aria-label="Закрыть выбор" onClick={closeChoice}>
            ✕
          </button>
        </header>
        {loading && <p className="muted">Считаем варианты…</p>}
        {choice && (
          <div className="choice__cards">
            {choice.variants.map((option) => (
              <VariantCard
                key={option.variant}
                option={option}
                before={choice.metrics_before}
                lateBefore={choice.late_before}
                current={choice.current}
                locked={busy}
                onChoose={(variant) => void chooseVariant(variant)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Подключить (`MainScreen.tsx`, `WhyPanel.tsx`)**

`MainScreen.tsx`: импорт `ChoiceDialog` и строка `<ChoiceDialog />` перед `<ErrorToast />`.

`WhyPanel.tsx`, в обработчике Esc: к списку открытых слоёв добавить окно выбора:

```ts
      const { mapMenu, toolbarDialog, editingRequestId, delayDialogOpen, engineerDialog, choice, choiceLoading } = useAppStore.getState();
      if (mapMenu || toolbarDialog || editingRequestId || delayDialogOpen || engineerDialog || choice || choiceLoading) return;
```

- [ ] **Step 5: Метки шкалы (`TimeBar.tsx`) — падающий тест в `TimeBar.test.tsx`**

```tsx
it('offers «Варианты…» for an event that breaks the plan and shows its choice', () => {
  const openChoice = vi.fn().mockResolvedValue(undefined);
  const timeline = [
    makeTimelineItem({ id: 'tl_2', event: makeEventChoice().event, status: 'applied', choosable: true, variant: 'stable' }),
    makeTimelineItem({ id: 'tl_3', status: 'applied' }),
  ];
  resetStore({ datasetId: 'd_test', state: makePlanningState({ timeline }), openChoice });
  render(<TimeBar />);
  fireEvent.click(screen.getByRole('button', { name: /Недоступен/ }));
  const events = screen.getByRole('group', { name: 'События 13:00' });
  expect(within(events).getByText('Вариант: Минимум перестановок')).toBeInTheDocument();
  fireEvent.click(within(events).getByRole('button', { name: 'Варианты…' }));
  expect(openChoice).toHaveBeenCalledWith('tl_2');
  expect(screen.queryByRole('group', { name: 'События 13:00' })).not.toBeInTheDocument();
});

it('blocks play and the slider while the choice is open', () => {
  resetStore({ datasetId: 'd_test', state: makePlanningState(), choice: makeEventChoice() });
  render(<TimeBar />);
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
  expect(screen.getByRole('slider', { name: 'Текущее время' })).toBeDisabled();
});
```

Реализация в `TimeBar.tsx`:

```tsx
  const openChoice = useAppStore((s) => s.openChoice);
  const choosing = useAppStore((s) => s.choice !== null || s.choiceLoading);
```

- кнопка запуска: `disabled={choosing}`;
- `<input type="range" … disabled={choosing}`;
- в `openPin.items.map`, в `<li>` между статусом и «Удалить событие»:

```tsx
                  {item.choosable && (
                    <>
                      <span className="muted">{item.variant ? `Вариант: ${VARIANT_TITLES[item.variant]}` : 'Вариант не выбран'}</span>
                      <button
                        type="button"
                        className="btn btn-small"
                        disabled={deleteLocked || item.status === 'rejected'}
                        onClick={() => {
                          setOpenMinute(null);
                          void openChoice(item.id);
                        }}
                      >
                        Варианты…
                      </button>
                    </>
                  )}
```

(`VARIANT_TITLES` импортировать из `../lib/variants`.)

- [ ] **Step 6: Стили (`frontend/src/styles.css`, после блока `.dialog__actions`)**

```css
/* Окно выбора варианта исправления: поверх всего экрана, три карточки в ряд. */
.choice-overlay {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(16, 24, 40, 0.35);
}

.choice {
  width: min(1100px, 100%);
  max-height: calc(100vh - 48px);
  overflow: auto;
  background: var(--surface);
  border-radius: 14px;
  box-shadow: var(--shadow);
  padding: 20px 22px 22px;
}

.choice__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
}

.choice__head h2 {
  margin: 0;
  font-size: 20px;
}

.choice__eyebrow {
  margin: 0 0 2px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--primary);
}

.choice__cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.variant {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
}

.variant--recommended {
  border: 2px solid var(--primary);
  background: #f7f9ff;
}

.variant__head h3 {
  margin: 4px 0 2px;
  font-size: 17px;
}

.variant__head p {
  margin: 0;
}

.variant__numbers {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
  margin: 0;
}

.variant__number dt {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
}

.variant__number dd {
  margin: 0;
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.variant__number strong {
  font-size: 20px;
}

.variant__delta {
  font-size: 12px;
  color: var(--muted);
}

.variant__pros,
.variant__cons {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 13px;
}

.variant__pros {
  color: var(--ok);
}

.variant__cons {
  color: var(--danger);
}

.variant__choose {
  margin-top: auto;
}

.time-bar__pin--awaiting {
  box-shadow: 0 0 0 2px var(--warn);
  animation: pin-awaiting 1.2s ease-in-out infinite;
}

@keyframes pin-awaiting {
  50% {
    box-shadow: 0 0 0 4px var(--warn-soft);
  }
}

@media (max-width: 1180px) {
  .choice__cards {
    grid-template-columns: minmax(0, 1fr);
  }
}
```

- [ ] **Step 7: Все проверки фронтенда**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
Expected: все тесты зелёные, сборка без ошибок.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components frontend/src/styles.css
git commit -m "Окно выбора варианта исправления и «Варианты…» у метки события

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Документация, сборка и живая проверка

**Files:**
- Modify: `README.md`, `docs/assumptions.md`

- [ ] **Step 1: README**

В списке возможностей после пункта «События дня: …» добавить строку:

```markdown
- Варианты исправления: на недоступность, смену транспорта, задержку инженера и срочную заявку сервис считает три варианта — «Оптимально по дню», «Минимум перестановок» и «Ничего не менять», — сравнивает их по клиентам без инженера и опозданиям, бригадам, переносам заявок и километрам и рекомендует лучший. Часы останавливаются на таком событии, пока диспетчер не выберет; выбор можно поменять с метки события на шкале.
```

В сценарии демо, в пункте про «Время дня»: дописать предложение «Когда часы доходят до недоступности или поломки транспорта, открывается окно с тремя вариантами исправления: выберите, и часы пойдут дальше».

- [ ] **Step 2: `docs/assumptions.md`**

Новый раздел после «Перепланирование»:

```markdown
## Варианты исправления при событии

- «Ломающие» события — недоступность, смена транспорта, задержка инженера и срочная заявка. Для них считаются три варианта от одного плана до события: «Оптимально по дню» (обычный пересчёт), «Минимум перестановок» (перенос заявки к другому инженеру стоит условные 500 км вместо 20) и «Ничего не менять» (без решателя: прежние маршруты; заявки инженера, которому больше нельзя работать, и заявки, которым нужен транспорт, которого у инженера нет, остаются без инженера; срочная заявка тоже; опоздания видны).
- Рекомендуется вариант с наименьшим числом клиентов без инженера и с опозданием, затем с меньшим числом бригад, затем с меньшим числом заявок, переехавших к другим бригадам, затем с меньшим пробегом; при равенстве — «Оптимально по дню». Строки «лучше / хуже» строятся по ненулевым разницам с рекомендованным (у рекомендованного — со следующим).
- Время плана не проходит «ломающее» событие без выбора. Выбор хранится в событии шкалы как стратегия: смена выбора или события раньше по шкале пересчитывает план с этой стратегией, выборы более поздних событий сохраняются. Прямой вызов `POST /events` и одобрение предложений помощника применяют «Оптимально по дню» сразу.
- Фоновый расчёт шкалы доходит до первого события без выбора и считает для него все три варианта; для события на текущее время окно ждёт расчёта двух прогонов решателя.
```

- [ ] **Step 3: Полные проверки и commit**

Run: `cd backend && ~/.local/bin/uv run pytest -q -o addopts= && ~/.local/bin/uv run ruff check app tests scripts && ~/.local/bin/uv run ruff format --check app tests scripts`
Run: `cd frontend && npx vitest run && npx tsc --noEmit && npm run build`
Expected: всё зелёное.

```bash
git add README.md docs/assumptions.md
git commit -m "Документация: варианты исправления при событии дня

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Пересборка и живая проверка**

Run: `docker compose up -d --build --wait backend frontend`

В Chrome на `http://localhost:8000` (Юго-восток; загрузить `data/bundles/south_east/bundle.json`, если сессии нет):
1. «Добавить событие» → «Инженер заболел» на 13:00 для самой загруженной бригады, часы на 09:00, «Запустить».
2. Часы встают на 13:00, открывается окно с тремя карточками, одна «Рекомендуем»; Enter выбирает её; часы идут дальше.
3. Клик по метке 13:00 → «Варианты…» → выбрать «Ничего не менять» → план на экране меняется, баннер «до / после» показывает разницу.
4. Отметить время ответа окна для события «сейчас» (ожидаемо до ~10 с).
