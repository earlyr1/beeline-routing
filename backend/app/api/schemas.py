"""Модели ответов API (docs/superpowers/specs/2026-09-15-api-contract.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

from app.domain.enums import Transport
from app.domain.models import Engineer, Event, Office, Plan, Request
from app.domain.timeutil import HHMM
from app.planning.models import AppliedEvent, EventChoice, EventVariant, PlanDiff, PrecomputedPlan
from app.planning.session import PlanningSession
from app.planning.timeline import CURSOR_RANGE_TEXT, LAST_MINUTE, TimelineStatus
from app.planning.workload import WORKLOAD_LEVEL_TEXT, is_workload_level
from app.synth.work_types import WorkType

DatasetStatusValue = Literal["processing", "ready", "failed"]
DatasetStage = Literal["parsing", "geocoding", "matrix", "solving", "ready"]


class GeocodingCounts(BaseModel):
    house: int = 0
    street: int = 0
    locality: int = 0
    none: int = 0


class NotFoundAddress(BaseModel):
    request_id: str
    address: str


class UploadReport(BaseModel):
    region: str
    region_title: str
    source: Literal["beeline_csv", "bundle"]
    requests: int
    engineers: int
    skipped_rows: list[str] = Field(default_factory=list)
    geocoding: GeocodingCounts
    not_found: list[NotFoundAddress] = Field(default_factory=list)
    matrix_source: Literal["osrm", "haversine"]


class Progress(BaseModel):
    done: int
    total: int


class DatasetStatus(BaseModel):
    dataset_id: str
    status: DatasetStatusValue
    stage: DatasetStage
    progress: Progress
    report: UploadReport | None = None
    error: str | None = None


class PlanRequest(BaseModel):
    """Тело POST /plan. Поле, которого нет в теле (или всё тело), остаётся значением сессии."""

    workload_level: StrictInt | None = None
    lunch: StrictBool | None = None  # обед по плану

    @model_validator(mode="after")
    def _known_level(self) -> PlanRequest:
        # Ошибка уровня модели, а не поля: диспетчер видит только текст, без имени поля.
        if self.workload_level is not None and not is_workload_level(self.workload_level):
            raise ValueError(WORKLOAD_LEVEL_TEXT)
        return self


class CursorRequest(BaseModel):
    """Тело POST /cursor: текущее время плана."""

    time: HHMM

    @model_validator(mode="after")
    def _within_day(self) -> CursorRequest:
        # Ошибка уровня модели, а не поля: диспетчер видит только текст, без имени поля.
        if self.time > LAST_MINUTE:
            raise ValueError(CURSOR_RANGE_TEXT)
        return self


class TimelineItem(BaseModel):
    """Событие на шкале: применённое событие, если оно применено, иначе запланированное."""

    id: str
    event: Event
    status: TimelineStatus
    reason: str | None = None
    # Стратегия «ломающего» события («optimal», «stable», «keep» или «assign:<инженер>»); null — не выбрана
    # или событие не «ломающее».
    variant: EventVariant | None = None
    # «Ломающее» событие: для него сервер предлагает варианты исправления.
    choosable: bool = False


class VariantRequest(BaseModel):
    """Тело PUT …/timeline/events/{id}/variant. Стратегию проверяет обработчик (check_variant)."""

    variant: EventVariant


class MorningVisit(BaseModel):
    """Визит утреннего плана: время и бригада, которые клиент услышал бы до событий дня."""

    request_id: str
    engineer_id: str
    start: HHMM


class PlanningState(BaseModel):
    dataset_id: str
    version: int
    workload_level: int
    lunch_enabled: bool
    region: str
    office: Office
    now: HHMM
    requests: list[Request]
    engineers: list[Engineer]
    plan: Plan
    previous_plan: Plan | None = None
    baseline: Plan
    control: Plan | None = None
    last_diff: PlanDiff | None = None
    # Утренний план дня (текущее время 00:00, ни одного события шкалы) — только время и бригада каждого визита:
    # с ним вкладка «Коммуникации» сравнивает план, когда клиенту ещё ничего не говорили.
    morning: list[MorningVisit] = Field(default_factory=list)
    events: list[AppliedEvent] = Field(default_factory=list)
    matrix_source: Literal["osrm", "haversine"]
    # Текущее время плана. now, events, last_diff и previous_plan относятся к плану на это время.
    cursor: HHMM = 0
    timeline: list[TimelineItem] = Field(default_factory=list)
    # Все шаги таймлайна посчитаны: статусы событий впереди окончательные.
    timeline_ready: bool = True
    # «Ломающее» событие, на котором остановилось текущее время, и его варианты; null — выбирать нечего.
    pending_choice: EventChoice | None = None
    # Откуда утренний план дня: посчитан заранее ночным расчётом (сколько шёл поиск и когда закончился) или null —
    # найден при загрузке дня. События дня пересчитываются от утреннего плана на месте в обоих случаях.
    precomputed: PrecomputedPlan | None = None


class RouteLeg(BaseModel):
    to_request_id: str
    coordinates: list[list[float]]


class RouteGeometry(BaseModel):
    # Версия плана, по которому построены линии.
    version: int
    engineer_id: str
    transport: Transport
    source: Literal["osrm", "straight"]
    legs: list[RouteLeg]


class PointAddress(BaseModel):
    """Адрес точки на карте для ручной заявки; address — null, если адрес неизвестен."""

    address: str | None
    precision: Literal["house", "street", "locality", "none"]


class ClientConfig(BaseModel):
    yandex_maps_api_key: str | None
    llm_enabled: bool
    osrm_available: bool
    # Типы работ диалога срочной заявки с нормативами организаторов; первый — выбор по умолчанию («Авария»).
    work_types: list[WorkType] = Field(default_factory=list)


def morning_visits(plan: Plan | None) -> list[MorningVisit]:
    """Время и бригада каждого визита утреннего плана; пустой список, если утреннего плана нет."""
    return [
        MorningVisit(request_id=visit.request_id, engineer_id=route.engineer_id, start=visit.start)
        for route in (plan.routes if plan is not None else [])
        for visit in route.visits
    ]


def to_planning_state(
    session: PlanningSession,
    *,
    cursor: int | None = None,
    timeline: list[TimelineItem] | None = None,
    timeline_ready: bool = True,
    pending_choice: EventChoice | None = None,
    morning: Plan | None = None,
) -> PlanningState:
    """Состояние на текущее время cursor (по умолчанию время последнего события сессии).

    morning — утренний план дня: из него в ответ идут время и бригада визитов, а не весь план.
    """
    return PlanningState(
        dataset_id=session.dataset_id,
        version=session.version,
        workload_level=session.workload_level,
        lunch_enabled=session.lunch_enabled,
        region=session.region,
        office=session.office,
        now=session.now,
        requests=session.requests,
        engineers=session.engineers,
        plan=session.plan,
        previous_plan=session.previous_plan,
        baseline=session.baseline,
        control=session.control,
        last_diff=session.last_diff,
        morning=morning_visits(morning),
        events=session.events,
        matrix_source=session.problem.travel.base.source,
        cursor=session.now if cursor is None else cursor,
        timeline=timeline or [],
        timeline_ready=timeline_ready,
        pending_choice=pending_choice,
        precomputed=session.precomputed,
    )
