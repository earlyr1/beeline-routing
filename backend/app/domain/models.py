"""Доменная модель. Она же схема JSON-бандла и API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import EventType, Priority, ReasonCode, RequestStatus, Skill, Transport
from app.domain.timeutil import HHMM

GeocodePrecision = Literal["house", "street", "locality", "none"]


class Request(BaseModel):
    id: str
    address: str
    lat: float | None = None
    lon: float | None = None
    geocode_precision: GeocodePrecision = "none"
    district: str = ""
    duration_min: int = Field(gt=0)
    window_start: HHMM
    window_end: HHMM
    priority: Priority = Priority.NORMAL
    # «Как можно скорее»: окно заявки задаёт backend, от времени события до самого позднего конца смен.
    asap: bool = False
    skill: Skill
    transport_required: Transport | None = None
    status: RequestStatus = RequestStatus.ACTIVE
    source_type_bk: str = ""
    source_type_hd: str = ""

    @model_validator(mode="after")
    def _window_order(self) -> Request:
        # Окно из запроса у заявки «как можно скорее» не используется: клиент может прислать любое.
        if not self.asap and self.window_end < self.window_start:
            raise ValueError("конец временного окна раньше начала")
        return self


# Ожидание срочной заявки «как можно скорее» (начало работы минус начало окна) до 4 часов не штрафуется.
ASAP_FREE_WAIT_MIN = 240


class Engineer(BaseModel):
    id: str
    name: str
    start_lat: float
    start_lon: float
    shift_start: HHMM
    shift_end: HHMM
    skills: list[Skill] = Field(min_length=1, max_length=3)
    transport: Transport
    available: bool = True
    unavailable_from: HHMM | None = None

    @model_validator(mode="after")
    def _shift_order(self) -> Engineer:
        if self.shift_end <= self.shift_start:
            raise ValueError("конец смены должен быть позже начала")
        return self


class Office(BaseModel):
    region: str
    title: str
    address: str
    lat: float
    lon: float


class Event(BaseModel):
    type: EventType
    time: HHMM
    request: Request | None = None
    request_id: str | None = None
    engineer_id: str | None = None
    # Смена транспорта: transport — новый транспорт от клиента, previous_transport заполняет backend.
    transport: Transport | None = None
    previous_transport: Transport | None = None
    # Изменение заявки: request — заявка с желаемыми значениями, previous_request (до изменения) заполняет backend.
    previous_request: Request | None = None
    # Задержка инженера: на сколько минут задерживается инженер engineer_id.
    delay_min: int | None = None

    @model_validator(mode="after")
    def _payload(self) -> Event:
        if self.type == EventType.ENGINEER_DELAYED:
            if not self.engineer_id or self.delay_min is None:
                raise ValueError("для задержки инженера нужны engineer_id и delay_min")
            if not MIN_DELAY_MIN <= self.delay_min <= MAX_DELAY_MIN:
                raise ValueError(DELAY_RANGE_TEXT)
        if self.type == EventType.URGENT and self.request is None:
            raise ValueError("для срочной заявки нужен полный набор полей заявки")
        if self.type in (EventType.CANCEL, EventType.RESTORE) and not self.request_id:
            raise ValueError("для отмены или возврата нужен request_id")
        if self.type == EventType.ENGINEER_UNAVAILABLE and not self.engineer_id:
            raise ValueError("для недоступности инженера нужен engineer_id")
        if self.type == EventType.ENGINEER_TRANSPORT_CHANGED and (
            not self.engineer_id or self.transport is None
        ):
            raise ValueError("для смены транспорта нужны engineer_id и transport")
        if self.type == EventType.REQUEST_UPDATED:
            if not self.request_id or self.request is None:
                raise ValueError("для изменения заявки нужны request_id и request")
            if self.request.id != self.request_id:
                raise ValueError("номер заявки в request_id и request.id не совпадает")
        return self


MIN_DELAY_MIN = 5
MAX_DELAY_MIN = 480
DELAY_RANGE_TEXT = f"задержка должна быть от {MIN_DELAY_MIN} до {MAX_DELAY_MIN} минут"


class Visit(BaseModel):
    request_id: str
    arrival: HHMM
    start: HHMM
    end: HHMM
    leg_km: float
    leg_min: int
    late_min: int = 0
    pinned: bool = False


class Route(BaseModel):
    engineer_id: str
    visits: list[Visit] = Field(default_factory=list)
    total_km: float = 0.0
    total_travel_min: int = 0


class Unassigned(BaseModel):
    request_id: str
    reason_code: ReasonCode
    reason_text: str


class Metrics(BaseModel):
    engineers_used: int
    km_per_engineer: dict[str, float]
    total_km: float
    assigned: int
    unassigned: int
    violations: int = 0


class Plan(BaseModel):
    solver: str
    routes: list[Route]
    unassigned: list[Unassigned]
    metrics: Metrics
    violations: list[str] = Field(default_factory=list)


class Bundle(BaseModel):
    region: str
    office: Office
    requests: list[Request]
    engineers: list[Engineer]
    events: list[Event] = Field(default_factory=list)
    control_plan: Plan | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> Bundle:
        for label, ids in (
            ("заявок", [request.id for request in self.requests]),
            ("инженеров", [engineer.id for engineer in self.engineers]),
        ):
            repeated = _repeated(ids)
            if repeated:
                raise ValueError(f"повторяются номера {label}: {_listed(repeated)}")
        # Заявка «как можно скорее» не проверяет порядок окна: в событии backend заменяет окно сам, а заявку
        # из бандла солвер берёт как есть.
        inverted = [request.id for request in self.requests if request.window_end < request.window_start]
        if inverted:
            raise ValueError(f"конец временного окна раньше начала у заявок: {_listed(inverted)}")
        return self


MAX_REPEATED_SHOWN = 10


def _listed(ids: list[str]) -> str:
    shown = ", ".join(ids[:MAX_REPEATED_SHOWN])
    if len(ids) > MAX_REPEATED_SHOWN:
        shown += f" и ещё {len(ids) - MAX_REPEATED_SHOWN}"
    return shown


def _repeated(ids: list[str]) -> list[str]:
    """Номера, встретившиеся больше одного раза, в порядке первого повтора."""
    seen: set[str] = set()
    repeated: dict[str, None] = {}
    for item in ids:
        if item in seen:
            repeated[item] = None
        seen.add(item)
    return list(repeated)
