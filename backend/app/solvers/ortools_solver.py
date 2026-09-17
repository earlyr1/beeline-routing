"""Оптимизированный план: OR-Tools RoutingModel (VRPTW с навыками, транспортом и сменами).

Цель лексикографическая через веса: сначала назначить все заявки (закреплённые диспетчером важнее всех,
срочные важнее обычных), затем задействовать меньше инженеров, затем меньше километров. Насколько дорог
новый инженер, задаёт нагрузка дня (app/planning/workload.py): в спокойный день он дешевле, на пределе дороже. При перепланировании
добавляется штраф за перенос заявки к другому инженеру, а у инженеров с закреплёнными
визитами фиксированная стоимость нулевая. Срочная заявка «как можно скорее» ждёт до 4 часов бесплатно,
дальше каждая минута ожидания слегка штрафуется.
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.domain.enums import Priority
from app.domain.models import ASAP_FREE_WAIT_MIN, LUNCH_MIN, Plan, Request
from app.settings import DEFAULT_SOLVER_TIME_LIMIT_S
from app.solvers.assemble import build_plan
from app.solvers.eligibility import exclusion
from app.solvers.problem import EngineerState, Problem
from app.solvers.simulate import simulate_route

DAY_MIN = 24 * 60


@dataclass(frozen=True)
class ObjectiveWeights:
    # Условные 1000 км за каждого задействованного инженера в «Обычный день»; другие уровни нагрузки меняют
    # только эту стоимость (app/planning/workload.py).
    vehicle_fixed_cost: int = 1_000_000
    drop_normal: int = 10_000_000
    drop_urgent: int = 100_000_000
    # Заявка, которую диспетчер закрепил за бригадой: его выбор важнее любой другой заявки, поэтому решатель скорее
    # снимет с бригады обычные и срочные заявки, чем оставит закреплённую без инженера.
    drop_fixed: int = 1_000_000_000
    reassignment: int = 20_000  # условные 20 км за перенос заявки к другому инженеру
    # Условные 0.2 км за минуту ожидания срочной заявки «как можно скорее» сверх 4 часов: лишний час стоит 12 км,
    # намного дешевле ещё одного инженера и снятия срочной заявки.
    asap_late_per_min: int = 200


def drop_penalty(request: Request, weights: ObjectiveWeights) -> int:
    """Штраф за заявку без инженера: закреплённая диспетчером дороже срочной, срочная дороже обычной."""
    if request.fixed_engineer_id is not None:
        return weights.drop_fixed
    return weights.drop_urgent if request.priority == Priority.URGENT else weights.drop_normal


@dataclass(frozen=True)
class SearchStrategy:
    """Стратегия поиска OR-Tools: как строится первое решение и какой метаэвристикой оно улучшается.

    При перепланировании поиск стартует от прежнего плана, и первое решение не строится: различаются только
    метаэвристики.
    """

    first_solution: int = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    metaheuristic: int = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH


DEFAULT_STRATEGY = SearchStrategy()


class OrToolsSolver:
    name = "ortools"

    def __init__(
        self,
        time_limit_s: int = DEFAULT_SOLVER_TIME_LIMIT_S,
        weights: ObjectiveWeights | None = None,
        strategy: SearchStrategy = DEFAULT_STRATEGY,
    ) -> None:
        self.time_limit_s = time_limit_s
        self.weights = weights or ObjectiveWeights()
        self.strategy = strategy

    def solve(self, problem: Problem) -> Plan:
        return build_plan(problem, self.name, self.sequences(problem))

    def sequences(self, problem: Problem) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {state.engineer.id: [] for state in problem.states}
        vehicles = [state for state in problem.states if state.active]
        candidates = [
            request_id
            for request_id in problem.open_request_ids
            if any(exclusion(problem.request(request_id), state) is None for state in vehicles)
        ]
        if not vehicles or not candidates:
            return result
        solved = self._solve_model(problem, vehicles, candidates)
        for state, sequence in zip(vehicles, solved, strict=True):
            result[state.engineer.id] = _keep_feasible_prefix(problem, state, sequence)
        return repair_unassigned(problem, result)

    def _solve_model(
        self, problem: Problem, vehicles: list[EngineerState], candidates: list[str]
    ) -> list[list[str]]:
        weights = self.weights
        v_count = len(vehicles)
        end = v_count  # общий фиктивный финиш: возврат в стартовую точку не нужен
        nodes = (
            [state.start_node for state in vehicles] + [-1] + [problem.request_node(r) for r in candidates]
        )
        service = [0] * (v_count + 1) + [problem.request(r).duration_min for r in candidates]
        size = len(nodes)

        manager = pywrapcp.RoutingIndexManager(size, v_count, list(range(v_count)), [end] * v_count)
        routing = pywrapcp.RoutingModel(manager)

        time_callbacks = []
        for v, state in enumerate(vehicles):
            engineer = state.engineer
            time_matrix = [[0] * size for _ in range(size)]
            cost_matrix = [[0] * size for _ in range(size)]
            for a in range(size):
                if a == end:
                    continue
                for b in range(size):
                    if b == end or a == b:
                        time_matrix[a][b] = service[a]
                        continue
                    if b < v_count:
                        continue  # в стартовые узлы не въезжаем
                    time_matrix[a][b] = service[a] + problem.travel_min(nodes[a], nodes[b], engineer)
                    cost = round(problem.travel_km(nodes[a], nodes[b], engineer) * 1000)
                    previous = problem.previous_assignment.get(candidates[b - v_count - 1])
                    if previous is not None and previous != engineer.id:
                        cost += weights.reassignment
                    cost_matrix[a][b] = cost

            # Матрицы регистрируются в C++: поиск не вызывает Python на каждой дуге и успевает больше.
            time_callbacks.append(routing.RegisterTransitMatrix(time_matrix))
            routing.SetArcCostEvaluatorOfVehicle(routing.RegisterTransitMatrix(cost_matrix), v)

        routing.AddDimensionWithVehicleTransits(time_callbacks, DAY_MIN, 2 * DAY_MIN, False, "Time")
        time_dimension = routing.GetDimensionOrDie("Time")

        for k, request_id in enumerate(candidates):
            request = problem.request(request_id)
            index = manager.NodeToIndex(v_count + 1 + k)
            time_dimension.CumulVar(index).SetRange(request.window_start, request.window_end)
            allowed = [v for v, state in enumerate(vehicles) if exclusion(request, state) is None]
            # SetAllowedVehiclesForIndex не принимает list в Python-обёртке 9.15, поэтому VehicleVar
            routing.VehicleVar(index).SetValues([-1] + allowed)
            routing.AddDisjunction([index], drop_penalty(request, weights))
            if request.priority == Priority.URGENT and request.asap and weights.asap_late_per_min:
                # Жёсткое окно до конца смен остаётся, а начало позже 4 часов ожидания штрафуется слегка.
                time_dimension.SetCumulVarSoftUpperBound(
                    index, request.window_start + ASAP_FREE_WAIT_MIN, weights.asap_late_per_min
                )

        for v, state in enumerate(vehicles):
            time_dimension.CumulVar(routing.Start(v)).SetRange(state.available_from, state.available_until)
            time_dimension.CumulVar(routing.End(v)).SetRange(state.available_from, state.available_until)
        routing.SetFixedCostOfAllVehicles(weights.vehicle_fixed_cost)
        for v, state in enumerate(vehicles):
            if problem.pinned.get(state.engineer.id):
                # Инженер уже работал сегодня и в метрике учтён в любом случае: не штрафуем за продолжение.
                routing.SetFixedCostOfVehicle(0, v)

        # Обед: перерыв 45 минут с началом в окне обеда, который не пересекает работу на объекте. Точное место
        # обеда и итоговое время визитов потом ставит прогон маршрута. В день без обеда перерывов в модели нет.
        visit_transits = [service[manager.IndexToNode(index)] for index in range(routing.Size() + v_count)]
        for v, state in enumerate(vehicles):
            window = problem.lunch_window(state)
            if window is None:
                continue
            earliest, latest = window
            lunch = routing.solver().FixedDurationIntervalVar(
                max(earliest, state.available_from), latest, LUNCH_MIN, False, f"lunch_{v}"
            )
            time_dimension.SetBreakIntervalsOfVehicle([lunch], v, visit_transits)

        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = self.strategy.first_solution
        params.local_search_metaheuristic = self.strategy.metaheuristic
        params.time_limit.FromSeconds(self.time_limit_s)
        routing.CloseModelWithParameters(params)

        initial = None
        if problem.previous_order:
            position = {request_id: v_count + 1 + k for k, request_id in enumerate(candidates)}
            hint = []
            for state in vehicles:
                sequence = [
                    r
                    for r in problem.previous_order.get(state.engineer.id, [])
                    if r in position and exclusion(problem.request(r), state) is None
                ]
                hint.append([manager.NodeToIndex(position[r]) for r in sequence])
            initial = routing.ReadAssignmentFromRoutes(hint, True)
        solution = (
            routing.SolveFromAssignmentWithParameters(initial, params)
            if initial is not None
            else routing.SolveWithParameters(params)
        )
        if solution is None:
            return [[] for _ in vehicles]

        routes: list[list[str]] = []
        for v in range(v_count):
            sequence = []
            index = solution.Value(routing.NextVar(routing.Start(v)))
            while not routing.IsEnd(index):
                sequence.append(candidates[manager.IndexToNode(index) - v_count - 1])
                index = solution.Value(routing.NextVar(index))
            routes.append(sequence)
        return routes


def _keep_feasible_prefix(problem: Problem, state: EngineerState, sequence: list[str]) -> list[str]:
    """Страховка от расхождения округлений: выкидываем визиты, которые не проходят симуляцию."""
    kept: list[str] = []
    for request_id in sequence:
        if simulate_route(problem, state, kept + [request_id]).feasible:
            kept.append(request_id)
    return kept


def repair_unassigned(problem: Problem, sequences: dict[str, list[str]]) -> dict[str, list[str]]:
    """Страховка от недосмотра поиска за лимит времени: жадно вставляет оставшиеся заявки.

    Первыми идут закреплённые диспетчером заявки, за ними срочные. Для каждой берётся допустимая позиция
    с наименьшим приростом километров, причём инженеры, у которых уже есть работа сегодня, предпочтительнее
    простаивающих.
    """
    result = {engineer_id: list(sequence) for engineer_id, sequence in sequences.items()}
    placed = {request_id for sequence in result.values() for request_id in sequence}
    pending = [request_id for request_id in problem.open_request_ids if request_id not in placed]
    pending.sort(
        key=lambda request_id: (
            problem.request(request_id).fixed_engineer_id is None,
            problem.request(request_id).priority != Priority.URGENT,
        )
    )
    for request_id in pending:
        request = problem.request(request_id)
        best: tuple[tuple[int, float], str, list[str]] | None = None
        for state in problem.states:
            if exclusion(request, state) is not None:
                continue
            engineer_id = state.engineer.id
            sequence = result.get(engineer_id, [])
            base_km = sum(visit.leg_km for visit in simulate_route(problem, state, sequence).visits)
            idle = 0 if sequence or problem.pinned.get(engineer_id) else 1
            for position in range(len(sequence) + 1):
                candidate = sequence[:position] + [request_id] + sequence[position:]
                sim = simulate_route(problem, state, candidate)
                if not sim.feasible:
                    continue
                key = (idle, sum(visit.leg_km for visit in sim.visits) - base_km)
                if best is None or key < best[0]:
                    best = (key, engineer_id, candidate)
        if best is not None:
            result[best[1]] = best[2]
    return result
