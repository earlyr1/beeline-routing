"""Что изменилось в фактах дня: единственное место планирования, где важен тип события.

Спецификация: docs/superpowers/specs/2026-09-24-unified-events-design.md. Обработчик своего типа (таблица _KINDS)
проверяет событие против текущего состояния дня — EventRejected с текстом для диспетчера — и возвращает Facts:
заявки и инженеров после события, событие в том виде, в каком его сохранит план, и то, что событие добавляет
к задаче на остаток дня. Дальше apply_event (app/planning/session.py) идёт одним путём для всех типов: закрепить
сделанное, посчитать стратегию, собрать разницу планов. Шкала (app/planning/timeline.py) и API спрашивают отсюда
же, какие номера событие называет, о какой оно заявке, где искать его адрес и о чём договорились с клиентом: сами
они тип события не проверяют.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from app.domain.enums import (
    SKILL_RU,
    TRANSPORT_RU,
    EventType,
    Priority,
    RequestStatus,
    RequestTier,
    request_label,
)
from app.domain.models import Engineer, Event, Plan, Request, TimeWindow, Visit
from app.domain.timeutil import fmt_hhmm
from app.ingest.geocode import GeoResult
from app.planning.delay import delay_engineer, forecast_delay, missed_hold
from app.planning.models import PlanDiff
from app.solvers.problem import Problem
from app.solvers.reasons import too_far_km
from app.solvers.simulate import simulate_route

if TYPE_CHECKING:
    from app.planning.session import PlanningContext, PlanningSession


class EventRejected(ValueError):
    """Событие нельзя применить. Текст сообщения показывается диспетчеру (HTTP 422)."""


# Поля, которые меняет «Изменение заявки». Координаты и точность геокодирования следуют за источником места,
# номер, статус, район и типы заявки всегда берутся из сохранённой заявки. Окно заявки «как можно скорее» задаёт backend.
EDITABLE_REQUEST_FIELDS = frozenset(
    {
        "address",
        "duration_min",
        "window_start",
        "window_end",
        "priority",
        "asap",
        "skill",
        "transport_required",
        "needs_equipment",
    }
)
_NOT_FOUND = GeoResult(None, None, "none", None)

# Закрепить сделанное к времени события заново, отпустив ещё и эти заявки (визит в пути к ним не удерживается).
Pin = Callable[[Collection[str]], Problem]


@dataclass(frozen=True)
class Facts:
    """Что событие изменило в фактах дня. Входная сессия не меняется: requests и engineers — копии.

    event — событие, как его сохранит план: с полями, которые заполняет backend (previous_request,
    previous_transport, previous_engineer_id), и с координатами срочной заявки. released — заявки, визит в пути
    к которым не удерживается: их изменили или переназначили, и солвер решает заново. subject_request_id — заявка,
    о которой событие, если оно об одной заявке: её диспетчер может отдать выбранной бригаде (assignable).

    Три поправки, которые нужны не каждому событию: adjust — задача после закрепления сделанного (задержка
    сдвигает день инженера; pin закрепляет заново, отпустив ещё и названные заявки), check — проверка по этой
    задаче до выбора стратегии (переназначение: бригада успевает к заявке), annotate — подпись к разнице планов
    (прогноз задержки без пересчёта).
    """

    requests: list[Request]
    engineers: list[Engineer]
    event: Event
    released: frozenset[str] = frozenset()
    subject_request_id: str | None = None
    adjust: Callable[[Problem, Pin], Problem] | None = None
    check: Callable[[Problem], None] | None = None
    annotate: Callable[[PlanDiff, Problem, Plan], PlanDiff] | None = None


@dataclass
class _Day:
    """Копии заявок и инженеров дня, которые меняет обработчик, и работа, начатая к времени события."""

    requests: list[Request]
    engineers: list[Engineer]
    plan: Plan
    started: dict[str, Visit]

    def request(self, request_id: str | None) -> Request:
        request = next((item for item in self.requests if item.id == request_id), None)
        if request is None:
            raise EventRejected(f"Заявка {request_id} не найдена.")
        return request

    def engineer(self, engineer_id: str | None) -> Engineer:
        engineer = next((item for item in self.engineers if item.id == engineer_id), None)
        if engineer is None:
            raise EventRejected(f"Инженер {engineer_id} не найден.")
        return engineer

    def facts(self, event: Event, released: Collection[str] = ()) -> Facts:
        return Facts(
            requests=self.requests, engineers=self.engineers, event=event, released=frozenset(released)
        )

    def started_text(self, request: Request, action: str) -> str | None:
        """«Заявка … уже в работе с HH:MM, <action> её нельзя.», если работа по заявке начата до события."""
        visit = self.started.get(request.id)
        if visit is None:
            return None
        label = request_label(request.id, request.priority)
        return f"Заявка {label} уже в работе с {fmt_hhmm(visit.start)}, {action} её нельзя."


# --- Место заявки и геокодер -------------------------------------------------------------------------------------


def _has_point(request: Request) -> bool:
    return request.lat is not None and request.lon is not None


def _located(request: Request, ctx: PlanningContext) -> Request:
    if _has_point(request) or ctx.geocode is None:
        return request
    geo = ctx.geocode(request.address, request.district)
    return request.model_copy(update={"lat": geo.lat, "lon": geo.lon, "geocode_precision": geo.precision})


def _known_addresses(answers: dict[str, GeoResult]) -> Callable[[str, str], GeoResult]:
    """Геокодер из готовых ответов: адрес, которого нет среди них, не найден."""
    return lambda address, district: answers.get(address, _NOT_FOUND)


def replay_checked_event(event: Event, ctx: PlanningContext) -> tuple[Event, PlanningContext]:
    """Готовит к применению изменение заявки, которое уже прошло check_event (предложение помощника).

    Координаты в таком событии дал геокодер, а не точка на карте. Они передаются как готовый ответ геокодера
    на адрес заявки: точность адреса сохраняется, геокодер не вызывается, а если адрес совпадает с сохранённым,
    остаются текущие координаты заявки. Другие события возвращаются без изменений.
    """
    sent = edited_request(event)
    if sent is None:
        return event, ctx
    answer = GeoResult(sent.lat, sent.lon, sent.geocode_precision, None)
    unlocated = sent.model_copy(update={"lat": None, "lon": None})
    return (
        event.model_copy(update={"request": unlocated}),
        replace(ctx, geocode=_known_addresses({sent.address: answer})),
    )


def geocode_entry(
    event: Event, requests: Mapping[str, Request], ctx: PlanningContext
) -> tuple[Event, dict[str, GeoResult]]:
    """Ищет адрес события таймлайна один раз, при добавлении и до блокировок датасета.

    Срочная заявка получает координаты в самом событии. Адрес изменённой заявки без точки на карте ищется всегда,
    даже если совпадает с сохранённым: к моменту применения адрес заявки может поменять событие раньше по времени.
    Ответ геокодера возвращается словарём «адрес → ответ» и хранится вместе с событием, повторные применения берут
    его через offline_context. requests — заявки дня и срочные заявки таймлайна по номеру: из них берётся район.
    """
    if ctx.geocode is None:
        return event, {}
    added = new_request(event)
    if added is not None:
        located = _located(added, ctx)
        return (event if located is added else event.model_copy(update={"request": located})), {}
    sent = edited_request(event)
    known = requests.get(event.request_id or "") if sent is not None else None
    if sent is None or known is None or _has_point(sent):
        return event, {}
    return event, {sent.address: ctx.geocode(sent.address, known.district)}


def offline_context(ctx: PlanningContext, answers: Mapping[str, GeoResult]) -> PlanningContext:
    """Контекст повторного применения: геокодер не вызывается, адреса берутся только из готовых ответов."""
    return replace(ctx, geocode=_known_addresses(dict(answers)) if answers else None)


def _edited_location(stored: Request, sent: Request, ctx: PlanningContext) -> dict[str, Any]:
    """Место изменённой заявки: точка на карте, новый адрес через геокодер или прежние координаты."""
    if _has_point(sent):
        if (sent.lat, sent.lon) == (stored.lat, stored.lon):
            return {}
        return {"lat": sent.lat, "lon": sent.lon, "geocode_precision": "house"}
    if sent.address == stored.address:
        return {}
    geo = ctx.geocode(sent.address, stored.district) if ctx.geocode is not None else _NOT_FOUND
    if geo.lat is None or geo.lon is None:
        raise EventRejected(f"Адрес «{sent.address}» не найден на карте. Укажите точку на карте.")
    return {"lat": geo.lat, "lon": geo.lon, "geocode_precision": geo.precision}


# --- Общие правила -----------------------------------------------------------------------------------------------


def _started_visits(plan: Plan, now: int) -> dict[str, Visit]:
    return {visit.request_id: visit for route in plan.routes for visit in route.visits if visit.start < now}


def _unavailable_since(engineer: Engineer) -> int:
    return engineer.unavailable_from if engineer.unavailable_from is not None else engineer.shift_start


def _working_until(engineer: Engineer) -> int:
    """Конец рабочего дня инженера: конец смены или время, с которого он недоступен."""
    if engineer.available:
        return engineer.shift_end
    return min(engineer.shift_end, _unavailable_since(engineer))


def asap_window(engineers: list[Engineer], now: int) -> dict[str, int]:
    """Окно заявки «как можно скорее»: от времени события до самого позднего конца смен.

    Считаются инженеры, доступные во время события: недоступный с более позднего времени работает до этого
    времени, уже недоступный не считается. Если никто не работает позже now, конец окна равен началу.
    """
    return {"window_start": now, "window_end": max([now, *(_working_until(e) for e in engineers)])}


def window_order_text(label: str) -> str:
    return f"Конец окна заявки {label} должен быть позже начала."


def _unavailable_text(engineer: Engineer, action: str) -> str:
    return f"{engineer.name} недоступен с {fmt_hhmm(_unavailable_since(engineer))}, {action}."


def _planned_engineer(plan: Plan, request_id: str) -> str | None:
    return next(
        (
            route.engineer_id
            for route in plan.routes
            for visit in route.visits
            if visit.request_id == request_id
        ),
        None,
    )


def delayed_until(events: Iterable[Event]) -> dict[str, int]:
    """Для каждого задержанного инженера самое позднее время T + N среди его задержек.

    Задержка прежнего события продолжает действовать на следующих (app/planning/delay.keep_delays): это факт дня,
    который живёт в истории событий, а не в инженере.
    """
    until: dict[str, int] = {}
    for event in events:
        if event.type == EventType.ENGINEER_DELAYED and event.engineer_id and event.delay_min is not None:
            until[event.engineer_id] = max(until.get(event.engineer_id, 0), event.time + event.delay_min)
    return until


# --- Обработчики по типу события ---------------------------------------------------------------------------------


def _request_updated(day: _Day, event: Event, ctx: PlanningContext) -> Facts:
    now = event.time
    stored, sent = day.request(event.request_id), event.request
    assert sent is not None  # валидатор Event: у изменения заявки request есть всегда
    label = request_label(stored.id, stored.priority)
    started = day.started_text(stored, "изменить")
    if started is not None:
        raise EventRejected(started)
    if not sent.asap:
        if sent.window_end < now:
            raise EventRejected(
                f"Окно заявки {label} заканчивается в {fmt_hhmm(sent.window_end)}, это раньше времени "
                f"события {fmt_hhmm(now)}."
            )
        if sent.window_end <= sent.window_start:
            raise EventRejected(window_order_text(label))
        window = {}
    elif stored.asap:
        # Заявка остаётся «как можно скорее»: часы ожидания не перезапускаются, окно из запроса не используется.
        window = {"window_start": stored.window_start, "window_end": stored.window_end}
    else:
        # Заявка стала «как можно скорее»: часы ожидания идут с этого события.
        window = asap_window(day.engineers, now)
    changes = {**sent.model_dump(include=set(EDITABLE_REQUEST_FIELDS)), **window}
    merged = stored.model_copy(update={**changes, **_edited_location(stored, sent, ctx)})
    if merged == stored:
        raise EventRejected(f"В заявке {label} ничего не изменилось.")
    day.requests[next(k for k, request in enumerate(day.requests) if request is stored)] = merged
    # Изменённую заявку солвер планирует заново, даже если инженер уже едет к ней.
    stored_event = event.model_copy(update={"request": merged, "previous_request": stored})
    return day.facts(stored_event, released=[stored.id])


def _cancel(day: _Day, event: Event, ctx: PlanningContext) -> Facts:
    request = day.request(event.request_id)
    if request.status == RequestStatus.CANCELLED:
        raise EventRejected(f"Заявка {request_label(request.id, request.priority)} уже отменена.")
    started = day.started_text(request, "отменить")
    if started is not None:
        raise EventRejected(started)
    request.status = RequestStatus.CANCELLED
    return day.facts(event)


def _restore(day: _Day, event: Event, ctx: PlanningContext) -> Facts:
    """Возврат в интерфейсе больше не предлагается, но дни, сохранённые раньше, и API его содержат: он
    переигрывается как был."""
    now = event.time
    request = day.request(event.request_id)
    label = request_label(request.id, request.priority)
    if request.status != RequestStatus.CANCELLED:
        raise EventRejected(f"Заявка {label} не отменена, возвращать нечего.")
    if request.window_end < now and request.asap:
        raise EventRejected(
            f"Заявка {label} как можно скорее с {fmt_hhmm(request.window_start)}: смены закончились "
            f"в {fmt_hhmm(request.window_end)}, вернуть её в план нельзя."
        )
    if request.window_end < now:
        raise EventRejected(
            f"Окно заявки {label} ({fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}) "
            f"уже прошло, вернуть её в план нельзя."
        )
    request.status = RequestStatus.ACTIVE
    return day.facts(event)


def _engineer_delayed(day: _Day, event: Event, ctx: PlanningContext) -> Facts:
    engineer = day.engineer(event.engineer_id)
    if not engineer.available:
        raise EventRejected(_unavailable_text(engineer, "задержку поставить нельзя"))
    engineer_id, delay_min = engineer.id, event.delay_min
    assert delay_min is not None  # валидатор Event: у задержки delay_min есть всегда

    # Задержка меняет не инженера, а его маршрут: визиты и доступность сдвигаются в задаче после закрепления.
    def adjust(problem: Problem, pin: Pin) -> Problem:
        missed = missed_hold(problem, engineer_id, delay_min)
        if missed is not None:
            # С задержкой инженер не успеет в окно заявки, к которой едет: кому её отдать, решает солвер.
            problem = pin([missed])
        return delay_engineer(problem, engineer_id, delay_min)

    def annotate(diff: PlanDiff, problem: Problem, before: Plan) -> PlanDiff:
        forecast = forecast_delay(problem, before, engineer_id, delay_min)
        return diff.model_copy(update={"delay_forecast": forecast})

    return replace(day.facts(event), adjust=adjust, annotate=annotate)


def _transport_changed(day: _Day, event: Event, ctx: PlanningContext) -> Facts:
    engineer = day.engineer(event.engineer_id)
    if not engineer.available:
        raise EventRejected(_unavailable_text(engineer, "сменить транспорт нельзя"))
    if engineer.transport == event.transport:
        raise EventRejected(f"У {engineer.name} уже транспорт «{TRANSPORT_RU[engineer.transport]}».")
    # Закреплённые визиты сохраняют прежние время и пробег (pin_problem берёт их из текущего плана),
    # а все участки после них солвер считает по новому транспорту.
    previous = engineer.transport
    assert event.transport is not None  # валидатор Event: у смены транспорта transport есть всегда
    engineer.transport = event.transport
    return day.facts(event.model_copy(update={"previous_transport": previous}))


def _request_reassigned(day: _Day, event: Event, ctx: PlanningContext) -> Facts:
    request = day.request(event.request_id)
    label = request_label(request.id, request.priority)
    if request.status == RequestStatus.CANCELLED:
        raise EventRejected(f"Заявка {label} отменена, назначить её нельзя.")
    if request.status == RequestStatus.POSTPONED:
        raise EventRejected(
            f"Заявка {label} перенесена, назначить её нельзя: клиенту сказали, что сегодня не приедем."
        )
    started = day.started_text(request, "переназначить")
    if started is not None:
        raise EventRejected(started)
    engineer = day.engineer(event.engineer_id)
    if not engineer.available:
        raise EventRejected(_unavailable_text(engineer, "назначить заявку нельзя"))
    if request.skill not in engineer.skills:
        raise EventRejected(f"У {engineer.name} нет навыка «{SKILL_RU[request.skill]}».")
    if request.transport_required not in (None, engineer.transport):
        raise EventRejected(
            f"Заявке {label} нужен транспорт «{TRANSPORT_RU[request.transport_required]}», "
            f"у {engineer.name} «{TRANSPORT_RU[engineer.transport]}»."
        )
    previous_engineer = _planned_engineer(day.plan, request.id)
    if previous_engineer == engineer.id:
        raise EventRejected(f"Заявка {label} уже у {engineer.name}.")
    # Закрепление держит заявку у бригады и в «Ничего не менять» (заявка встаёт в её маршрут), и в пересчёте.
    request.fixed_engineer_id = engineer.id
    request_id, engineer_id = request.id, engineer.id

    def check(problem: Problem) -> None:
        _check_reachable(problem, request_id, engineer_id, label)

    stored_event = event.model_copy(update={"previous_engineer_id": previous_engineer})
    # Переназначенную заявку солвер планирует заново, даже если инженер уже едет к ней.
    return replace(day.facts(stored_event, released=[request_id]), check=check)


def _check_reachable(problem: Problem, request_id: str, engineer_id: str, label: str) -> None:
    """Переназначение: бригада доедет до заявки, у неё есть оборудование и она успеет хотя бы без других заявок.

    Иначе EventRejected. Проверка по задаче после события (закреплённая работа, задержки) и до выбора стратегии:
    отказ от неё не зависит. label — подпись заявки для диспетчера.
    """
    if not problem.has_request(request_id):
        raise EventRejected(f"У заявки {label} нет точки на карте, назначить её нельзя.")
    state = problem.state(engineer_id)
    name = state.engineer.name
    if not state.active:
        raise EventRejected(f"У {name} не осталось рабочего времени сегодня.")
    alone = simulate_route(problem, state, [request_id])
    if alone.feasible:
        return
    far = too_far_km(problem, state, (), request_id)
    if far is not None:
        # Плечо длиннее предела транспорта: дело не во времени, бригада на велосипеде туда просто не поедет.
        transport = TRANSPORT_RU[state.engineer.transport]
        raise EventRejected(
            f"{name} не доедет до заявки {label}: до неё {far:.0f} км, "
            f"а «{transport}» не дальше {problem.leg_limit_km(state.engineer):g} км."
        )
    request = problem.request(request_id)
    if request.needs_equipment and state.equipment_left <= 0:
        # Оборудование бригада получила утром на весь день и раздала его: новую единицу днём взять негде,
        # и дело не во времени — про окно и смену тут говорить нечего.
        raise EventRejected(
            f"У {name} не осталось оборудования для заявки {label}: утром бригада взяла "
            f"{state.engineer.equipment_stock} ед., и все они уже розданы."
        )
    visit = alone.visits[0]
    # Без новых нарушений обеду места нет: время визита названо с учётом обеда.
    lunch_note = " и с учётом обеда" if alone.lunch_conflict else ""
    raise EventRejected(
        f"{name} не успевает к заявке {label} даже без других заявок{lunch_note}: начнёт не раньше "
        f"{fmt_hhmm(visit.start)}, окно {fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}, "
        f"смена до {fmt_hhmm(state.available_until)}."
    )


def _engineer_unavailable(day: _Day, event: Event, ctx: PlanningContext) -> Facts:
    engineer = day.engineer(event.engineer_id)
    if not engineer.available:
        raise EventRejected(f"{engineer.name} уже недоступен с {fmt_hhmm(engineer.unavailable_from or 0)}.")
    engineer.available = False
    engineer.unavailable_from = event.time
    return day.facts(event)


def _urgent(day: _Day, event: Event, ctx: PlanningContext) -> Facts:
    """Срочная заявка диспетчера ждёт наравне с аварией: «Срочная» ставит её в верхнюю очередь распределения
    (dispatch_order) при любом типе работ. Уровень — род работ, и его, какой бы ни прислал клиент, сервер берёт
    по типу заявки BK, как у заявки дня того же типа: срочное подключение остаётся подключением. Заявка без типа
    работ из таблицы нормативов (старый диалог, чат) — авария."""
    now = event.time
    urgent = event.request
    assert urgent is not None  # валидатор Event: у срочной заявки request есть всегда
    new = urgent.model_copy(
        update={
            "priority": Priority.URGENT,
            "tier": ctx.tier_by_bk.get(urgent.source_type_bk, RequestTier.EMERGENCY),
            "status": RequestStatus.ACTIVE,
            "fixed_engineer_id": None,
        }
    )
    taken = next((request for request in day.requests if request.id == new.id), None)
    if taken is not None:
        raise EventRejected(f"Заявка с номером {request_label(taken.id, taken.priority)} уже есть в плане.")
    if new.asap:
        # Окно из запроса не используется: заявка ждёт с времени события до конца смен.
        new = new.model_copy(update=asap_window(day.engineers, now))
    if new.window_end < now:
        raise EventRejected(
            f"Окно срочной заявки заканчивается в {fmt_hhmm(new.window_end)}, это раньше времени события "
            f"{fmt_hhmm(now)}."
        )
    new = _located(new, ctx)
    day.requests.append(new)
    return day.facts(event.model_copy(update={"request": new}))


def _client_agreed(day: _Day, event: Event, ctx: PlanningContext) -> Facts:
    """«Коммуникация»: диспетчер позвонил клиенту и договорился.

    Окно, которое клиенту назвали, становится окном заявки: решатель обязан его держать, а вкладка «Коммуникации»
    дальше сравнивает план с ним. Окна нет — клиенту сказали, что сегодня не приедем: заявка переносится, решатель
    её не получает, а план держит её среди заявок без инженера (app/solvers/problem.py). Названное окно у
    перенесённой заявки возвращает её в работу дня: клиент перезвонил, договорились заново. «Клиент отказался» —
    это не договорённость, а обычная отмена (cancel).
    """
    now = event.time
    request = day.request(event.request_id)
    label = request_label(request.id, request.priority)
    if request.status == RequestStatus.CANCELLED:
        raise EventRejected(f"Заявка {label} отменена, договариваться с клиентом не о чем.")
    started = day.started_text(request, "перенести")
    if started is not None:
        raise EventRejected(started)
    window = event.agreed_window
    if window is None:
        request.status = RequestStatus.POSTPONED
        return day.facts(event, released=[request.id])
    if window.asap:
        # Окно «как можно скорее» клиенту называют словами, а задаёт его сервер, как у изменения заявки: заявка
        # «как можно скорее» держит своё (часы её ожидания не перезапускаются), обычная ждёт с этого звонка до
        # конца смен. Присланные начало и конец не используются, а в событие записывается окно, которое получила
        # заявка: его вкладка «Коммуникации» и считает известным клиенту.
        bounds = (
            {"window_start": request.window_start, "window_end": request.window_end}
            if request.asap
            else asap_window(day.engineers, now)
        )
        window = TimeWindow(start=bounds["window_start"], end=bounds["window_end"], asap=True)
    elif window.end < now:
        raise EventRejected(
            f"Окно {fmt_hhmm(window.start)}–{fmt_hhmm(window.end)} для заявки {label} заканчивается "
            f"раньше времени события {fmt_hhmm(now)}."
        )
    elif window.end <= window.start:
        raise EventRejected(window_order_text(label))
    request.window_start, request.window_end, request.asap = window.start, window.end, window.asap
    request.status = RequestStatus.ACTIVE
    # С новым окном заявку солвер планирует заново, даже если инженер уже едет к ней, как у изменённой заявки.
    return day.facts(event.model_copy(update={"agreed_window": window}), released=[request.id])


@dataclass(frozen=True)
class _Kind:
    """Тип события для всего сервиса: обработчик фактов и то, что событие называет.

    names_engineer и names_request — событие называет инженера (engineer_id) и заявку (request_id) дня: их номера
    шкала проверяет до пересчёта. new_request — событие добавляет заявку (request), edited_request — присылает
    заявку с желаемыми значениями. Два случая, когда выбрать бригаду заявке события в окне выбора нельзя:
    ends_request — событие снимает заявку с плана (отдавать некому), pins_request — событие само закрепляет её
    за названной бригадой (переназначение): отдать её другой бригаде значило бы спорить с событием — на шкале
    заявка ушла бы одной бригаде, а в плане стояла бы у другой. agreement — событие записывает договорённость
    с клиентом («Коммуникация»): из таких событий вкладка «Коммуникации» знает, что клиентам уже сказали.
    """

    apply: Callable[[_Day, Event, PlanningContext], Facts]
    names_engineer: bool = False
    names_request: bool = False
    new_request: bool = False
    edited_request: bool = False
    ends_request: bool = False
    pins_request: bool = False
    agreement: bool = False


_KINDS: dict[EventType, _Kind] = {
    EventType.URGENT: _Kind(_urgent, new_request=True),
    EventType.CANCEL: _Kind(_cancel, names_request=True, ends_request=True),
    EventType.RESTORE: _Kind(_restore, names_request=True),
    EventType.ENGINEER_UNAVAILABLE: _Kind(_engineer_unavailable, names_engineer=True),
    EventType.ENGINEER_TRANSPORT_CHANGED: _Kind(_transport_changed, names_engineer=True),
    EventType.REQUEST_UPDATED: _Kind(_request_updated, names_request=True, edited_request=True),
    EventType.ENGINEER_DELAYED: _Kind(_engineer_delayed, names_engineer=True),
    EventType.REQUEST_REASSIGNED: _Kind(
        _request_reassigned, names_engineer=True, names_request=True, pins_request=True
    ),
    # «Сегодня не приедем» снимает заявку с плана, но решает это окно события, а не его тип: «отдать бригаде» у
    # перенесённой заявки отсекает assignable по заявкам после события.
    EventType.CLIENT_AGREED: _Kind(_client_agreed, names_request=True, agreement=True),
}


def event_facts(session: PlanningSession, event: Event, ctx: PlanningContext) -> Facts:
    """Факты дня после события: проверяет его и считает изменения. Бросает EventRejected; сессию не меняет."""
    # previous_transport, previous_request и previous_engineer_id заполняет только backend.
    sent = event.model_copy(
        update={"previous_transport": None, "previous_request": None, "previous_engineer_id": None}
    )
    day = _Day(
        requests=[request.model_copy() for request in session.requests],
        engineers=[engineer.model_copy() for engineer in session.engineers],
        plan=session.plan,
        started=_started_visits(session.plan, event.time),
    )
    facts = _KINDS[event.type].apply(day, sent, ctx)
    return replace(facts, subject_request_id=subject_request_id(facts.event))


# --- Что событие называет: номера для проверок шкалы и API -------------------------------------------------------


def named_engineer(event: Event) -> str | None:
    """Инженер дня, которого называет событие; None — событие не об инженере."""
    return event.engineer_id if _KINDS[event.type].names_engineer else None


def named_request(event: Event) -> str | None:
    """Заявка дня, которую называет событие по номеру; None — такой нет (в том числе у срочной заявки)."""
    return event.request_id if _KINDS[event.type].names_request else None


def new_request(event: Event) -> Request | None:
    """Заявка, которую событие добавляет в день (срочная заявка диспетчера)."""
    return event.request if _KINDS[event.type].new_request else None


def edited_request(event: Event) -> Request | None:
    """Заявка с желаемыми значениями, которую присылает изменение заявки."""
    return event.request if _KINDS[event.type].edited_request else None


def subject_request_id(event: Event) -> str | None:
    """Заявка, о которой событие, если оно об одной заявке: названная по номеру или новая. Иначе None."""
    added = new_request(event)
    return added.id if added is not None else named_request(event)


def assign_allowed(event: Event) -> bool:
    """Стратегию «отдать бригаде» допускает само событие: оно об одной заявке, не снимает её с плана и бригаду
    ей не называет.

    Это проверка по одному событию — там, где заявок после события ещё нет (на входе API). Останется ли заявка
    в плане на самом деле, решают они (assignable): её могли отменить раньше по времени.
    """
    kind = _KINDS[event.type]
    return subject_request_id(event) is not None and not kind.ends_request and not kind.pins_request


def assignable(event: Event, requests: Iterable[Request]) -> bool:
    """Диспетчер может отдать заявку события выбранной бригаде (стратегия «assign:<инженер>»).

    requests — заявки дня после события. Кроме assign_allowed, заявка должна после события оставаться в работе дня
    (RequestStatus.ACTIVE): отменённую — самим событием или раньше по времени — отдавать некому.
    """
    subject = subject_request_id(event)
    return assign_allowed(event) and any(
        request.id == subject and request.status == RequestStatus.ACTIVE for request in requests
    )


@dataclass(frozen=True)
class Agreement:
    """Что клиенту сказали по телефону: окно по заявке request_id или None — сегодня не приедем."""

    request_id: str
    window: TimeWindow | None


def agreement(event: Event) -> Agreement | None:
    """Договорённость с клиентом, которую записывает событие («Коммуникация»); None — событие не об этом."""
    if not _KINDS[event.type].agreement or event.request_id is None:
        return None
    return Agreement(event.request_id, event.agreed_window)
