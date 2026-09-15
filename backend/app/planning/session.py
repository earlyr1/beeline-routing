"""Состояние планирования одного датасета и применение событий дня."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from app.domain.enums import TRANSPORT_RU, EventType, Priority, RequestStatus
from app.domain.models import Engineer, Event, Office, Plan, Request, Visit
from app.domain.timeutil import fmt_hhmm
from app.geo.kvcache import KVCache
from app.geo.matrix import TrafficProfile, TravelModel
from app.geo.osrm import OsrmClient
from app.ingest.geocode import GeoResult
from app.planning.diff import compute_diff
from app.planning.models import AppliedEvent, PlanDiff
from app.settings import DEFAULT_SOLVER_TIME_LIMIT_S
from app.solvers.fcfs import FcfsSolver
from app.solvers.ortools_solver import OrToolsSolver
from app.solvers.problem import EngineerState, Problem, make_problem


class EventRejected(ValueError):
    """Событие нельзя применить. Текст сообщения показывается диспетчеру (HTTP 422)."""


@dataclass
class PlanningContext:
    model: TravelModel
    traffic: TrafficProfile
    osrm: OsrmClient | None = None
    cache: KVCache | None = None
    time_limit_s: int = DEFAULT_SOLVER_TIME_LIMIT_S
    geocode: Callable[[str, str], GeoResult] | None = None


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

    def request(self, request_id: str) -> Request | None:
        return next((r for r in self.requests if r.id == request_id), None)

    def engineer(self, engineer_id: str) -> Engineer | None:
        return next((e for e in self.engineers if e.id == engineer_id), None)


def _solve(problem: Problem, ctx: PlanningContext) -> tuple[Plan, Plan]:
    return OrToolsSolver(time_limit_s=ctx.time_limit_s).solve(problem), FcfsSolver().solve(problem)


def start_session(
    dataset_id: str,
    region: str,
    office: Office,
    requests: list[Request],
    engineers: list[Engineer],
    control: Plan | None,
    ctx: PlanningContext,
) -> PlanningSession:
    problem = make_problem(
        requests, engineers, model=ctx.model, traffic=ctx.traffic, osrm=ctx.osrm, cache=ctx.cache
    )
    plan, baseline = _solve(problem, ctx)
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


def pin_problem(problem: Problem, plan: Plan, now: int) -> Problem:
    """Закрепляет визиты, начатые до now, и визит, к которому инженер уже едет.

    Инженер продолжает день из точки последнего закреплённого визита в его время окончания.
    Visit.pinned остаётся True только у начатой работы: для диспетчера «закреплена» значит «уже
    в работе, отменить нельзя». Визит в пути солвер не трогает, но отменить его можно до начала работы.
    """
    routes = {route.engineer_id: route for route in plan.routes}
    open_ids = set(problem.open_request_ids)
    pinned: dict[str, list[Visit]] = {}
    previous_assignment: dict[str, str] = {}
    previous_order: dict[str, list[str]] = {}
    pinned_ids: set[str] = set()
    states: list[EngineerState] = []
    for state in problem.states:
        engineer_id = state.engineer.id
        visits = routes[engineer_id].visits if engineer_id in routes else []
        done = [visit for visit in visits if visit.start < now]
        upcoming = [visit for visit in visits if visit.start >= now]
        if upcoming and _on_the_way(problem, state, upcoming[0], now, open_ids):
            done.append(upcoming.pop(0))
        rest = [visit.request_id for visit in upcoming]
        start_node, available_from = state.start_node, max(state.available_from, now)
        if done:
            start_node = problem.request_node(done[-1].request_id)
            available_from = max(available_from, done[-1].end)
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
        previous_assignment=previous_assignment,
        previous_order=previous_order,
        now=now,
    )


def _located(request: Request, ctx: PlanningContext) -> Request:
    if (request.lat is not None and request.lon is not None) or ctx.geocode is None:
        return request
    geo = ctx.geocode(request.address, request.district)
    return request.model_copy(update={"lat": geo.lat, "lon": geo.lon, "geocode_precision": geo.precision})


def geocode_urgent(event: Event, ctx: PlanningContext) -> Event:
    """Координаты срочной заявки по адресу, если их не передали. Другие события не меняются.

    Геокодер может отвечать долго, поэтому API вызывает функцию до блокировки датасета.
    """
    if event.type != EventType.URGENT or event.request is None:
        return event
    located = _located(event.request, ctx)
    return event if located is event.request else event.model_copy(update={"request": located})


def _started_visits(plan: Plan, now: int) -> dict[str, Visit]:
    return {visit.request_id: visit for route in plan.routes for visit in route.visits if visit.start < now}


def _find_engineer(engineers: list[Engineer], engineer_id: str | None) -> Engineer:
    engineer = next((e for e in engineers if e.id == engineer_id), None)
    if engineer is None:
        raise EventRejected(f"Инженер {engineer_id} не найден.")
    return engineer


def _apply_to_inputs(
    session: PlanningSession, event: Event, ctx: PlanningContext
) -> tuple[list[Request], list[Engineer], Event]:
    now = event.time
    requests = [request.model_copy() for request in session.requests]
    engineers = [engineer.model_copy() for engineer in session.engineers]
    by_id = {request.id: request for request in requests}
    started = _started_visits(session.plan, now)

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
            if request.window_end < now:
                raise EventRejected(
                    f"Окно заявки {request.id} ({fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}) "
                    f"уже прошло, вернуть её в план нельзя."
                )
            request.status = RequestStatus.ACTIVE
        return requests, engineers, event

    if event.type == EventType.ENGINEER_TRANSPORT_CHANGED:
        engineer = _find_engineer(engineers, event.engineer_id)
        if not engineer.available:
            since = (
                engineer.unavailable_from if engineer.unavailable_from is not None else engineer.shift_start
            )
            raise EventRejected(f"{engineer.name} недоступен с {fmt_hhmm(since)}, сменить транспорт нельзя.")
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
    if new.window_end < now:
        raise EventRejected(
            f"Окно срочной заявки заканчивается в {fmt_hhmm(new.window_end)}, это раньше времени события "
            f"{fmt_hhmm(now)}."
        )
    new = _located(new, ctx)
    requests.append(new)
    return requests, engineers, event.model_copy(update={"request": new})


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
    base = make_problem(
        requests, engineers, model=ctx.model, traffic=ctx.traffic, osrm=ctx.osrm, cache=ctx.cache
    )
    problem = pin_problem(base, session.plan, event.time)
    plan, baseline = _solve(problem, ctx)
    cancelled = {request.id for request in requests if request.status == RequestStatus.CANCELLED}
    version = session.version + 1
    applied = AppliedEvent(id=f"ev_{len(session.events) + 1}", event=stored_event, version=version)
    return replace(
        session,
        requests=requests,
        engineers=engineers,
        problem=problem,
        plan=plan,
        baseline=baseline,
        previous_plan=session.plan,
        last_diff=compute_diff(session.plan, plan, cancelled),
        events=[*session.events, applied],
        now=event.time,
        version=version,
    )
