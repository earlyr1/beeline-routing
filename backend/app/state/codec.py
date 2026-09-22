"""День диспетчера в jsonb и обратно: единственное место, которое знает форму хранимых данных.

Снимок сессии (SessionSnapshot) — это PlanningSession без двух вещей. Без матрицы дороги: она чистая функция
от точек дня, 278 КБ на шаг против нуля в базе, и на восстановлении её собирает тот же make_problem из kv-кэша.
И без контрольного плана, региона и офиса: они у дня одни и лежат в days.prepared. Остальное сохраняется как
есть, чтобы после перезапуска диспетчер увидел ТОТ ЖЕ план, а не пересчитанный: солвер ограничен по времени
и второй раз найдёт другой.

Поля Problem, которые меняются по ходу дня (закреплённая работа, доступность бригад, прежние назначения),
сохраняются; остальное — заявки с координатами, бригады, запас на дорогу, обед — восстанавливается ровно так,
как это делает make_problem на тех же входных данных.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace

from pydantic import BaseModel, Field

from app.api.registry import PreparedDay
from app.domain.models import Engineer, Event, Lunch, Office, Plan, Request, Unassigned, Visit
from app.geo.matrix import TravelTimes
from app.ingest.geocode import GeoResult
from app.planning.models import AppliedEvent, EventVariant, PlanDiff, PrecomputedPlan
from app.planning.session import PlanningContext, PlanningSession, day_problem
from app.planning.timeline import TimelineEntry, TimelineStep
from app.solvers.problem import EngineerState

logger = logging.getLogger(__name__)


def _without_nulls(value: object) -> object:
    """Тот же разобранный JSON, но без байтов \\x00 в строках — и в ключах объектов тоже."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    if isinstance(value, dict):
        return {_without_nulls(key): _without_nulls(item) for key, item in value.items()}
    return value


def clean_json(raw: str) -> str:
    """JSON, который примет jsonb: из строк убран \\x00.

    Постгресовый text (а jsonb — это text внутри) нулевого байта не держит вовсе, и день с таким байтом
    в адресе не сохранился бы целиком — а байт этот приезжает из настоящей выгрузки, через parse_beeline_csv.
    Убираем на выходе кодека: одно место вместо валидатора на каждом текстовом поле десятка моделей.

    Искать нужно не сам байт, а его запись в JSON: model_dump_json отдаёт его как escape-последовательность
    \\u0000, и наивный replace по строке JSON не нашёл бы ничего. Поэтому редкий случай разбирается и
    собирается заново, а обычный — проверка подстроки и возврат как есть.

    День в памяти процесса байт при этом сохраняет: убрать его и оттуда значило бы чинить текст в десятке
    точек входа (выгрузка, бандл, адрес срочной заявки, ответ помощника). Разница видна только после
    перезапуска и только в тексте, который Postgres и так хранить не умеет.
    """
    if "\\u0000" not in raw:
        return raw
    return json.dumps(_without_nulls(json.loads(raw)), ensure_ascii=False)


def clean_text(value: str | None) -> str | None:
    """То же для обычной text-колонки, где escape-последовательности нет и байт лежит как есть."""
    return None if value is None else value.replace("\x00", "")


def dump_model(model: BaseModel) -> str:
    """Модель pydantic в jsonb: то же самое, что model_dump_json, только пригодное для записи."""
    return clean_json(model.model_dump_json())


class GeoSnapshot(BaseModel):
    """Ответ геокодера, сохранённый вместе с событием шкалы: повторное применение геокодер не зовёт."""

    lat: float | None = None
    lon: float | None = None
    precision: str = "none"
    query: str | None = None


class EngineerStateSnapshot(BaseModel):
    """Бригада на момент пересчёта: откуда продолжает день, когда свободна и сколько оборудования осталось."""

    engineer_id: str
    start_node: int
    available_from: int
    available_until: int
    equipment_left: int


class ProblemSnapshot(BaseModel):
    """Состояние задачи, накопленное событиями дня: матрица и входные данные сюда не входят."""

    states: list[EngineerStateSnapshot] = Field(default_factory=list)
    open_request_ids: list[str] = Field(default_factory=list)
    unplannable: list[Unassigned] = Field(default_factory=list)
    pinned: dict[str, list[Visit]] = Field(default_factory=dict)
    previous_assignment: dict[str, str] = Field(default_factory=dict)
    previous_order: dict[str, list[str]] = Field(default_factory=dict)
    previous_start: dict[str, int] = Field(default_factory=dict)
    pinned_lunch: dict[str, Lunch] = Field(default_factory=dict)
    now: int = 0
    lunch: bool = True


class SessionSnapshot(BaseModel):
    """План на один момент дня целиком, кроме матрицы дороги и того, что лежит в days.prepared."""

    requests: list[Request]
    engineers: list[Engineer]
    plan: Plan
    baseline: Plan
    previous_plan: Plan | None = None
    last_diff: PlanDiff | None = None
    events: list[AppliedEvent] = Field(default_factory=list)
    now: int = 0
    version: int = 1
    workload_level: int
    lunch_enabled: bool = True
    precomputed: PrecomputedPlan | None = None
    problem: ProblemSnapshot
    # Откуда была матрица дороги, когда план считали: «osrm» или «haversine». При восстановлении сверяется
    # с пересобранной — расхождение уходит в лог (сам план от матрицы уже не зависит, он посчитан).
    matrix_source: str = "osrm"


class PreparedSnapshot(BaseModel):
    """Входные данные дня: то, от чего считается утренний план, и контрольное распределение диспетчеров."""

    region: str
    region_title: str
    office: Office
    requests: list[Request]
    engineers: list[Engineer]
    control: Plan | None = None
    generated: bool = False


class MatrixCache:
    """Матрицы дороги, собранные при подъёме дня, по набору точек.

    У шагов одного дня точки чаще всего одни и те же (событие редко меняет адрес), поэтому матрица собирается
    один раз на набор, а не на каждый из полусотни шагов: иначе подъём дня читал бы из kv-кэша и разбирал
    десятки мегабайт одного и того же.
    """

    def __init__(self) -> None:
        self._items: dict[tuple[tuple[float, float], ...], TravelTimes] = {}

    def travel(self, requests: Sequence[Request], engineers: Sequence[Engineer]) -> TravelTimes | None:
        return self._items.get(_points_key(requests, engineers))

    def remember(
        self, requests: Sequence[Request], engineers: Sequence[Engineer], travel: TravelTimes
    ) -> None:
        self._items[_points_key(requests, engineers)] = travel


def _points_key(
    requests: Sequence[Request], engineers: Sequence[Engineer]
) -> tuple[tuple[float, float], ...]:
    """Точки задачи в том же порядке, в каком их выстраивает make_problem: бригады, потом заявки с координатами."""
    return (
        *((engineer.start_lat, engineer.start_lon) for engineer in engineers),
        *((r.lat, r.lon) for r in requests if r.lat is not None and r.lon is not None),
    )


def dump_prepared(prepared: PreparedDay) -> str:
    return dump_model(
        PreparedSnapshot(
            region=prepared.region,
            region_title=prepared.region_title,
            office=prepared.office,
            requests=prepared.requests,
            engineers=prepared.engineers,
            control=prepared.control,
            generated=prepared.generated,
        )
    )


def load_prepared(raw: str) -> PreparedDay:
    snapshot = PreparedSnapshot.model_validate_json(raw)
    return PreparedDay(
        snapshot.region,
        snapshot.region_title,
        snapshot.office,
        snapshot.requests,
        snapshot.engineers,
        snapshot.control,
        snapshot.generated,
    )


def dump_session(session: PlanningSession) -> str:
    problem = session.problem
    return dump_model(
        SessionSnapshot(
            requests=session.requests,
            engineers=session.engineers,
            plan=session.plan,
            baseline=session.baseline,
            previous_plan=session.previous_plan,
            last_diff=session.last_diff,
            events=session.events,
            now=session.now,
            version=session.version,
            workload_level=session.workload_level,
            lunch_enabled=session.lunch_enabled,
            precomputed=session.precomputed,
            problem=ProblemSnapshot(
                states=[
                    EngineerStateSnapshot(
                        engineer_id=state.engineer.id,
                        start_node=state.start_node,
                        available_from=state.available_from,
                        available_until=state.available_until,
                        equipment_left=state.equipment_left,
                    )
                    for state in problem.states
                ],
                open_request_ids=problem.open_request_ids,
                unplannable=problem.unplannable,
                pinned=problem.pinned,
                previous_assignment=problem.previous_assignment,
                previous_order=problem.previous_order,
                previous_start=problem.previous_start,
                pinned_lunch=problem.pinned_lunch,
                now=problem.now,
                lunch=problem.lunch,
            ),
            matrix_source=problem.travel.base.source,
        )
    )


def load_session(
    raw: str,
    *,
    dataset_id: str,
    prepared: PreparedDay,
    ctx: PlanningContext,
    matrices: MatrixCache | None = None,
) -> PlanningSession:
    """Сессия из снимка: матрица дороги пересобирается, остальное берётся как записано.

    Регион, офис и контрольный план — из входных данных дня: у дня они одни на все его планы.
    """
    snapshot = SessionSnapshot.model_validate_json(raw)
    base = day_problem(
        snapshot.requests,
        snapshot.engineers,
        ctx,
        snapshot.workload_level,
        snapshot.lunch_enabled,
        travel=matrices.travel(snapshot.requests, snapshot.engineers) if matrices is not None else None,
    )
    if matrices is not None:
        matrices.remember(snapshot.requests, snapshot.engineers, base.travel)
    if base.travel.base.source != snapshot.matrix_source:
        # План уже посчитан и не изменится, но производные числа (линии маршрутов, лишние километры
        # в объяснении) считаются по этой матрице. Такое бывает, только если пропали и kv-кэш, и OSRM.
        logger.warning(
            "День %s поднят из базы с матрицей «%s», а план считали на «%s»: линии маршрутов и километры "
            "в объяснениях будут по формуле.",
            dataset_id,
            base.travel.base.source,
            snapshot.matrix_source,
        )
    engineers = {engineer.id: engineer for engineer in base.engineers}
    problem = replace(
        base,
        states=[
            EngineerState(
                engineers[state.engineer_id],
                state.start_node,
                state.available_from,
                state.available_until,
                state.equipment_left,
            )
            for state in snapshot.problem.states
            if state.engineer_id in engineers
        ],
        open_request_ids=snapshot.problem.open_request_ids,
        unplannable=snapshot.problem.unplannable,
        pinned=snapshot.problem.pinned,
        previous_assignment=snapshot.problem.previous_assignment,
        previous_order=snapshot.problem.previous_order,
        previous_start=snapshot.problem.previous_start,
        pinned_lunch=snapshot.problem.pinned_lunch,
        now=snapshot.problem.now,
        # Обед сегодня по ходу дня не меняется, но поле сохраняется — значит, и читается: иначе событие,
        # которое его однажды тронет, молча потеряет его при перезапуске.
        lunch=snapshot.problem.lunch,
    )
    return PlanningSession(
        dataset_id=dataset_id,
        region=prepared.region,
        office=prepared.office,
        requests=snapshot.requests,
        engineers=snapshot.engineers,
        control=prepared.control,
        problem=problem,
        plan=snapshot.plan,
        baseline=snapshot.baseline,
        previous_plan=snapshot.previous_plan,
        last_diff=snapshot.last_diff,
        events=snapshot.events,
        now=snapshot.now,
        version=snapshot.version,
        workload_level=snapshot.workload_level,
        lunch_enabled=snapshot.lunch_enabled,
        precomputed=snapshot.precomputed,
    )


def dump_event(event: Event) -> str:
    return dump_model(event)


def load_event(raw: str) -> Event:
    return Event.model_validate_json(raw)


class GeoAnswers(BaseModel):
    """Ответы геокодера события в jsonb: обёртка нужна, чтобы у значения был один разбор на всё."""

    answers: dict[str, GeoSnapshot] = Field(default_factory=dict)


def dump_geo(geo: Mapping[str, GeoResult]) -> str:
    answers = {
        address: GeoSnapshot(lat=item.lat, lon=item.lon, precision=item.precision, query=item.query)
        for address, item in geo.items()
    }
    return dump_model(GeoAnswers(answers=answers))


def load_geo(raw: str) -> dict[str, GeoResult]:
    answers = GeoAnswers.model_validate_json(raw).answers
    return {
        address: GeoResult(item.lat, item.lon, item.precision, item.query)
        for address, item in answers.items()
    }


def entry_from_row(
    entry_id: str, seq: int, event: str, geo: str, checked: bool, variant: EventVariant | None
) -> TimelineEntry:
    return TimelineEntry(
        id=entry_id, seq=seq, event=load_event(event), geo=load_geo(geo), checked=checked, variant=variant
    )


def step_from_row(session: PlanningSession, applied: str | None, reason: str | None) -> TimelineStep:
    return TimelineStep(
        session=session,
        applied=AppliedEvent.model_validate_json(applied) if applied is not None else None,
        reason=reason,
    )


def dump_applied(applied: AppliedEvent | None) -> str | None:
    return None if applied is None else dump_model(applied)
