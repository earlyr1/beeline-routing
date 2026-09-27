"""Прогон маршрута инженера по времени: единая проверка всех ограничений, включая обед."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from app.domain.enums import SKILL_RU, TRANSPORT_RU, RequestStatus
from app.domain.models import LUNCH_MIN, Lunch, Visit
from app.domain.timeutil import fmt_hhmm
from app.solvers.problem import EngineerState, Problem

# Заявка вне работы дня в маршруте — нарушение: ни решатель, ни «Ничего не менять» таких не получают.
_INACTIVE_TEXT = {RequestStatus.CANCELLED: "заявка отменена", RequestStatus.POSTPONED: "заявка перенесена"}


@dataclass
class SimResult:
    visits: list[Visit]
    violations: list[str]
    end_node: int
    end_time: int  # окончание последнего визита: обед после него сюда не входит
    lunch: Lunch | None = None
    # Обед не встаёт без новых нарушений: он стоит там, где меньше всего задерживает клиентов (добавленные
    # опоздания — в нарушениях визитов), или в окне обеда ему нет места вовсе.
    lunch_conflict: bool = False

    @property
    def feasible(self) -> bool:
        return not self.violations


def simulate_route(
    problem: Problem,
    state: EngineerState,
    request_ids: Sequence[str],
    *,
    lunch: bool = True,
    not_before: Mapping[str, int] | None = None,
) -> SimResult:
    """Прогон визитов в заданном порядке. Если инженеру нужен обед (Problem.lunch_window), он ставится между визитами.

    Места для обеда: перед каждым визитом и после последнего. Обед берётся там, где инженер освободился (у прошлого
    визита или в точке старта), с момента, когда он свободен, но не раньше начала окна обеда; дорога к следующему
    визиту идёт после обеда. Место, где начало обеда позже окна, не подходит. Берётся первое по маршруту место, где
    обед не добавляет нарушений к прогону без обеда, и прежде всего место, где обед не сдвигает визиты (ожидания
    перед визитом хватает на обед). Если такого места нет, бригада всё равно пообедает: обед встаёт туда, где
    добавляет меньше всего минут опоздания, затем переработки, затем нарушений, при равенстве — раньше по маршруту,
    и прогон помечается lunch_conflict. Опоздания и переработка из-за обеда остаются нарушениями визитов, отдельной
    записи про обед нет. Запись «не помещается обед» — только если в окне обеда места нет вовсе. Обед ставится,
    только если у инженера есть визиты: в самом маршруте или закреплённые до события. lunch=False прогоняет маршрут
    без обеда, как план диспетчеров.

    not_before — время, раньше которого визит не начинают, по номерам заявок: так «Ничего не менять» держит
    времена, которые уже назвали клиентам. Приехав раньше, инженер ждёт; позже — визит идёт как обычно.
    """
    plain = _drive(problem, state, request_ids, not_before=not_before)
    window = problem.lunch_window(state) if lunch else None
    if window is None or not (request_ids or problem.pinned.get(state.engineer.id)):
        return plain
    earliest, latest = window
    starts = [visit.start for visit in plain.visits]
    free = [state.available_from] + [visit.end for visit in plain.visits]
    known = set(plain.violations)
    runs: list[SimResult] = []
    clean: SimResult | None = None
    for position in range(len(request_ids) + 1):
        if max(free[position], earliest) > latest:
            continue
        run = _drive(problem, state, request_ids, lunch_at=position, earliest=earliest, not_before=not_before)
        if known.issuperset(run.violations):
            if [visit.start for visit in run.visits] == starts:
                return run
            clean = clean or run
        runs.append(run)
    if clean is not None:
        return clean
    if not request_ids:
        # Новых визитов нет, а обед после закреплённой работы не помещается в смену: это не нарушение.
        return plain
    if runs:
        # min берёт первое из равных, то есть место раньше по маршруту.
        return replace(min(runs, key=lambda run: _lunch_cost(run, state)), lunch_conflict=True)
    # lunch_window не даёт окна, которое прошло к началу маршрута, поэтому место перед первым визитом есть всегда;
    # запись остаётся на случай, если это правило окна обеда поменяется.
    text = (
        f"у {state.engineer.name} не помещается обед {LUNCH_MIN} мин "
        f"с началом {fmt_hhmm(earliest)}–{fmt_hhmm(latest)}"
    )
    return replace(plain, violations=[*plain.violations, text], lunch_conflict=True)


def _lunch_cost(run: SimResult, state: EngineerState) -> tuple[int, int, int]:
    """Во что маршруту обходится обед на этом месте: минуты опоздания, переработки и число нарушений."""
    finish = run.end_time if run.lunch is None else max(run.end_time, run.lunch.end)
    late = sum(visit.late_min for visit in run.visits)
    return late, max(0, finish - state.available_until), len(run.violations)


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
    not_before: Mapping[str, int] | None = None,
) -> SimResult:
    """Прогон без выбора места: обед перед визитом с номером lunch_at, при lunch_at == len(request_ids) после последнего."""
    held = not_before or {}
    engineer = state.engineer
    limit_km = problem.leg_limit_km(engineer)
    node, clock = state.start_node, state.available_from
    visits: list[Visit] = []
    violations: list[str] = []
    lunch: Lunch | None = None
    equipment = 0  # единиц оборудования, разобранных маршрутом к этому визиту
    for position, request_id in enumerate(request_ids):
        request = problem.request(request_id)
        destination = problem.request_node(request_id)
        leg_min = problem.travel_min(node, destination, engineer)
        leg_km = problem.travel_km(node, destination, engineer)
        if position == lunch_at:
            lunch = _lunch_from(clock, earliest)
            clock = lunch.end
        arrival = clock + leg_min
        # Приехав раньше обещанного клиенту времени, инженер ждёт: клиента дома может ещё не быть.
        start = max(arrival, request.window_start, held.get(request_id, 0))
        late = max(0, start - request.window_end)
        end = start + request.duration_min
        if request.status != RequestStatus.ACTIVE:
            violations.append(f"{request_id}: {_INACTIVE_TEXT[request.status]}")
        if request.skill not in engineer.skills:
            violations.append(f"{request_id}: у {engineer.name} нет навыка «{SKILL_RU[request.skill]}»")
        if request.transport_required is not None and request.transport_required != engineer.transport:
            violations.append(
                f"{request_id}: нужен транспорт «{TRANSPORT_RU[request.transport_required]}», "
                f"у {engineer.name} «{TRANSPORT_RU[engineer.transport]}»"
            )
        if request.needs_equipment:
            # Оборудование бригада взяла утром на весь день: новых единиц в маршруте взять негде.
            equipment += 1
            if equipment > state.equipment_left:
                violations.append(
                    f"{request_id}: у {engineer.name} не осталось оборудования — в маршруте "
                    f"{equipment} заявок с оборудованием, а с собой {state.equipment_left} ед."
                )
        if problem.leg_too_long(node, destination, engineer):
            violations.append(
                f"{request_id}: плечо {leg_km:.1f} км длиннее предела {limit_km:g} км "
                f"для транспорта «{TRANSPORT_RU[engineer.transport]}»"
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
