"""Доменная модель. Она же схема JSON-бандла и API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.enums import (
    EventType,
    Priority,
    ReasonCode,
    RequestStatus,
    RequestTier,
    Skill,
    Transport,
)
from app.domain.timeutil import HHMM

GeocodePrecision = Literal["house", "street", "locality", "none"]

# Дневной запас оборудования бригады: сколько единиц (роутеров, приставок, колонок) она берёт в офисе утром
# на весь день (ответ организаторов, вопрос 4; «допустимый запас можно определить самостоятельно»).
# Шесть единиц выбраны по настоящим дням из выгрузки: ни в одном регионе диспетчеры не давали бригаде больше
# пяти заявок с оборудованием (максимумы по регионам 5, 4, 2 и 4), так что запас покрывает любой реальный день
# с единицей в запасе. При этом он не бесплатный: на Востоке, где 40 заявок с оборудованием на 12 бригад,
# оптимум без ограничения складывает в одну бригаду семь единиц (tests/test_equipment_east.py).
DEFAULT_EQUIPMENT_STOCK = 6


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
    # Приоритет распределения по роду работ: авария важнее подключения, подключение важнее ремонта и дозаказа.
    # Ставится по типу заявки BK при сборке бандла. В старых бандлах поля нет — тогда это нижний уровень.
    tier: RequestTier = RequestTier.ROUTINE
    # «Как можно скорее»: окно заявки задаёт backend, от времени события до самого позднего конца смен.
    asap: bool = False
    skill: Skill
    transport_required: Transport | None = None
    status: RequestStatus = RequestStatus.ACTIVE
    source_type_bk: str = ""
    source_type_hd: str = ""
    # Нужно привезти единицу оборудования: роутер, приставку или колонку. Заявка тратит одну единицу дневного
    # запаса бригады (Engineer.equipment_stock). В старых бандлах поля нет — тогда оборудование не нужно.
    needs_equipment: bool = False
    # Диспетчер закрепил заявку за этой бригадой («Переназначение заявки»): солверы не отдают её другим. Закрепление
    # снимается, когда бригада больше не может взять заявку. Заполняет backend по событию, у заявок дня поле пустое.
    fixed_engineer_id: str | None = None

    @model_validator(mode="after")
    def _window_order(self) -> Request:
        # Окно из запроса у заявки «как можно скорее» не используется: клиент может прислать любое.
        if not self.asap and self.window_end < self.window_start:
            raise ValueError("конец временного окна раньше начала")
        return self


def dispatch_order(request: Request) -> int:
    """Очередь заявки, когда ресурсов дня не хватает на всех: чем меньше, тем важнее.

    0 — заявка, закреплённая диспетчером: его выбор важнее любой оптимизации. 1 — верхний уровень: авария из
    данных и срочная заявка диспетчера. 2 — подключение. 3 — ремонт, дозаказ и всё остальное. По этому порядку
    живут и штраф за снятую заявку в цели OR-Tools, и жадная починка маршрутов, и вставка заявки в маршрут при
    событии, поэтому они не могут разойтись.
    """
    if request.fixed_engineer_id is not None:
        return 0
    if request.priority == Priority.URGENT or request.tier == RequestTier.EMERGENCY:
        return 1
    return 2 if request.tier == RequestTier.CONNECTION else 3


# Ожидание срочной заявки «как можно скорее» (начало работы минус начало окна) до 4 часов не штрафуется.
ASAP_FREE_WAIT_MIN = 240

# Обед по плану: 45 минут между визитами, начало не раньше чем через 3 часа и не позже чем через 6 часов после
# начала смены (для смены 10:00–22:00 с 13:00 до 16:00). При рабочем дне короче 6 часов обеда в плане нет.
LUNCH_MIN = 45
LUNCH_EARLIEST_MIN = 180
LUNCH_LATEST_MIN = 360
LUNCH_WORKDAY_MIN = 360


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
    # Оборудование бригада получает в офисе утром сразу на весь день: сколько единиц она увезла с собой.
    # Каждая заявка с needs_equipment тратит одну. В старых бандлах поля нет — тогда запас по умолчанию.
    equipment_stock: int = Field(default=DEFAULT_EQUIPMENT_STOCK, ge=0)

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
    # Переназначение заявки: request_id уходит бригаде engineer_id. previous_engineer_id (бригада заявки в плане до
    # события, null — заявка была без инженера) заполняет backend.
    previous_engineer_id: str | None = None

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
        if self.type == EventType.REQUEST_REASSIGNED and (not self.request_id or not self.engineer_id):
            raise ValueError("для переназначения заявки нужны request_id и engineer_id")
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


class Lunch(BaseModel):
    start: HHMM
    end: HHMM


class Route(BaseModel):
    engineer_id: str
    visits: list[Visit] = Field(default_factory=list)
    total_km: float = 0.0
    total_travel_min: int = 0
    # Обед инженера в наших планах; null у инженера без визитов, при коротком дне и в плане диспетчеров.
    lunch: Lunch | None = None


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
    # Поле cancellations старых бандлов (отмены дня на шкале) игнорируется: в реальных данных клиент отменял уже
    # после приезда инженера, время и дорога потрачены, поэтому заявка со статусом «Отменена» — обычная заявка дня.

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
