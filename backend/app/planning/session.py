"""Состояние планирования одного датасета и применение событий дня."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app.domain.enums import TRANSPORT_RU, EventType, Priority, RequestStatus
from app.domain.models import Engineer, Event, Lunch, Office, Plan, Request, Visit
from app.domain.timeutil import fmt_hhmm
from app.geo.kvcache import KVCache
from app.geo.matrix import TrafficProfile, TravelModel
from app.geo.osrm import OsrmClient
from app.ingest.geocode import GeoResult
from app.planning.delay import delay_engineer, delayed_until, forecast_delay, keep_delays, missed_hold
from app.planning.diff import compute_diff
from app.planning.models import AppliedEvent, PlanDiff
from app.planning.workload import DEFAULT_WORKLOAD_LEVEL, travel_buffer, workload_weights
from app.settings import DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S, DEFAULT_SOLVER_TIME_LIMIT_S
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import OrToolsSolver
from app.solvers.problem import EngineerState, Problem, make_problem


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


@dataclass
class PlanningContext:
    model: TravelModel
    traffic: TrafficProfile
    osrm: OsrmClient | None = None
    cache: KVCache | None = None
    # Лимит OR-Tools на день без обеда и на перепланирование по событию.
    time_limit_s: int = DEFAULT_SOLVER_TIME_LIMIT_S
    # Лимит OR-Tools на весь день с обедом.
    time_limit_lunch_s: int = DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S
    geocode: Callable[[str, str], GeoResult] | None = None

    def day_time_limit_s(self, lunch_enabled: bool) -> int:
        """Лимит на план всего дня с нуля: с обедом поиск дольше, без обеда как у перепланирования."""
        return self.time_limit_lunch_s if lunch_enabled else self.time_limit_s


@dataclass(frozen=True)
class PlanningSession:
    dataset_id: str
    region: str
    office: Office
    requests: list[Request]
    engineers: list[Engineer]
    control: Plan | None
    problem: Problem
    plan: Plan
    baseline: Plan
    previous_plan: Plan | None = None
    last_diff: PlanDiff | None = None
    events: list[AppliedEvent] = field(default_factory=list)
    now: int = 0
    version: int = 1
    # Уровень нагрузки дня (app/planning/workload.py): веса OR-Tools и запас на дорогу во всех решениях сессии.
    workload_level: int = DEFAULT_WORKLOAD_LEVEL
    # Обед по плану, выбранный для дня: действует во всех решениях сессии, включая FCFS и перепланирование.
    lunch_enabled: bool = True

    def request(self, request_id: str) -> Request | None:
        return next((r for r in self.requests if r.id == request_id), None)

    def engineer(self, engineer_id: str) -> Engineer | None:
        return next((e for e in self.engineers if e.id == engineer_id), None)


def _day_problem(
    requests: list[Request],
    engineers: list[Engineer],
    ctx: PlanningContext,
    workload_level: int,
    lunch_enabled: bool,
) -> Problem:
    """Задача на начало дня с запасом на дорогу уровня нагрузки и выбранным обедом.

    Базовая матрица берётся из кэша, если уже была.
    """
    return make_problem(
        requests,
        engineers,
        model=ctx.model,
        traffic=ctx.traffic,
        osrm=ctx.osrm,
        cache=ctx.cache,
        buffer=travel_buffer(workload_level),
        lunch=lunch_enabled,
    )


def _solve(problem: Problem, workload_level: int, time_limit_s: int) -> tuple[Plan, Plan]:
    """Оптимизированный план с весами уровня нагрузки и базовый FCFS. Стоимость инженера FCFS не использует."""
    optimizer = OrToolsSolver(time_limit_s=time_limit_s, weights=workload_weights(workload_level))
    return optimizer.solve(problem), FcfsSolver().solve(problem)


def start_session(
    dataset_id: str,
    region: str,
    office: Office,
    requests: list[Request],
    engineers: list[Engineer],
    control: Plan | None,
    ctx: PlanningContext,
    *,
    workload_level: int = DEFAULT_WORKLOAD_LEVEL,
    lunch_enabled: bool = True,
) -> PlanningSession:
    """План всего дня с нуля: предподсчёт загрузки и пересборка дня. Лимит OR-Tools зависит от обеда."""
    problem = _day_problem(requests, engineers, ctx, workload_level, lunch_enabled)
    plan, baseline = _solve(problem, workload_level, ctx.day_time_limit_s(lunch_enabled))
    return PlanningSession(
        dataset_id=dataset_id,
        region=region,
        office=office,
        requests=list(requests),
        engineers=list(engineers),
        control=control,
        problem=problem,
        plan=plan,
        baseline=baseline,
        workload_level=workload_level,
        lunch_enabled=lunch_enabled,
    )


def _on_the_way(problem: Problem, state: EngineerState, visit: Visit, now: int, open_ids: set[str]) -> bool:
    """Инженер выехал к визиту раньше now, и выезд в now задержал бы начало работы.

    Отменённая заявка и недоступный инженер не закрепляются: визит возвращается в пул.
    """
    if visit.request_id not in open_ids or not state.engineer.available or state.available_until <= now:
        return False
    if visit.arrival - visit.leg_min >= now:
        return False
    window_start = problem.request(visit.request_id).window_start
    return max(now + visit.leg_min, window_start) > visit.start


def pin_problem(problem: Problem, plan: Plan, now: int, released: Collection[str] = ()) -> Problem:
    """Закрепляет визиты, начатые до now, и визит, к которому инженер уже едет.

    Инженер продолжает день из точки последнего закреплённого визита в его время окончания.
    Visit.pinned остаётся True только у начатой работы: для диспетчера «закреплена» значит «уже
    в работе, отменить нельзя». Визит в пути солвер не трогает, но отменить его можно до начала работы.
    Визит в пути к заявке из released (её только что изменили) не удерживается: солвер решает заново.
    Обед, начатый до now, остаётся как в прежнем плане (в том числе у инженера, который стал недоступен), и новый
    обед инженеру уже не нужен. Инженер на обеде свободен не раньше конца обеда.
    """
    routes = {route.engineer_id: route for route in plan.routes}
    open_ids = set(problem.open_request_ids)
    pinned: dict[str, list[Visit]] = {}
    pinned_lunch: dict[str, Lunch] = {}
    previous_assignment: dict[str, str] = {}
    previous_order: dict[str, list[str]] = {}
    pinned_ids: set[str] = set()
    states: list[EngineerState] = []
    for state in problem.states:
        engineer_id = state.engineer.id
        route = routes.get(engineer_id)
        visits = route.visits if route else []
        lunch = route.lunch if route and route.lunch and route.lunch.start < now else None
        done = [visit for visit in visits if visit.start < now]
        upcoming = [visit for visit in visits if visit.start >= now]
        if (
            upcoming
            and upcoming[0].request_id not in released
            and _on_the_way(problem, state, upcoming[0], now, open_ids)
        ):
            done.append(upcoming.pop(0))
        rest = [visit.request_id for visit in upcoming]
        start_node, available_from = state.start_node, max(state.available_from, now)
        if done:
            start_node = problem.request_node(done[-1].request_id)
            available_from = max(available_from, done[-1].end)
        if lunch is not None:
            pinned_lunch[engineer_id] = lunch
            available_from = max(available_from, lunch.end)
        pinned[engineer_id] = [visit.model_copy(update={"pinned": visit.start < now}) for visit in done]
        pinned_ids.update(visit.request_id for visit in done)
        previous_order[engineer_id] = rest
        previous_assignment.update({request_id: engineer_id for request_id in rest})
        states.append(EngineerState(state.engineer, start_node, available_from, state.available_until))
    return replace(
        problem,
        states=states,
        open_request_ids=[rid for rid in problem.open_request_ids if rid not in pinned_ids],
        pinned=pinned,
        pinned_lunch=pinned_lunch,
        previous_assignment=previous_assignment,
        previous_order=previous_order,
        now=now,
    )


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
    sent = event.request
    if event.type != EventType.REQUEST_UPDATED or sent is None:
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
    sent = event.request
    if sent is None or ctx.geocode is None:
        return event, {}
    if event.type == EventType.URGENT:
        located = _located(sent, ctx)
        return (event if located is sent else event.model_copy(update={"request": located})), {}
    known = requests.get(event.request_id or "") if event.type == EventType.REQUEST_UPDATED else None
    if known is None or _has_point(sent):
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


def _started_visits(plan: Plan, now: int) -> dict[str, Visit]:
    return {visit.request_id: visit for route in plan.routes for visit in route.visits if visit.start < now}


def _find_engineer(engineers: list[Engineer], engineer_id: str | None) -> Engineer:
    engineer = next((e for e in engineers if e.id == engineer_id), None)
    if engineer is None:
        raise EventRejected(f"Инженер {engineer_id} не найден.")
    return engineer


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


def window_order_text(request_id: str) -> str:
    return f"Конец окна заявки {request_id} должен быть позже начала."


def _apply_to_inputs(
    session: PlanningSession, event: Event, ctx: PlanningContext
) -> tuple[list[Request], list[Engineer], Event]:
    now = event.time
    # previous_transport и previous_request заполняет только backend.
    event = event.model_copy(update={"previous_transport": None, "previous_request": None})
    requests = [request.model_copy() for request in session.requests]
    engineers = [engineer.model_copy() for engineer in session.engineers]
    by_id = {request.id: request for request in requests}
    started = _started_visits(session.plan, now)

    if event.type == EventType.REQUEST_UPDATED:
        index = next((k for k, request in enumerate(requests) if request.id == event.request_id), None)
        if index is None:
            raise EventRejected(f"Заявка {event.request_id} не найдена.")
        stored, sent = requests[index], event.request
        if stored.id in started:
            raise EventRejected(
                f"Заявка {stored.id} уже в работе с {fmt_hhmm(started[stored.id].start)}, изменить её нельзя."
            )
        if not sent.asap:
            if sent.window_end < now:
                raise EventRejected(
                    f"Окно заявки {stored.id} заканчивается в {fmt_hhmm(sent.window_end)}, это раньше времени "
                    f"события {fmt_hhmm(now)}."
                )
            if sent.window_end <= sent.window_start:
                raise EventRejected(window_order_text(stored.id))
            window = {}
        elif stored.asap:
            # Заявка остаётся «как можно скорее»: часы ожидания не перезапускаются, окно из запроса не используется.
            window = {"window_start": stored.window_start, "window_end": stored.window_end}
        else:
            # Заявка стала «как можно скорее»: часы ожидания идут с этого события.
            window = asap_window(engineers, now)
        changes = {**sent.model_dump(include=EDITABLE_REQUEST_FIELDS), **window}
        merged = stored.model_copy(update={**changes, **_edited_location(stored, sent, ctx)})
        if merged == stored:
            raise EventRejected(f"В заявке {stored.id} ничего не изменилось.")
        requests[index] = merged
        return requests, engineers, event.model_copy(update={"request": merged, "previous_request": stored})

    if event.type in (EventType.CANCEL, EventType.RESTORE):
        request = by_id.get(event.request_id or "")
        if request is None:
            raise EventRejected(f"Заявка {event.request_id} не найдена.")
        if event.type == EventType.CANCEL:
            if request.status == RequestStatus.CANCELLED:
                raise EventRejected(f"Заявка {request.id} уже отменена.")
            if request.id in started:
                raise EventRejected(
                    f"Заявка {request.id} уже в работе с {fmt_hhmm(started[request.id].start)}, отменить её нельзя."
                )
            request.status = RequestStatus.CANCELLED
        else:
            if request.status != RequestStatus.CANCELLED:
                raise EventRejected(f"Заявка {request.id} не отменена, возвращать нечего.")
            if request.window_end < now and request.asap:
                raise EventRejected(
                    f"Заявка {request.id} как можно скорее с {fmt_hhmm(request.window_start)}: смены закончились "
                    f"в {fmt_hhmm(request.window_end)}, вернуть её в план нельзя."
                )
            if request.window_end < now:
                raise EventRejected(
                    f"Окно заявки {request.id} ({fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}) "
                    f"уже прошло, вернуть её в план нельзя."
                )
            request.status = RequestStatus.ACTIVE
        return requests, engineers, event

    if event.type == EventType.ENGINEER_DELAYED:
        engineer = _find_engineer(engineers, event.engineer_id)
        if not engineer.available:
            raise EventRejected(
                f"{engineer.name} недоступен с {fmt_hhmm(_unavailable_since(engineer))}, задержку поставить нельзя."
            )
        # Задержка меняет не инженера, а его маршрут: визиты и доступность сдвигает _pinned_problem.
        return requests, engineers, event

    if event.type == EventType.ENGINEER_TRANSPORT_CHANGED:
        engineer = _find_engineer(engineers, event.engineer_id)
        if not engineer.available:
            raise EventRejected(
                f"{engineer.name} недоступен с {fmt_hhmm(_unavailable_since(engineer))}, сменить транспорт нельзя."
            )
        if engineer.transport == event.transport:
            raise EventRejected(f"У {engineer.name} уже транспорт «{TRANSPORT_RU[engineer.transport]}».")
        # Закреплённые визиты сохраняют прежние время и пробег (pin_problem берёт их из текущего плана),
        # а все участки после них солвер считает по новому транспорту.
        previous = engineer.transport
        engineer.transport = event.transport
        return requests, engineers, event.model_copy(update={"previous_transport": previous})

    if event.type == EventType.ENGINEER_UNAVAILABLE:
        engineer = _find_engineer(engineers, event.engineer_id)
        if not engineer.available:
            raise EventRejected(
                f"{engineer.name} уже недоступен с {fmt_hhmm(engineer.unavailable_from or 0)}."
            )
        engineer.available = False
        engineer.unavailable_from = now
        return requests, engineers, event

    new = event.request.model_copy(update={"priority": Priority.URGENT, "status": RequestStatus.ACTIVE})
    if new.id in by_id:
        raise EventRejected(f"Заявка с номером {new.id} уже есть в плане.")
    if new.asap:
        # Окно из запроса не используется: заявка ждёт с времени события до конца смен.
        new = new.model_copy(update=asap_window(engineers, now))
    if new.window_end < now:
        raise EventRejected(
            f"Окно срочной заявки заканчивается в {fmt_hhmm(new.window_end)}, это раньше времени события "
            f"{fmt_hhmm(now)}."
        )
    new = _located(new, ctx)
    requests.append(new)
    return requests, engineers, event.model_copy(update={"request": new})


def _pinned_problem(base: Problem, session: PlanningSession, event: Event) -> Problem:
    """Задача на остаток дня после события: закреплённая работа, прежние задержки и задержка из самого события."""
    until = delayed_until(applied.event for applied in session.events)

    def pin(released: Collection[str]) -> Problem:
        return keep_delays(pin_problem(base, session.plan, event.time, released), until)

    if event.type == EventType.REQUEST_UPDATED:
        # Изменённую заявку солвер планирует заново, даже если инженер уже едет к ней.
        return pin([event.request_id])
    problem = pin(())
    if event.type != EventType.ENGINEER_DELAYED:
        return problem
    missed = missed_hold(problem, event.engineer_id, event.delay_min)
    if missed is not None:
        # С задержкой инженер не успеет в окно заявки, к которой едет: кому её отдать, решает солвер.
        problem = pin([missed])
    return delay_engineer(problem, event.engineer_id, event.delay_min)


def early_event_text(time: int, now: int) -> str:
    return f"Время события {fmt_hhmm(time)} раньше текущего времени плана {fmt_hhmm(now)}."


def _check_time(session: PlanningSession, event: Event) -> None:
    if event.time < session.now:
        raise EventRejected(early_event_text(event.time, session.now))


def check_event(session: PlanningSession, event: Event, ctx: PlanningContext) -> Event:
    """Проверяет событие против текущего состояния без пересчёта плана.

    Возвращает событие в том виде, в каком apply_event его сохранит (у срочной заявки появляются
    координаты из геокодера). Бросает EventRejected с текстом для диспетчера. Сессию не меняет.
    """
    _check_time(session, event)
    return _apply_to_inputs(session, event, ctx)[2]


def apply_event(
    session: PlanningSession, event: Event, ctx: PlanningContext, *, version: int | None = None
) -> PlanningSession:
    """Применяет одно событие дня и возвращает НОВУЮ сессию; входная не меняется.

    Бросает EventRejected, если событие противоречит текущему состоянию. Уровень нагрузки и обед остаются как в
    сессии. Лимит OR-Tools обычный и с обедом: перепланирование стартует от текущего плана. version — номер нового
    плана (у сессии и у применённого события); без него следующий за номером входной сессии.
    """
    _check_time(session, event)
    requests, engineers, stored_event = _apply_to_inputs(session, event, ctx)
    base = _day_problem(requests, engineers, ctx, session.workload_level, session.lunch_enabled)
    problem = _pinned_problem(base, session, stored_event)
    plan, baseline = _solve(problem, session.workload_level, ctx.time_limit_s)
    cancelled = {request.id for request in requests if request.status == RequestStatus.CANCELLED}
    diff = compute_diff(session.plan, plan, cancelled)
    if stored_event.type == EventType.ENGINEER_DELAYED:
        forecast = forecast_delay(problem, session.plan, stored_event.engineer_id, stored_event.delay_min)
        diff = diff.model_copy(update={"delay_forecast": forecast})
    version = session.version + 1 if version is None else version
    applied = AppliedEvent(id=f"ev_{len(session.events) + 1}", event=stored_event, version=version)
    return replace(
        session,
        requests=requests,
        engineers=engineers,
        problem=problem,
        plan=plan,
        baseline=baseline,
        previous_plan=session.plan,
        last_diff=diff,
        events=[*session.events, applied],
        now=event.time,
        version=version,
    )
