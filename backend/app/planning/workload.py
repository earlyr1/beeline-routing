"""Нагрузка инженеров на день: уровень, который диспетчер выбирает при загрузке данных.

Уровень задаёт стоимость нового инженера для оптимизатора и запас времени на дорогу. В спокойный день
оптимизатор охотнее задействует ещё одного инженера, а дорога планируется с большим
запасом. На пределе инженеров меньше, каждому больше заявок, время в пути без запаса. Базовый вариант FCFS
стоимость инженера не использует, но едет по тем же минутам с запасом: запас описывает сам день, и оба плана
сравниваются на одинаковом времени.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.solvers.ortools_solver import ObjectiveWeights
from app.solvers.problem import TravelBuffer


@dataclass(frozen=True)
class WorkloadLevel:
    title: str
    emoji: str
    vehicle_fixed_cost: int  # в единицах стоимости пробега: 1000 = 1 км
    travel_buffer: TravelBuffer


# Индекс в кортеже и есть уровень нагрузки: самый спокойный, средний и самый напряжённый. Стоимость инженера везде
# ниже снятия обычной заявки (drop_normal): оптимизатор по-прежнему скорее задействует ещё одного инженера, чем
# оставит заявку без исполнителя.
WORKLOAD_LEVELS: tuple[WorkloadLevel, ...] = (
    WorkloadLevel("Спокойный день", "😌", 20_000, TravelBuffer(1.30, 5)),
    WorkloadLevel("Обычный день", "😐", 1_000_000, TravelBuffer(1.10, 5)),
    WorkloadLevel("На пределе", "🥵", 6_000_000, TravelBuffer(1.00, 0)),
)
DEFAULT_WORKLOAD_LEVEL = 1
WORKLOAD_LEVEL_TEXT = f"уровень нагрузки должен быть от 0 до {len(WORKLOAD_LEVELS) - 1}"


def is_workload_level(level: int) -> bool:
    return 0 <= level < len(WORKLOAD_LEVELS)


def workload(level: int) -> WorkloadLevel:
    """Строка таблицы уровня. Бросает ValueError с текстом для диспетчера, если уровня нет."""
    if not is_workload_level(level):
        raise ValueError(WORKLOAD_LEVEL_TEXT)
    return WORKLOAD_LEVELS[level]


def workload_weights(level: int) -> ObjectiveWeights:
    """Веса оптимизатора для уровня: меняется только стоимость нового инженера, остальные веса прежние."""
    return replace(ObjectiveWeights(), vehicle_fixed_cost=workload(level).vehicle_fixed_cost)


def travel_buffer(level: int) -> TravelBuffer:
    return workload(level).travel_buffer
