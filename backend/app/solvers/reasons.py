"""Причина, по которой заявка осталась неназначенной, языком диспетчера."""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.enums import SKILL_RU, TRANSPORT_RU, ReasonCode
from app.domain.models import Unassigned
from app.domain.timeutil import fmt_hhmm
from app.solvers.problem import EngineerState, Problem
from app.solvers.simulate import simulate_route


def too_far_km(
    problem: Problem, state: EngineerState, sequence: Sequence[str], request_id: str
) -> float | None:
    """Самое короткое плечо до заявки, если и оно длиннее предела транспорта инженера, иначе None.

    Плечо считается от стартовой точки инженера и от каждой его заявки: если заявка дальше предела отовсюду,
    бригада до неё не доедет ни первой, ни в середине маршрута.
    """
    engineer = state.engineer
    destination = problem.request_node(request_id)
    nodes = [state.start_node, *(problem.request_node(rid) for rid in sequence)]
    if not all(problem.leg_too_long(node, destination, engineer) for node in nodes):
        return None
    return min(problem.travel_km(node, destination, engineer) for node in nodes)


def state_equipment_left(problem: Problem, state: EngineerState, sequences: dict[str, list[str]]) -> int:
    """Сколько единиц оборудования у бригады ещё свободно: что осталось с утра минус разобранное её маршрутом."""
    return state.equipment_left - problem.equipment_used(sequences.get(state.engineer.id, []))


def unassigned_reason(problem: Problem, request_id: str, sequences: dict[str, list[str]]) -> Unassigned:
    """Причина по всем инженерам, а у заявки, закреплённой диспетчером, только по её бригаде."""
    request = problem.request(request_id)
    skill = SKILL_RU[request.skill]
    window = f"{fmt_hhmm(request.window_start)}–{fmt_hhmm(request.window_end)}"
    states = problem.states
    lead_in = ""
    fixed = [s for s in states if s.engineer.id == request.fixed_engineer_id]
    if fixed:
        # Другие бригады заявку не возьмут: причина — в закреплённой бригаде. Если её нет в дне, считаем по всем.
        states = fixed
        lead_in = f"Заявку закрепил диспетчер за {fixed[0].engineer.name}. "

    def result(code: ReasonCode, text: str) -> Unassigned:
        return Unassigned(request_id=request_id, reason_code=code, reason_text=lead_in + text)

    skilled = [s for s in states if request.skill in s.engineer.skills]
    if not skilled:
        return result(ReasonCode.NO_SKILL, f"Нет инженера с навыком «{skill}».")
    with_transport = [
        s
        for s in skilled
        if request.transport_required is None or s.engineer.transport == request.transport_required
    ]
    if not with_transport:
        transport = TRANSPORT_RU[request.transport_required]
        return result(
            ReasonCode.NO_TRANSPORT, f"Нет инженера с навыком «{skill}» и транспортом «{transport}»."
        )
    active = [s for s in with_transport if s.active]
    if not active and request.asap:
        # У заявки «как можно скорее» инженер, у которого сегодня не осталось рабочего времени, не недоступен:
        # он просто не успевает сегодня.
        active = [s for s in with_transport if s.engineer.available]
    if not active:
        names = ", ".join(s.engineer.name for s in with_transport)
        return result(ReasonCode.NO_FREE_ENGINEER, f"Все подходящие инженеры недоступны: {names}.")

    # Плечо до заявки считается от стартовой точки бригады: прогон «даже без других заявок» едет именно оттуда,
    # и у бригады, которой это плечо запрещено, он показал бы время поездки, которой не будет.
    direct = [(s, too_far_km(problem, s, (), request_id)) for s in active]
    alone = [s for s, far in direct if far is None]
    if not alone:
        limits = ", ".join(
            f"«{TRANSPORT_RU[transport]}» не дальше {limit:g} км"
            for transport, limit in sorted(
                {(s.engineer.transport, problem.leg_limit_km(s.engineer)) for s in active}
            )
        )
        # Через свои заявки бригада иногда доезжает и туда, но вместе с ними заявка в маршрут не поместилась.
        detour = any(
            too_far_km(problem, s, sequences.get(s.engineer.id, []), request_id) is None for s in active
        )
        tail = (
            " По пути от своих заявок доехать можно, но вместе с ними заявка не помещается." if detour else ""
        )
        return result(
            ReasonCode.NO_TRANSPORT,
            f"Нет инженера, который доедет: ближайшая подходящая бригада в "
            f"{min(far for _, far in direct):.0f} км от заявки, а {limits}.{tail}",
        )

    # Оборудование бригада получает в офисе утром на весь день: если у всех, кто мог бы приехать, запас уже
    # разобран, дело не в окне и не в смене — везти нечего. Бригады, у которых единицы ещё есть, судим дальше
    # по времени, иначе прогон «даже без других заявок» показал бы время поездки, которой не будет.
    if request.needs_equipment:
        stocked = [s for s in alone if state_equipment_left(problem, s, sequences) > 0]
        if not stocked:
            names = ", ".join(s.engineer.name for s in alone)
            return result(
                ReasonCode.NO_FREE_ENGINEER,
                f"Ни у одной подходящей бригады не осталось оборудования: утренний запас разобран ({names}).",
            )
        alone = stocked

    solo = [(s, simulate_route(problem, s, [request_id])) for s in alone]
    fitting = [s for s, sim in solo if sim.feasible]
    if not fitting:
        state, sim = min(solo, key=lambda pair: (pair[1].visits[0].start, pair[1].visits[0].end))
        visit = sim.visits[0]
        lead = "Сегодня не успеть" if request.asap else f"Работа не помещается в окно {window} или в смену"
        # Без обеда инженер успел бы: не помещается именно обед.
        lunch_note = "и с учётом обеда " if sim.lunch_conflict else ""
        return result(
            ReasonCode.DOES_NOT_FIT,
            f"{lead}: даже без других заявок {lunch_note}"
            f"{state.engineer.name} начнёт не раньше {fmt_hhmm(visit.start)} и закончит в "
            f"{fmt_hhmm(visit.end)} (смена до {fmt_hhmm(state.available_until)}).",
        )

    finish, state = min(
        ((simulate_route(problem, s, sequences.get(s.engineer.id, [])).end_time, s) for s in fitting),
        key=lambda pair: pair[0],
    )
    lead = (
        "Сегодня нет свободных исполнителей"
        if request.asap
        else f"Нет свободных исполнителей на окно {window}"
    )
    return result(
        ReasonCode.NO_FREE_ENGINEER,
        f"{lead}: подходящие инженеры ({len(fitting)}) заняты другими "
        f"заявками. Раньше всех освобождается {state.engineer.name} в {fmt_hhmm(finish)}.",
    )
