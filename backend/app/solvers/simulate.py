"""Прогон маршрута инженера по времени: единая проверка всех ограничений."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.enums import SKILL_RU, TRANSPORT_RU, RequestStatus
from app.domain.models import Visit
from app.domain.timeutil import fmt_hhmm
from app.solvers.problem import EngineerState, Problem


@dataclass
class SimResult:
    visits: list[Visit]
    violations: list[str]
    end_node: int
    end_time: int

    @property
    def feasible(self) -> bool:
        return not self.violations


def simulate_route(problem: Problem, state: EngineerState, request_ids: Sequence[str]) -> SimResult:
    engineer = state.engineer
    node, clock = state.start_node, state.available_from
    visits: list[Visit] = []
    violations: list[str] = []
    for request_id in request_ids:
        request = problem.request(request_id)
        destination = problem.request_node(request_id)
        leg_min = problem.travel_min(node, destination, engineer)
        leg_km = problem.travel_km(node, destination, engineer)
        arrival = clock + leg_min
        start = max(arrival, request.window_start)
        late = max(0, start - request.window_end)
        end = start + request.duration_min
        if request.status != RequestStatus.ACTIVE:
            violations.append(f"{request_id}: заявка отменена")
        if request.skill not in engineer.skills:
            violations.append(f"{request_id}: у {engineer.name} нет навыка «{SKILL_RU[request.skill]}»")
        if request.transport_required is not None and request.transport_required != engineer.transport:
            violations.append(
                f"{request_id}: нужен транспорт «{TRANSPORT_RU[request.transport_required]}», "
                f"у {engineer.name} «{TRANSPORT_RU[engineer.transport]}»"
            )
        if late:
            violations.append(
                f"{request_id}: начало {fmt_hhmm(start)} позже окна до {fmt_hhmm(request.window_end)} на {late} мин"
            )
        if end > state.available_until:
            violations.append(
                f"{request_id}: окончание {fmt_hhmm(end)} позже конца смены {fmt_hhmm(state.available_until)}"
            )
        visits.append(
            Visit(
                request_id=request_id,
                arrival=arrival,
                start=start,
                end=end,
                leg_km=round(leg_km, 2),
                leg_min=leg_min,
                late_min=late,
            )
        )
        node, clock = destination, end
    return SimResult(visits=visits, violations=violations, end_node=node, end_time=clock)
