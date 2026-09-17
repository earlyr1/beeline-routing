"""Объяснение по заявке языком диспетчера: ограничения, альтернативы, факторы выбора."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import SKILL_RU, TRANSPORT_RU, Priority, RequestStatus
from app.domain.models import ASAP_FREE_WAIT_MIN, Engineer, Plan, Request, Visit
from app.domain.timeutil import fmt_hhmm
from app.planning.models import Alternative, ConstraintCheck, Explanation
from app.solvers.eligibility import Exclusion, exclusion
from app.solvers.ortools_solver import ObjectiveWeights
from app.solvers.problem import EngineerState, Problem
from app.solvers.simulate import simulate_route

KM_EPSILON = 0.05
ASAP_CHECK = "Как можно скорее"


@dataclass(frozen=True)
class Insertion:
    extra_km: float
    start: int


def _held_ids(problem: Problem, engineer_id: str) -> set[str]:
    """Визиты, которые солвер не трогает: начатая работа и визит, к которому инженер уже едет."""
    return {visit.request_id for visit in problem.pinned.get(engineer_id, [])}


def _open_sequence(problem: Problem, plan: Plan, engineer_id: str) -> list[str]:
    route = next((r for r in plan.routes if r.engineer_id == engineer_id), None)
    held = _held_ids(problem, engineer_id)
    return [visit.request_id for visit in route.visits if visit.request_id not in held] if route else []


def _route_km(problem: Problem, state: EngineerState, sequence: list[str]) -> float:
    return sum(visit.leg_km for visit in simulate_route(problem, state, sequence).visits)


def best_insertion(
    problem: Problem, state: EngineerState, sequence: list[str], request_id: str
) -> Insertion | None:
    """Самая дешёвая по километрам допустимая вставка заявки в маршрут инженера."""
    base = _route_km(problem, state, sequence)
    best: Insertion | None = None
    for position in range(len(sequence) + 1):
        candidate = sequence[:position] + [request_id] + sequence[position:]
        sim = simulate_route(problem, state, candidate)
        if not sim.feasible:
            continue
        extra = round(sum(visit.leg_km for visit in sim.visits) - base, 2)
        if best is None or extra < best.extra_km:
            best = Insertion(extra_km=extra, start=sim.visits[position].start)
    return best


def _engineer_name(problem: Problem, engineer_id: str | None) -> str:
    return next((s.engineer.name for s in problem.states if s.engineer.id == engineer_id), str(engineer_id))


def _alternative(
    problem: Problem, plan: Plan, state: EngineerState, request: Request
) -> tuple[Alternative, bool]:
    """Возвращает альтернативу и признак «инженер сейчас без заявок»."""
    engineer = state.engineer
    reason = exclusion(request, state)
    if reason == Exclusion.FIXED_TO_OTHER:
        return Alternative(
            engineer_id=engineer.id,
            feasible=False,
            reason=f"Заявку закрепил диспетчер за {_engineer_name(problem, request.fixed_engineer_id)}",
        ), False
    if reason == Exclusion.NO_SKILL:
        return Alternative(
            engineer_id=engineer.id, feasible=False, reason=f"Нет навыка «{SKILL_RU[request.skill]}»"
        ), False
    if reason == Exclusion.NO_TRANSPORT:
        return Alternative(
            engineer_id=engineer.id,
            feasible=False,
            reason=f"Нужен транспорт «{TRANSPORT_RU[request.transport_required]}», "
            f"у инженера «{TRANSPORT_RU[engineer.transport]}»",
        ), False
    if reason == Exclusion.UNAVAILABLE:
        since = f" с {fmt_hhmm(engineer.unavailable_from)}" if engineer.unavailable_from is not None else ""
        return Alternative(
            engineer_id=engineer.id, feasible=False, reason=f"Инженер недоступен{since}"
        ), False

    sequence = [rid for rid in _open_sequence(problem, plan, engineer.id) if rid != request.id]
    idle = not sequence and not problem.pinned.get(engineer.id)
    insertion = best_insertion(problem, state, sequence, request.id)
    if insertion is None:
        alone = simulate_route(problem, state, [request.id])
        text = (
            "Не успевает в окно или смену даже без других заявок"
            if not alone.feasible
            else "Не помещается в окно или смену вместе со своими заявками"
        )
        return Alternative(engineer_id=engineer.id, feasible=False, reason=text), idle
    note = ", но придётся задействовать ещё одного инженера" if idle else ""
    mileage = (
        "пробег почти не растёт"
        if insertion.extra_km <= KM_EPSILON
        else f"пробег +{insertion.extra_km:.1f} км"
    )
    return Alternative(
        engineer_id=engineer.id,
        feasible=True,
        extra_km=insertion.extra_km,
        start=insertion.start,
        reason=f"Может взять: {mileage}, начало {fmt_hhmm(insertion.start)}{note}",
    ), idle


def _shown(alternative: Alternative) -> Alternative:
    """Дорожные расстояния не всегда подчиняются неравенству треугольника, и прирост пробега бывает
    чуть меньше нуля. Сортировка идёт по сырому значению, диспетчеру показываем не меньше нуля."""
    if alternative.extra_km is None or alternative.extra_km >= 0:
        return alternative
    return alternative.model_copy(update={"extra_km": 0.0})


def _window_detail(request: Request, visit: Visit) -> str:
    text = (
        f"Прибытие {fmt_hhmm(visit.arrival)}, начало {fmt_hhmm(visit.start)}, окно "
        f"{fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}"
    )
    if visit.start > visit.arrival:
        text += f", ожидание {visit.start - visit.arrival} мин"
    if visit.late_min:
        return text + f", опоздание {visit.late_min} мин"
    return text + f", запас до конца окна {request.window_end - visit.start} мин"


def _asap_check(request: Request, visit: Visit) -> ConstraintCheck:
    """Ожидание заявки «как можно скорее» считается от начала окна: времени, когда заявка стала такой."""
    waiting = visit.start - request.window_start
    detail = (
        f"Создана в {fmt_hhmm(request.window_start)}, начало {fmt_hhmm(visit.start)}, ожидание {waiting} мин"
    )
    if waiting > ASAP_FREE_WAIT_MIN:
        detail += ", дольше 4 часов"
    return ConstraintCheck(name=ASAP_CHECK, ok=waiting <= ASAP_FREE_WAIT_MIN, detail=detail)


def _asap_factor(request: Request) -> str:
    since = fmt_hhmm(request.window_start)
    if request.priority != Priority.URGENT:
        return f"Заявка как можно скорее с {since}: у обычной заявки начало ограничено только концом смен."
    hour_km = ObjectiveWeights().asap_late_per_min * 60 / 1000
    return (
        f"Заявка как можно скорее с {since}: ожидание до 4 часов планировщик не штрафует, "
        f"каждый час сверх 4 часов стоит как {hour_km:g} км пробега."
    )


def _assigned_constraints(
    request: Request, engineer: Engineer, visit: Visit, held: bool
) -> list[ConstraintCheck]:
    required = request.transport_required
    current = TRANSPORT_RU[engineer.transport]
    # Закреплённый визит планировался на прежнем транспорте: после смены транспорта он не нарушение.
    replaced = held and required not in (None, engineer.transport)
    if required is None:
        transport_detail = f"Требований к транспорту нет, у инженера «{current}»"
    elif replaced:
        transport_detail = (
            f"Нужен «{TRANSPORT_RU[required]}», работа запланирована до смены транспорта, "
            f"сейчас у инженера «{current}»"
        )
    else:
        transport_detail = f"Нужен «{TRANSPORT_RU[required]}», у инженера «{current}»"
    until = engineer.shift_end
    if not engineer.available and engineer.unavailable_from is not None and not held:
        until = min(until, engineer.unavailable_from)
    return [
        ConstraintCheck(
            name="Навык",
            ok=request.skill in engineer.skills,
            detail=f"Нужен «{SKILL_RU[request.skill]}», у инженера: "
            f"{', '.join(SKILL_RU[skill] for skill in engineer.skills)}",
        ),
        ConstraintCheck(
            name="Транспорт", ok=replaced or required in (None, engineer.transport), detail=transport_detail
        ),
        _asap_check(request, visit)
        if request.asap
        else ConstraintCheck(
            name="Временное окно", ok=visit.late_min == 0, detail=_window_detail(request, visit)
        ),
        ConstraintCheck(
            name="Смена",
            ok=visit.end <= until,
            detail=f"Окончание работы {fmt_hhmm(visit.end)}, смена до {fmt_hhmm(until)}",
        ),
    ]


def _unassigned_constraints(problem: Problem, request: Request) -> list[ConstraintCheck]:
    states = problem.states
    skilled = [s for s in states if request.skill in s.engineer.skills]
    required = request.transport_required
    with_transport = [s for s in skilled if required is None or s.engineer.transport == required]
    eligible = [s for s in states if exclusion(request, s) is None]
    solo = [(s, simulate_route(problem, s, [request.id]).visits[0]) for s in eligible]
    window_ok = any(visit.late_min == 0 for _, visit in solo)
    shift_ok = any(visit.end <= s.available_until for s, visit in solo)
    window = f"{fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}"
    transport_detail = (
        "Требований к транспорту нет"
        if required is None
        else f"Нужен «{TRANSPORT_RU[required]}»: подходящих инженеров {len(with_transport)}"
    )
    timing = ConstraintCheck(
        name="Временное окно",
        ok=window_ok,
        detail=(
            f"Хотя бы один доступный подходящий инженер успевает к окну {window}"
            if window_ok
            else f"Ни один доступный подходящий инженер не успевает к окну {window}"
        ),
    )
    if request.asap:
        today_ok = any(visit.late_min == 0 and visit.end <= s.available_until for s, visit in solo)
        until = max((s.available_until for s in with_transport), default=request.window_end)
        timing = ConstraintCheck(
            name=ASAP_CHECK,
            ok=today_ok,
            detail=(
                "Хотя бы один подходящий инженер успевает сегодня"
                if today_ok
                else f"Сегодня никто из подходящих инженеров не успевает до конца смен в {fmt_hhmm(until)}"
            ),
        )
    return [
        ConstraintCheck(
            name="Навык",
            ok=bool(skilled),
            detail=f"Инженеров с навыком «{SKILL_RU[request.skill]}»: {len(skilled)}",
        ),
        ConstraintCheck(name="Транспорт", ok=bool(with_transport), detail=transport_detail),
        timing,
        ConstraintCheck(
            name="Смена",
            ok=shift_ok,
            detail=(
                "Хотя бы один подходящий инженер заканчивает работу в пределах смены"
                if shift_ok
                else "Ни один подходящий инженер не заканчивает работу в пределах смены"
            ),
        ),
    ]


def build_explanation(problem: Problem, plan: Plan, request: Request) -> Explanation:
    if request.status == RequestStatus.CANCELLED:
        return Explanation(
            request_id=request.id, status="cancelled", summary="Заявка отменена и в плане не участвует."
        )

    assigned = next(
        (
            (route.engineer_id, visit)
            for route in plan.routes
            for visit in route.visits
            if visit.request_id == request.id
        ),
        None,
    )
    if assigned is None:
        item = next((u for u in plan.unassigned if u.request_id == request.id), None)
        located = problem.has_request(request.id)
        alternatives = (
            [_shown(_alternative(problem, plan, s, request)[0]) for s in problem.states] if located else []
        )
        return Explanation(
            request_id=request.id,
            status="unassigned",
            summary=item.reason_text if item else "Заявка не назначена.",
            constraints=_unassigned_constraints(problem, request) if located else [],
            alternatives=alternatives,
            unassigned=item,
        )

    engineer_id, visit = assigned
    engineer = problem.state(engineer_id).engineer
    held = request.id in _held_ids(problem, engineer_id)
    constraints = _assigned_constraints(request, engineer, visit, held)
    lunch = next((route.lunch for route in plan.routes if route.engineer_id == engineer_id), None)
    lunch_factors = [f"Обед {fmt_hhmm(lunch.start)}–{fmt_hhmm(lunch.end)}."] if lunch else []
    if held:
        if visit.pinned:
            summary = f"Исполнитель {engineer.name} начал работу в {fmt_hhmm(visit.start)}, визит закреплён."
            factor = "Работа уже началась к моменту последнего события, поэтому заявка не переназначается."
        else:
            summary = (
                f"Исполнитель {engineer.name} уже в пути к заявке, работа начнётся в {fmt_hhmm(visit.start)}."
            )
            factor = (
                "Инженер уже выехал к заявке, поэтому она не переназначается. "
                "Отменить заявку можно до начала работы."
            )
        return Explanation(
            request_id=request.id,
            status="assigned",
            engineer_id=engineer_id,
            summary=summary,
            factors=[factor, *lunch_factors],
            constraints=constraints,
            visit=visit,
        )

    state = problem.state(engineer_id)
    own_sequence = _open_sequence(problem, plan, engineer_id)
    without = [rid for rid in own_sequence if rid != request.id]
    own_extra = round(_route_km(problem, state, own_sequence) - _route_km(problem, state, without), 2)

    evaluated = [
        _alternative(problem, plan, s, request) for s in problem.states if s.engineer.id != engineer_id
    ]
    feasible = sorted((pair for pair in evaluated if pair[0].feasible), key=lambda pair: pair[0].extra_km)
    infeasible = [pair for pair in evaluated if not pair[0].feasible]

    factors: list[str] = []
    if request.fixed_engineer_id == engineer_id:
        factors.append(
            f"Заявку закрепил диспетчер за {engineer.name}: другим бригадам планировщик её не отдаёт."
        )
    if request.priority == Priority.URGENT:
        factors.append("Срочная заявка: при нехватке времени планировщик назначает её в первую очередь.")
    if request.asap:
        factors.append(_asap_factor(request))
    if not feasible:
        factors.append("Другие инженеры взять заявку не могут: причины указаны в списке альтернатив.")
    else:
        best, _ = feasible[0]
        best_name = problem.state(best.engineer_id).engineer.name
        delta = best.extra_km - own_extra
        if delta > KM_EPSILON:
            factors.append(
                f"Кратчайшая вставка: у лучшей альтернативы ({best_name}) пробег больше на {delta:.1f} км."
            )
        elif delta < -KM_EPSILON:
            factors.append(
                f"Локально {best_name} взял бы заявку с пробегом на {-delta:.1f} км меньше, но в общем плане "
                f"так получается меньше задействованных инженеров или меньший суммарный пробег."
            )
        else:
            factors.append(f"У {best_name} такой же пробег, назначение сохраняет стабильность плана.")
        if all(idle for _, idle in feasible):
            factors.append(
                "Передача любому другому подходящему инженеру задействовала бы ещё одного исполнителя."
            )
    factors.append(
        f"В плане задействовано инженеров: {plan.metrics.engineers_used}, суммарный пробег "
        f"{plan.metrics.total_km:.1f} км."
    )
    factors.extend(lunch_factors)
    added = (
        "заявка почти не удлиняет маршрут"
        if own_extra <= KM_EPSILON
        else f"заявка добавляет к маршруту {own_extra:.1f} км"
    )
    timing = (
        f"{fmt_hhmm(visit.start)} (как можно скорее с {fmt_hhmm(request.window_start)})"
        if request.asap
        else f"{fmt_hhmm(visit.start)} в окне {fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}"
    )
    return Explanation(
        request_id=request.id,
        status="assigned",
        engineer_id=engineer_id,
        summary=(
            f"Исполнитель {engineer.name}. Навык и транспорт подходят, работа начнётся в {timing}, {added}."
        ),
        factors=factors,
        constraints=constraints,
        visit=visit,
        alternatives=[_shown(pair[0]) for pair in feasible] + [pair[0] for pair in infeasible],
    )
