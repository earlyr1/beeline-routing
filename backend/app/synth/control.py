"""План диспетчеров из контрольного распределения, посчитанный нашей моделью времени и пробега."""

from __future__ import annotations

from app.domain.enums import ReasonCode
from app.domain.models import Plan, Unassigned
from app.ingest.beeline_csv import RawFile
from app.solvers.assemble import build_plan
from app.solvers.problem import Problem

CONTROL_SOLVER = "dispatchers"


def build_control_plan(
    problem: Problem, control: RawFile, synthetic: RawFile, crew_to_engineer: dict[str, str]
) -> Plan:
    sequences: dict[str, list[str]] = {state.engineer.id: [] for state in problem.states}
    fixed: dict[str, Unassigned] = {}
    for c_row, s_row in zip(control.rows, synthetic.rows, strict=True):
        request_id = s_row.request_id
        if not problem.has_request(request_id):
            continue
        engineer_id = crew_to_engineer.get(c_row.crew)
        if engineer_id is None:
            fixed[request_id] = Unassigned(
                request_id=request_id,
                reason_code=ReasonCode.NO_FREE_ENGINEER,
                reason_text="Диспетчер не назначил бригаду.",
            )
            continue
        sequences[engineer_id].append(request_id)
    for sequence in sequences.values():
        # Порядок визитов бригады в выгрузке не записан, и мы выбираем его сами: сначала та заявка, чьё окно
        # закрывается раньше. Сортировка по началу окна ставила бы первыми аварии, у которых окно 00:01-23:59,
        # и бригада с семью авариями уезжала бы на них всё утро, опаздывая к заявкам с окном 10:00-12:00.
        # На Юго-востоке это дало бы 32 заявки с нарушением вместо 24 и лишние 25 км у плана диспетчеров.
        sequence.sort(key=lambda rid: (problem.request(rid).window_end, problem.request(rid).window_start))
    # План диспетчеров показывается как есть: обеда в нём нет, и визиты обедом не сдвигаются.
    return build_plan(problem, CONTROL_SOLVER, sequences, fixed_unassigned=fixed, lunch=False)
