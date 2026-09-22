"""Модели ответов API (docs/superpowers/specs/2026-09-15-api-contract.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

from app.domain.enums import Transport
from app.domain.models import Engineer, Event, Office, Plan, Request
from app.domain.timeutil import HHMM
from app.domain.windows import TimeSlot
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


class ScenarioInfo(BaseModel):
    """Подготовленный регион для кнопки на экране загрузки: день из data/bundles открывается одним нажатием."""

    region: str
    title: str
    requests: int
    engineers: int
    # Выгрузки Билайна по региону нет, данные сгенерированы нами (docs/assumptions.md): кнопка говорит об этом.
    generated: bool


class UploadReport(BaseModel):
    region: str
    region_title: str
    # Откуда день: разобранная выгрузка Билайна, загруженный бандл JSON или подготовленный регион по кнопке.
    source: Literal["beeline_csv", "bundle", "scenario"]
    # Регион сгенерирован нами, выгрузки Билайна по нему нет (docs/assumptions.md): отчёт говорит об этом
    # и после кнопки региона, и после загрузки его файла.
    generated: bool = False
    requests: int
    engineers: int
    skipped_rows: list[str] = Field(default_factory=list)
    # Что не так с окнами самих данных (app/ingest/window_check.py): окно вне рабочего дня, короткое окно
    # приезда, окна не по сетке. Окна при этом остаются как в файле — список только показывает их диспетчеру.
    window_warnings: list[str] = Field(default_factory=list)
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


class MorningRequest(BaseModel):
    """Заявка в начале дня: окно, которое знает клиент, и визит утреннего плана, если он был.

    Окно клиенту называют, когда заявку принимают, поэтому до событий дня он знает именно это окно —
    с ним вкладка «Коммуникации» сравнивает план. Время визита клиенту не называют (диспетчер обещает окно),
    но утренние время и бригада в ответе остаются: по ним видно, как день начинался.
    """

    request_id: str
    window_start: HHMM
    window_end: HHMM
    # Визит утреннего плана; null — утром заявка в маршруты не попала.
    engineer_id: str | None = None
    start: HHMM | None = None


class PlanningState(BaseModel):
    dataset_id: str
    version: int
    workload_level: int
    lunch_enabled: bool
    region: str
    # Регион сгенерирован нами: «диспетчеры» в нём — наша эвристика, а не решения людей (docs/assumptions.md).
    # Вкладка «Сравнение» подписывает их колонку по этому полю.
    generated: bool = False
    office: Office
    now: HHMM
    requests: list[Request]
    engineers: list[Engineer]
    plan: Plan
    previous_plan: Plan | None = None
    baseline: Plan
    control: Plan | None = None
    last_diff: PlanDiff | None = None
    # Начало дня (текущее время 00:00, ни одного события шкалы): окно каждой заявки и её утренний визит.
    # С окнами отсюда вкладка «Коммуникации» сравнивает план, когда клиенту ещё не звонили.
    morning: list[MorningRequest] = Field(default_factory=list)
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
    # Сетка окон визита из того же конфига: слоты, из которых диспетчер выбирает окно клиенту. Клиент их не
    # повторяет у себя, а берёт отсюда — так сетка не может разойтись с той, по которой сервер проверяет окно.
    window_grid: list[TimeSlot] = Field(default_factory=list)


def morning_requests(morning: PlanningSession | None) -> list[MorningRequest]:
    """Окно каждой заявки начала дня и её утренний визит; пустой список, если утреннего плана нет.

    Заявка без утреннего визита в список тоже попадает: её окно клиент знает так же, как окно любой другой.
    """
    if morning is None:
        return []
    visits = {
        visit.request_id: (route.engineer_id, visit.start)
        for route in morning.plan.routes
        for visit in route.visits
    }
    return [
        MorningRequest(
            request_id=request.id,
            window_start=request.window_start,
            window_end=request.window_end,
            engineer_id=visits.get(request.id, (None, None))[0],
            start=visits.get(request.id, (None, None))[1],
        )
        for request in morning.requests
    ]


def to_planning_state(
    session: PlanningSession,
    *,
    cursor: int | None = None,
    timeline: list[TimelineItem] | None = None,
    timeline_ready: bool = True,
    pending_choice: EventChoice | None = None,
    morning: PlanningSession | None = None,
    generated: bool = False,
) -> PlanningState:
    """Состояние на текущее время cursor (по умолчанию время последнего события сессии).

    morning — сессия начала дня: из неё в ответ идут окна заявок и визиты утреннего плана, а не она целиком.
    generated — регион сгенерирован нами: об этом говорит вкладка «Сравнение».
    """
    return PlanningState(
        dataset_id=session.dataset_id,
        version=session.version,
        workload_level=session.workload_level,
        lunch_enabled=session.lunch_enabled,
        region=session.region,
        generated=generated,
        office=session.office,
        now=session.now,
        requests=session.requests,
        engineers=session.engineers,
        plan=session.plan,
        previous_plan=session.previous_plan,
        baseline=session.baseline,
        control=session.control,
        last_diff=session.last_diff,
        morning=morning_requests(morning),
        events=session.events,
        matrix_source=session.problem.travel.base.source,
        cursor=session.now if cursor is None else cursor,
        timeline=timeline or [],
        timeline_ready=timeline_ready,
        pending_choice=pending_choice,
        precomputed=session.precomputed,
    )
