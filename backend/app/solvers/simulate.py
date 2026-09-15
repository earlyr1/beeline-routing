"""Прогон маршрута инженера по времени: единая проверка всех ограничений, включая обед."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.domain.enums import SKILL_RU, TRANSPORT_RU, RequestStatus
from app.domain.models import LUNCH_MIN, Lunch, Visit
from app.domain.timeutil import fmt_hhmm
from app.solvers.problem import EngineerState, Problem


@dataclass
class SimResult:
    visits: list[Visit]
    violations: list[str]
    end_node: int
    end_time: int  # окончание последнего визита: обед после него сюда не входит
    lunch: Lunch | None = None
    # Без обеда маршрут прошёл бы проверку: не помещается именно обед.
    lunch_conflict: bool = False

    @property
    def feasible(self) -> bool:
        return not self.violations


def simulate_route(
    problem: Problem, state: EngineerState, request_ids: Sequence[str], *, lunch: bool = True
) -> SimResult:
    """Прогон визитов в заданном порядке. Если инженеру нужен обед (Problem.lunch_window), он ставится между визитами.

    Места для обеда: перед каждым визитом и после последнего. Обед берётся там, где инженер освободился (у прошлого
    визита или в точке старта), с момента, когда он свободен, но не раньше начала окна обеда; дорога к следующему
    визиту идёт после обеда. Место, где начало обеда позже окна, не подходит. Берётся первое по маршруту место, где
    маршрут допустим, и прежде всего место, где обед не сдвигает визиты (ожидания перед визитом хватает на обед).
    Если обед нужен, а допустимого места нет, маршрут недопустим. Обед ставится, только если у инженера есть визиты:
    в самом маршруте или закреплённые до события. lunch=False прогоняет маршрут без обеда, как план диспетчеров.
    """
    plain = _drive(problem, state, request_ids)
    window = problem.lunch_window(state) if lunch else None
    if window is None or not (request_ids or problem.pinned.get(state.engineer.id)):
        return plain
    earliest, latest = window
    starts = [visit.start for visit in plain.visits]
    free = [state.available_from] + [visit.end for visit in plain.visits]
    first: SimResult | None = None
    shifted: SimResult | None = None
    unshifted: SimResult | None = None
    for position in range(len(request_ids) + 1):
        if max(free[position], earliest) > latest:
            continue
        run = _drive(problem, state, request_ids, lunch_at=position, earliest=earliest)
        keeps_times = [visit.start for visit in run.visits] == starts
        if run.feasible and keeps_times:
            return run
        first = first or run
        if run.feasible:
            shifted = shifted or run
        if keeps_times:
            unshifted = unshifted or run
    if shifted is not None:
        return shifted
    if not request_ids:
        # Новых визитов нет, а обед после закреплённой работы не помещается в смену: это не нарушение.
        return plain
    if not plain.feasible:
        # Маршрут недопустим и без обеда: причины и время прежние.
        return unshifted or plain
    chosen = unshifted or first or plain
    text = (
        f"у {state.engineer.name} не помещается обед {LUNCH_MIN} мин "
        f"с началом {fmt_hhmm(earliest)}–{fmt_hhmm(latest)}"
    )
    return replace(chosen, violations=[*chosen.violations, text], lunch_conflict=True)


def _lunch_from(free: int, earliest: int) -> Lunch:
    begin = max(free, earliest)
    return Lunch(start=begin, end=begin + LUNCH_MIN)


def _drive(
    problem: Problem,
    state: EngineerState,
    request_ids: Sequence[str],
    *,
    lunch_at: int | None = None,
    earliest: int = 0,
) -> SimResult:
    """Прогон без выбора места: обед перед визитом с номером lunch_at, при lunch_at == len(request_ids) после последнего."""
    engineer = state.engineer
    node, clock = state.start_node, state.available_from
    visits: list[Visit] = []
    violations: list[str] = []
    lunch: Lunch | None = None
    for position, request_id in enumerate(request_ids):
        request = problem.request(request_id)
        destination = problem.request_node(request_id)
        leg_min = problem.travel_min(node, destination, engineer)
        leg_km = problem.travel_km(node, destination, engineer)
        if position == lunch_at:
            lunch = _lunch_from(clock, earliest)
            clock = lunch.end
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
    if lunch_at == len(request_ids):
        lunch = _lunch_from(clock, earliest)
    if lunch is not None and lunch.end > state.available_until:
        violations.append(
            f"обед {fmt_hhmm(lunch.start)}–{fmt_hhmm(lunch.end)} позже конца смены {fmt_hhmm(state.available_until)}"
        )
    return SimResult(visits=visits, violations=violations, end_node=node, end_time=clock, lunch=lunch)
