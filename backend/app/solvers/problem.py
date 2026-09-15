"""Постановка задачи для солверов: заявки, инженеры, матрица и состояние дня."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.enums import ReasonCode, RequestStatus
from app.domain.models import Engineer, Request, Unassigned, Visit
from app.geo.kvcache import KVCache
from app.geo.matrix import TrafficProfile, TravelModel, TravelTimes, build_base_matrix
from app.geo.osrm import OsrmClient


@dataclass
class EngineerState:
    engineer: Engineer
    start_node: int
    available_from: int
    available_until: int

    @property
    def active(self) -> bool:
        return self.available_until > self.available_from


def initial_state(engineer: Engineer, home_node: int) -> EngineerState:
    until = engineer.shift_end
    if not engineer.available:
        cutoff = engineer.unavailable_from if engineer.unavailable_from is not None else engineer.shift_start
        until = min(until, cutoff)
    return EngineerState(engineer, home_node, engineer.shift_start, until)


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

    def travel_km(self, from_node: int, to_node: int, engineer: Engineer) -> float:
        return self.travel.km(from_node, to_node, engineer.transport)

    def travel_min(self, from_node: int, to_node: int, engineer: Engineer) -> int:
        """Коэффициент пробок берётся по началу окна заявки назначения."""
        return self.travel.minutes(from_node, to_node, engineer.transport, self._slot[to_node])


def make_problem(
    requests: list[Request],
    engineers: list[Engineer],
    *,
    model: TravelModel,
    traffic: TrafficProfile,
    osrm: OsrmClient | None = None,
    cache: KVCache | None = None,
) -> Problem:
    """Задача на начало дня: все инженеры в стартовых точках, все активные заявки открыты."""
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
    travel = TravelTimes(build_base_matrix(points, model, osrm=osrm, cache=cache), model, traffic)
    states = [initial_state(engineer, k) for k, engineer in enumerate(engineers)]
    open_ids = [r.id for r in located if r.status == RequestStatus.ACTIVE]
    return Problem(located, list(engineers), travel, states, open_ids, unplannable)
