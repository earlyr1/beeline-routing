"""Базовый вариант строго по ТЗ (п. 2.3).

Заявки берутся в порядке поступления и назначаются первому по порядку во входных данных
доступному инженеру, который удовлетворяет обязательным ограничениям. Порядок посещения
равен порядку назначения. Глобальной оптимизации нет.
"""

from __future__ import annotations

from app.domain.models import Plan
from app.solvers.assemble import build_plan
from app.solvers.eligibility import exclusion
from app.solvers.problem import Problem
from app.solvers.simulate import simulate_route


class FcfsSolver:
    name = "fcfs"

    def sequences(self, problem: Problem) -> dict[str, list[str]]:
        sequences: dict[str, list[str]] = {state.engineer.id: [] for state in problem.states}
        for request_id in problem.open_request_ids:
            request = problem.request(request_id)
            for state in problem.states:
                if exclusion(request, state) is not None:
                    continue
                candidate = sequences[state.engineer.id] + [request_id]
                if simulate_route(problem, state, candidate).feasible:
                    sequences[state.engineer.id] = candidate
                    break
        return sequences

    def solve(self, problem: Problem) -> Plan:
        return build_plan(problem, self.name, self.sequences(problem))
