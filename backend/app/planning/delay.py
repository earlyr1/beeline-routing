"""Задержка инженера: сдвиг его дня в задаче перепланирования и прогноз опозданий без пересчёта плана.

Задержка решается по маршруту инженера в текущем плане на время события. Функции работают с задачей, в которой
pin_problem уже закрепил начатую работу и визит в пути; остальных инженеров задержка не касается.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from app.domain.enums import EventType
from app.domain.models import Event, Plan, Visit
from app.planning.models import DelayForecast, LateVisit
from app.solvers.problem import EngineerState, Problem
from app.solvers.simulate import simulate_route


def delayed_until(events: Iterable[Event]) -> dict[str, int]:
    """Для каждого задержанного инженера самое позднее время T + N среди его задержек."""
    until: dict[str, int] = {}
    for event in events:
        if event.type == EventType.ENGINEER_DELAYED and event.engineer_id and event.delay_min is not None:
            until[event.engineer_id] = max(until.get(event.engineer_id, 0), event.time + event.delay_min)
    return until


def _ready_from(state: EngineerState, time: int) -> EngineerState:
    if time <= state.available_from:
        return state
    return replace(state, available_from=time)


def keep_delays(problem: Problem, until: dict[str, int]) -> Problem:
    """Задержки прежних событий продолжают действовать: новый выезд инженера не раньше T + N.

    pin_problem отпускает визиты, к которым инженер ещё не выехал. Без этой поправки следующее событие дня
    (например, отмена чужой заявки) вернуло бы задержанному инженеру доступность с текущего времени.
    Начатая работа и визит в пути уже сдвинуты в самом плане, для них поправка ничего не меняет.
    """
    states = [_ready_from(state, until.get(state.engineer.id, 0)) for state in problem.states]
    if all(new is old for new, old in zip(states, problem.states, strict=True)):
        return problem
    return replace(problem, states=states)


def _affected_visit(problem: Problem, engineer_id: str) -> Visit | None:
    """Визит, который сдвигает задержка: работа на объекте идёт или инженер к нему едет."""
    kept = problem.pinned.get(engineer_id, [])
    if not kept:
        return None
    last = kept[-1]
    on_site = last.start < problem.now < last.end
    on_the_way = not last.pinned  # у визита в пути работа не начата, pin_problem оставляет его pinned=False
    return last if on_site or on_the_way else None


def _delayed(problem: Problem, visit: Visit, delay_min: int) -> Visit:
    """На объекте работа заканчивается позже; в пути инженер позже приезжает и начинает не раньше окна."""
    if visit.start < problem.now:
        return visit.model_copy(update={"end": visit.end + delay_min})
    request = problem.request(visit.request_id)
    arrival = visit.arrival + delay_min
    start = max(arrival, request.window_start)
    return visit.model_copy(update={"arrival": arrival, "start": start, "end": start + request.duration_min})


def missed_hold(problem: Problem, engineer_id: str, delay_min: int) -> str | None:
    """Заявка, к которой инженер едет, если с задержкой он начнёт работу позже конца её окна."""
    visit = _affected_visit(problem, engineer_id)
    if visit is None or visit.pinned:
        return None
    window_end = problem.request(visit.request_id).window_end
    return visit.request_id if _delayed(problem, visit, delay_min).start > window_end else None


def delay_engineer(problem: Problem, engineer_id: str, delay_min: int) -> Problem:
    """Сдвигает день инженера на delay_min минут.

    На объекте: окончание начатого визита позже на delay_min. В пути: прибытие позже на delay_min, начало не
    раньше начала окна, окончание через длительность работы. В обоих случаях инженер продолжает от этого визита
    с нового окончания. Визит в пути, который с задержкой не успевает в окно, до вызова снимается с удержания
    (missed_hold и pin_problem с released). В остальных случаях инженер свободен не раньше now + delay_min.
    Если это время не раньше конца смены, новых визитов у инженера нет: солверы не берут инженера, у которого
    available_from не меньше available_until. Инженер при этом остаётся доступным, начатая работа сохраняется.
    """
    kept = list(problem.pinned.get(engineer_id, []))
    ready = problem.now + delay_min
    visit = _affected_visit(problem, engineer_id)
    if visit is not None:
        kept[-1] = _delayed(problem, visit, delay_min)
        ready = max(ready, kept[-1].end)
    states = [
        _ready_from(state, ready) if state.engineer.id == engineer_id else state for state in problem.states
    ]
    return replace(problem, states=states, pinned={**problem.pinned, engineer_id: kept})


def forecast_delay(problem: Problem, previous: Plan, engineer_id: str, delay_min: int) -> DelayForecast:
    """Прогноз без перепланирования для задачи после delay_engineer.

    Оставшиеся визиты инженера из прежнего плана (после закреплённой или удерживаемой в пути части) проходят
    в том же порядке с задержанного места и времени нестрогим прогоном simulate_route. В списке опозданий каждый
    визит, который начнётся позже конца окна. Переработка считается по окончанию последнего визита дня: из
    прогноза, а если оставшихся визитов нет, по закреплённому визиту.
    """
    state = problem.state(engineer_id)
    kept = problem.pinned.get(engineer_id, [])
    kept_ids = {visit.request_id for visit in kept}
    route = next((route for route in previous.routes if route.engineer_id == engineer_id), None)
    remaining = [visit for visit in (route.visits if route else []) if visit.request_id not in kept_ids]
    forecast = simulate_route(problem, state, [visit.request_id for visit in remaining]).visits
    late = [
        LateVisit(
            request_id=planned.request_id,
            planned_start=planned.start,
            forecast_start=visit.start,
            late_min=visit.late_min,
        )
        for planned, visit in zip(remaining, forecast, strict=True)
        if visit.late_min > 0
    ]
    day = kept + forecast
    overtime = max(0, day[-1].end - state.engineer.shift_end) if day else 0
    return DelayForecast(
        engineer_id=engineer_id,
        delay_min=delay_min,
        late_without_replan=late,
        overtime_without_replan_min=overtime,
    )
