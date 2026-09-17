"""Причина, по которой заявка осталась неназначенной, языком диспетчера."""

from __future__ import annotations

from app.domain.enums import SKILL_RU, TRANSPORT_RU, ReasonCode
from app.domain.models import Unassigned
from app.domain.timeutil import fmt_hhmm
from app.solvers.problem import Problem
from app.solvers.simulate import simulate_route


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

    solo = [(s, simulate_route(problem, s, [request_id])) for s in active]
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
