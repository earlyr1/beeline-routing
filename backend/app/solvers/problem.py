"""Постановка задачи для солверов: заявки, инженеры, матрица и состояние дня."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.domain.enums import ReasonCode, RequestStatus
from app.domain.models import (
    LUNCH_EARLIEST_MIN,
    LUNCH_LATEST_MIN,
    LUNCH_WORKDAY_MIN,
    Engineer,
    Lunch,
    Request,
    Unassigned,
    Visit,
)
from app.geo.kvcache import KVCache
from app.geo.matrix import TrafficProfile, TravelModel, TravelTimes, build_base_matrix
from app.geo.osrm import OsrmClient
from app.geo.transit import TransitLookup, TransitMatrix


@dataclass(frozen=True)
class TravelBuffer:
    """Запас времени на дорогу, который задаёт нагрузка дня (app/planning/workload.py).

    Поездка t > 0 минут планируется как max(ceil(t * factor), t + min_extra). Поездка в ту же точку остаётся
    нулевой. Километры, линии маршрутов и метрика пробега от запаса не зависят.
    """

    factor: float = 1.0
    min_extra: int = 0

    def minutes(self, travel_min: int) -> int:
        if travel_min <= 0:
            return travel_min
        # round убирает хвост двоичной дроби: 100 * 1.1 = 110.00000000000001 даёт 110 минут, а не 111.
        return max(math.ceil(round(travel_min * self.factor, 6)), travel_min + self.min_extra)


NO_BUFFER = TravelBuffer()


@dataclass
class EngineerState:
    engineer: Engineer
    start_node: int
    available_from: int
    available_until: int
    # Сколько единиц оборудования у бригады ещё с собой: утренний запас минус выданные до времени пересчёта.
    # Новую в офисе не берут, поэтому на остаток дня это жёсткий предел.
    equipment_left: int

    @property
    def active(self) -> bool:
        return self.available_until > self.available_from


def initial_state(engineer: Engineer, home_node: int) -> EngineerState:
    until = engineer.shift_end
    if not engineer.available:
        cutoff = engineer.unavailable_from if engineer.unavailable_from is not None else engineer.shift_start
        until = min(until, cutoff)
    return EngineerState(engineer, home_node, engineer.shift_start, until, engineer.equipment_stock)


@dataclass
class Problem:
    requests: list[Request]  # только заявки с координатами, в порядке входных данных
    engineers: list[Engineer]  # в порядке входных данных
    travel: TravelTimes
    states: list[EngineerState]  # в порядке engineers
    open_request_ids: list[str]  # что нужно распределить, в порядке поступления
    unplannable: list[Unassigned] = field(default_factory=list)
    pinned: dict[str, list[Visit]] = field(default_factory=dict)
    previous_assignment: dict[str, str] = field(default_factory=dict)
    previous_order: dict[str, list[str]] = field(default_factory=dict)
    now: int = 0
    buffer: TravelBuffer = NO_BUFFER  # запас на дорогу по нагрузке дня, входит в travel_min
    # Обед, начатый до события: остаётся как в прежнем плане, новый обед инженеру уже не нужен.
    pinned_lunch: dict[str, Lunch] = field(default_factory=dict)
    # Обед по плану в этот день. Без обеда задача планируется так же, как до появления обеда в сервисе.
    lunch: bool = True

    def __post_init__(self) -> None:
        offset = len(self.engineers)
        self._requests = {request.id: request for request in self.requests}
        self._nodes = {request.id: offset + k for k, request in enumerate(self.requests)}
        self._slot = [0] * offset + [request.window_start for request in self.requests]
        self._states = {state.engineer.id: state for state in self.states}

    def request(self, request_id: str) -> Request:
        return self._requests[request_id]

    def has_request(self, request_id: str) -> bool:
        return request_id in self._requests

    def request_node(self, request_id: str) -> int:
        return self._nodes[request_id]

    def home_node(self, engineer_id: str) -> int:
        return next(k for k, engineer in enumerate(self.engineers) if engineer.id == engineer_id)

    def state(self, engineer_id: str) -> EngineerState:
        return self._states[engineer_id]

    def equipment_used(self, request_ids: Iterable[str]) -> int:
        """Сколько единиц оборудования разбирает набор заявок: по одной на заявку с needs_equipment."""
        return sum(1 for rid in request_ids if self.has_request(rid) and self.request(rid).needs_equipment)

    def lunch_window(self, state: EngineerState) -> tuple[int, int] | None:
        """Самое раннее и самое позднее начало обеда инженера или None, если обед по плану ему не нужен.

        Обед не нужен в день без обеда (lunch=False), если рабочий день (до конца смены или до недоступности) короче
        6 часов, если обед уже начат до события (pinned_lunch) и если окно обеда прошло к моменту, с которого инженер
        свободен.
        """
        engineer = state.engineer
        if (
            not self.lunch
            or engineer.id in self.pinned_lunch
            or state.available_until - engineer.shift_start < LUNCH_WORKDAY_MIN
        ):
            return None
        latest = engineer.shift_start + LUNCH_LATEST_MIN
        if state.available_from > latest:
            return None
        return engineer.shift_start + LUNCH_EARLIEST_MIN, latest

    def travel_km(self, from_node: int, to_node: int, engineer: Engineer) -> float:
        return self.travel.km(from_node, to_node, engineer.transport)

    def leg_limit_km(self, engineer: Engineer) -> float | None:
        """Предел одного плеча для транспорта инженера или None, если предела нет (автомобиль)."""
        return self.travel.model.leg_limit_km(engineer.transport)

    def leg_too_long(self, from_node: int, to_node: int, engineer: Engineer) -> bool:
        """Плечо длиннее предела: единственный источник ответа для прогона маршрута и модели OR-Tools."""
        limit = self.leg_limit_km(engineer)
        return limit is not None and self.travel_km(from_node, to_node, engineer) > limit

    def travel_min(self, from_node: int, to_node: int, engineer: Engineer) -> int:
        """Минуты в пути с запасом нагрузки дня. Коэффициент пробок берётся по началу окна заявки назначения.

        Это единственный источник времени в пути для солверов, прогона маршрута, причин, объяснений и прогнозов.
        """
        return self.buffer.minutes(
            self.travel.minutes(from_node, to_node, engineer.transport, self._slot[to_node])
        )


def make_problem(
    requests: list[Request],
    engineers: list[Engineer],
    *,
    model: TravelModel,
    traffic: TrafficProfile,
    osrm: OsrmClient | None = None,
    cache: KVCache | None = None,
    buffer: TravelBuffer = NO_BUFFER,
    lunch: bool = True,
    transit: Sequence[TransitMatrix] = (),
) -> Problem:
    """Задача на начало дня: все инженеры в стартовых точках, все активные заявки открыты.

    buffer — запас на дорогу по нагрузке дня. Базовая матрица от него не зависит: OSRM берётся из кэша.
    lunch — обед по плану в этот день.
    transit — матрицы 2ГИС по регионам, посчитанные диспетчером заранее: минуты общественного транспорта берутся
    из них по парам точек (TransitLookup), остальные пары считает встроенная модель.
    """
    located = [r for r in requests if r.lat is not None and r.lon is not None]
    unplannable = [
        Unassigned(
            request_id=r.id,
            reason_code=ReasonCode.ADDRESS_NOT_FOUND,
            reason_text=f"Адрес не найден на карте: «{r.address}».",
        )
        for r in requests
        if (r.lat is None or r.lon is None) and r.status == RequestStatus.ACTIVE
    ]
    points = [(e.start_lat, e.start_lon) for e in engineers] + [(r.lat, r.lon) for r in located]
    # Минуты 2ГИС берутся по парам: точка дня привязывается к ближайшей точке матрицы в 150 м, и пара из одной матрицы
    # идёт из 2ГИС, даже если в дне появилась срочная заявка или сменился адрес. Пары с новой точкой, как и день,
    # которого нет ни в одной матрице, считает встроенная модель, и это не ошибка.
    lookup = TransitLookup(points, transit) if transit else None
    travel = TravelTimes(
        build_base_matrix(points, model, osrm=osrm, cache=cache),
        model,
        traffic,
        transit=lookup if lookup is not None and lookup.covered else None,
    )
    states = [initial_state(engineer, k) for k, engineer in enumerate(engineers)]
    open_ids = [r.id for r in located if r.status == RequestStatus.ACTIVE]
    return Problem(
        located, list(engineers), travel, states, open_ids, unplannable, buffer=buffer, lunch=lunch
    )
