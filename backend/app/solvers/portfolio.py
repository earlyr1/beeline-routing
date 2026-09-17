"""Параллельный поиск OR-Tools: несколько стратегий одновременно в отдельных процессах, берётся лучший план.

Поиск OR-Tools занимает одно ядро и идёт до лимита времени, поэтому на многоядерной машине за тот же лимит можно
запустить несколько поисков с разными стратегиями и выбрать лучший план. Планы сравниваются по той же цели, что
у OR-Tools (plan_cost), уже после страховочной починки маршрутов.

Процессы запускаются через spawn: веб-сервер работает с потоками, а fork при живых потоках небезопасен. Пул
создаётся при первом поиске и живёт до конца процесса; упавший процесс не роняет расчёт — пул пересоздаётся,
а если не ответил ни один поиск, план считается в текущем процессе.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
from collections.abc import Sequence
from concurrent.futures import Future, ProcessPoolExecutor

from ortools.constraint_solver import routing_enums_pb2

from app.domain.enums import Priority
from app.domain.models import ASAP_FREE_WAIT_MIN, Plan
from app.solvers.assemble import build_plan
from app.solvers.ortools_solver import (
    DEFAULT_STRATEGY,
    ObjectiveWeights,
    OrToolsSolver,
    SearchStrategy,
    drop_penalty,
)
from app.solvers.problem import Problem

logger = logging.getLogger(__name__)

_FIRST = routing_enums_pb2.FirstSolutionStrategy
_META = routing_enums_pb2.LocalSearchMetaheuristic

# Порядок важен: при равной стоимости побеждает стратегия раньше, первой идёт прежняя стратегия сервиса.
PORTFOLIO: tuple[SearchStrategy, ...] = (
    DEFAULT_STRATEGY,
    SearchStrategy(_FIRST.PARALLEL_CHEAPEST_INSERTION, _META.GUIDED_LOCAL_SEARCH),
    SearchStrategy(_FIRST.PATH_CHEAPEST_ARC, _META.SIMULATED_ANNEALING),
    SearchStrategy(_FIRST.SAVINGS, _META.TABU_SEARCH),
)
# Запас сверх лимита поиска на запуск процесса, передачу задачи и сборку ответа.
RESULT_GRACE_S = 60


def plan_cost(problem: Problem, plan: Plan, weights: ObjectiveWeights) -> int:
    """Стоимость плана в весах цели OR-Tools: снятые заявки, новые инженеры, километры, переносы, ожидание ASAP.

    Заявки без координат не входят в задачу и одинаковы у всех планов. Нарушение ограничений стоит как снятая
    срочная заявка: план с нарушениями хуже любого допустимого.
    """
    cost = 0
    for item in plan.unassigned:
        if problem.has_request(item.request_id):
            cost += drop_penalty(problem.request(item.request_id), weights)
    for route in plan.routes:
        pinned = problem.pinned.get(route.engineer_id, [])
        pinned_ids = {visit.request_id for visit in pinned}
        visits = [visit for visit in route.visits if visit.request_id not in pinned_ids]
        if visits and not pinned:
            cost += weights.vehicle_fixed_cost
        for visit in visits:
            cost += round(visit.leg_km * 1000)
            previous = problem.previous_assignment.get(visit.request_id)
            if previous is not None and previous != route.engineer_id:
                cost += weights.reassignment
            request = problem.request(visit.request_id)
            if request.priority == Priority.URGENT and request.asap:
                waited = visit.start - (request.window_start + ASAP_FREE_WAIT_MIN)
                cost += max(0, waited) * weights.asap_late_per_min
    return cost + len(plan.violations) * weights.drop_urgent


def _sequences(
    problem: Problem, weights: ObjectiveWeights, time_limit_s: int, strategy: SearchStrategy
) -> dict[str, list[str]]:
    """Один поиск в процессе пула: последовательности заявок по инженерам."""
    return OrToolsSolver(time_limit_s=time_limit_s, weights=weights, strategy=strategy).sequences(problem)


def _ready() -> bool:
    """Пустая задача: процесс пула запущен и импортировал OR-Tools."""
    return True


class SolverPool:
    """Пул процессов для поисков OR-Tools. workers — сколько поисков идут одновременно."""

    def __init__(self, workers: int) -> None:
        if workers < 1:
            raise ValueError("число процессов поиска должно быть не меньше 1")
        self.workers = workers
        self._lock = threading.Lock()
        self._executor: ProcessPoolExecutor | None = None

    def _pool(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ProcessPoolExecutor(
                    max_workers=self.workers, mp_context=multiprocessing.get_context("spawn")
                )
            return self._executor

    def _reset(self, broken: ProcessPoolExecutor) -> None:
        with self._lock:
            if self._executor is broken:
                self._executor = None
        broken.shutdown(wait=False, cancel_futures=True)

    def warm_up(self) -> None:
        """Запускает процессы заранее, чтобы первый поиск не ждал запуска Python и импорта OR-Tools."""
        pool = self._pool()
        for future in [pool.submit(_ready) for _ in range(self.workers)]:
            future.result()

    def strategies(self, share: int = 1) -> tuple[SearchStrategy, ...]:
        """Стратегии одного поиска, когда пул делят share одновременных поисков."""
        return PORTFOLIO[: max(1, min(len(PORTFOLIO), self.workers // max(1, share)))]

    def solve(
        self,
        problem: Problem,
        weights: ObjectiveWeights,
        time_limit_s: int,
        strategies: Sequence[SearchStrategy],
    ) -> Plan:
        """Лучший план из поисков со стратегиями strategies, запущенных одновременно."""
        pool = self._pool()
        futures: list[Future[dict[str, list[str]]]] = []
        try:
            for strategy in strategies:
                futures.append(pool.submit(_sequences, problem, weights, time_limit_s, strategy))
        except RuntimeError as error:  # пул сломан или закрыт
            logger.warning("Пул поиска недоступен, план считается в текущем процессе: %s", error)
            self._reset(pool)
        plans: list[Plan] = []
        for future in futures:
            try:
                sequences = future.result(timeout=time_limit_s + RESULT_GRACE_S)
            except Exception as error:  # noqa: BLE001 - упавший поиск заменяют остальные или расчёт на месте
                logger.warning("Поиск OR-Tools в пуле не ответил: %s", error)
                self._reset(pool)
                continue
            plans.append(build_plan(problem, OrToolsSolver.name, sequences))
        if not plans:
            return OrToolsSolver(time_limit_s=time_limit_s, weights=weights).solve(problem)
        # min возвращает первый из равных: при равной стоимости побеждает стратегия раньше в портфеле.
        return min(plans, key=lambda plan: plan_cost(problem, plan, weights))

    def shutdown(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
