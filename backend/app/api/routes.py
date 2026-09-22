from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi import Request as HttpRequest

from app.api.deps import AppDeps
from app.api.geometry import route_geometry
from app.api.ingest_service import preprocess_scenario, preprocess_upload, scenario_bundle, scenarios
from app.api.registry import DatasetRecord
from app.api.schemas import (
    ClientConfig,
    CursorRequest,
    DatasetStatus,
    PlanningState,
    PlanRequest,
    RouteGeometry,
    ScenarioInfo,
    VariantRequest,
)
from app.api.timeline import (
    VariantUnavailable,
    check_variant,
    ensure_precompute,
    event_choice,
    insert_and_replay,
    move_cursor,
    planning_state,
    settle,
)
from app.domain.models import Event
from app.domain.timeutil import fmt_hhmm
from app.planning.explain import build_explanation
from app.planning.models import EventChoice, Explanation
from app.planning.session import (
    EventRejected,
    PlanningSession,
    early_event_text,
    geocode_entry,
    start_session,
)
from app.planning.timeline import EVENT_TIME_RANGE_TEXT, LAST_MINUTE, check_known, known_requests
from app.planning.variants import is_choosable
from app.synth.work_types import urgent_work_types

router = APIRouter(prefix="/api")


def get_deps(request: HttpRequest) -> AppDeps:
    return request.app.state.deps


Deps = Annotated[AppDeps, Depends(get_deps)]


def _record(deps: AppDeps, dataset_id: str) -> DatasetRecord:
    record = deps.registry.get(dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Датасет {dataset_id} не найден.")
    return record


def _session(record: DatasetRecord) -> PlanningSession:
    if record.status == "failed":
        raise HTTPException(status_code=409, detail=f"Предподсчёт завершился ошибкой: {record.error}")
    if record.session is None:
        raise HTTPException(status_code=409, detail="Датасет ещё обрабатывается, план не готов.")
    return record.session


def _check_known(record: DatasetRecord, event: Event) -> None:
    try:
        check_known(record.base, record.timeline.entries, event)
    except EventRejected as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _check_variant(record: DatasetRecord, event: Event, variant: str) -> None:
    try:
        check_variant(record, event, variant)
    except VariantUnavailable as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/config", response_model=ClientConfig)
def client_config(deps: Deps) -> ClientConfig:
    return ClientConfig(
        yandex_maps_api_key=deps.settings.yandex_maps_api_key,
        llm_enabled=deps.llm is not None,
        osrm_available=deps.osrm.health() if deps.osrm is not None else False,
        # Нормативы из того же конфига, по которому собраны бандлы дня.
        work_types=urgent_work_types(deps.ingest.synth_config),
    )


@router.post("/upload", response_model=DatasetStatus, status_code=202)
def upload(background: BackgroundTasks, file: Annotated[UploadFile, File()], deps: Deps) -> DatasetStatus:
    filename = file.filename or ""
    if not filename.lower().endswith((".csv", ".json")):
        raise HTTPException(status_code=400, detail="Поддерживаются файлы .csv и .json.")
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Файл пустой.")
    record = deps.registry.create()
    background.add_task(preprocess_upload, record, filename, data, deps.ingest)
    return record.status_model()


@router.get("/scenarios", response_model=list[ScenarioInfo])
def scenario_list(deps: Deps) -> list[ScenarioInfo]:
    """Подготовленные регионы для кнопок экрана загрузки: заголовок, сколько заявок и бригад, наш ли это регион."""
    return scenarios(deps.ingest)


@router.post("/scenarios/{region}", response_model=DatasetStatus, status_code=202)
def start_scenario(region: str, background: BackgroundTasks, deps: Deps) -> DatasetStatus:
    """День подготовленного региона без выбора файла. Дальше всё как после загрузки: тот же датасет и тот же путь."""
    try:
        bundle = scenario_bundle(deps.ingest, region)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    record = deps.registry.create()
    background.add_task(preprocess_scenario, record, bundle, deps.ingest)
    return record.status_model()


@router.get("/datasets/{dataset_id}", response_model=DatasetStatus)
def dataset_status(dataset_id: str, deps: Deps) -> DatasetStatus:
    return _record(deps, dataset_id).status_model()


@router.post("/datasets/{dataset_id}/plan", response_model=PlanningState)
def build_plan(dataset_id: str, deps: Deps, body: PlanRequest | None = None) -> PlanningState:
    """План дня. С событиями на шкале, со сменой уровня нагрузки или обеда день пересчитывается с нуля.

    Поле workload_level или lunch, которого нет в теле, остаётся значением сессии. Пересборка очищает таймлайн,
    заново ставит на него отмены дня и ставит текущее время на 00:00. Если шкала не менялась со сборки дня (на ней
    только его отмены) и значения те же, возвращается предподсчитанный план без изменений, текущее время остаётся
    прежним.
    """
    record = _record(deps, dataset_id)
    with record.timeline_lock:
        with record.lock:
            session = _session(record)
            level = (
                session.workload_level if body is None or body.workload_level is None else body.workload_level
            )
            lunch = session.lunch_enabled if body is None or body.lunch is None else body.lunch
            if record.day_unchanged() and (level, lunch) == (
                session.workload_level,
                session.lunch_enabled,
            ):
                return planning_state(record)
            day = record.prepared
            version = record.next_version()
        # Пересборка идёт без record.lock: состояние, объяснения и линии маршрутов отвечают по прежнему плану.
        fresh = start_session(
            dataset_id,
            day.region,
            day.office,
            day.requests,
            day.engineers,
            day.control,
            deps.ingest.planning,
            workload_level=level,
            lunch_enabled=lunch,
        )
        record.start_day(replace(fresh, version=version))
        state = planning_state(record)
    # Шаги шкалы считаются в фоне, как и после любого события; у пустой шкалы шагов нет.
    ensure_precompute(record, deps.ingest.planning, deps.run_background)
    return state


@router.get("/datasets/{dataset_id}/state", response_model=PlanningState)
def get_state(dataset_id: str, deps: Deps) -> PlanningState:
    """Состояние на текущее время плана. Непосчитанные шаги шкалы (например, отмены дня) считаются в фоне."""
    record = _record(deps, dataset_id)
    with record.lock:
        _session(record)
        state = planning_state(record)
    ensure_precompute(record, deps.ingest.planning, deps.run_background)
    return state


@router.post("/datasets/{dataset_id}/events", response_model=PlanningState)
def post_event(dataset_id: str, event: Event, deps: Deps) -> PlanningState:
    """Событие в текущее время плана или позже: встаёт на шкалу, и текущее время переходит к нему.

    Событие раньше текущего времени отклоняется. События на шкале между текущим временем и новым событием
    применяются по пути. Отклонённое событие на шкале не остаётся, текущее время не меняется. «Ломающее» событие
    сразу получает стратегию optimal; если по пути есть «ломающее» событие без выбора, ответ 409.
    """
    record = _record(deps, dataset_id)
    ctx = deps.ingest.planning
    with record.lock:
        _session(record)
        requests = known_requests(record.base, record.timeline.entries)
    # Геокодер может отвечать долго: адрес срочной заявки и новый адрес изменённой заявки ищем до блокировок
    # датасета, чтобы не держать остальные запросы к нему. При пересчётах геокодер больше не вызывается, даже если
    # адрес не нашёлся.
    event, geo = geocode_entry(event, requests, ctx)
    try:
        with record.timeline_lock:
            with record.lock:
                _session(record)
                cursor = record.cursor
                if event.time < cursor:
                    raise HTTPException(status_code=422, detail=early_event_text(event.time, cursor))
                entry = record.timeline.create(event, geo, variant="optimal" if is_choosable(event) else None)
            step = insert_and_replay(record, ctx, entry)
            if step is None:
                with record.lock:
                    record.timeline.remove(entry.id)
                    awaiting = record.timeline.walk(record.base).awaiting
                when = fmt_hhmm(awaiting.event.time if awaiting is not None else cursor)
                raise HTTPException(status_code=409, detail=f"Сначала выберите вариант для события в {when}.")
            if step.reason is not None:
                raise HTTPException(status_code=422, detail=step.reason)
            settle(record, ctx, max(cursor, event.time))
            return planning_state(record)
    finally:
        ensure_precompute(record, ctx, deps.run_background)


@router.post("/datasets/{dataset_id}/timeline/events", response_model=PlanningState)
def add_timeline_event(
    dataset_id: str, event: Event, deps: Deps, variant: str | None = None
) -> PlanningState:
    """Событие на шкале в любое время дня; текущее время плана не меняется.

    Событие не позже текущего времени применяется сразу, план пересчитывается от его места. Если оно не
    применяется, на шкале его нет, а ответ 422 с причиной. Событие позже текущего времени ждёт своего времени, его
    шаг считается в фоне.

    variant — стратегия события сразу, без окна выбора: так диспетчер отменяет заявку отказавшегося клиента,
    выбирая между пересчётом остатка дня и «маршруты не трогать». Событию без стратегий ответ 409.
    """
    record = _record(deps, dataset_id)
    ctx = deps.ingest.planning
    if event.time > LAST_MINUTE:
        raise HTTPException(status_code=422, detail=EVENT_TIME_RANGE_TEXT)
    with record.lock:
        _session(record)
        _check_known(record, event)
        if variant is not None:
            _check_variant(record, event, variant)
        requests = known_requests(record.base, record.timeline.entries)
    event, geo = geocode_entry(event, requests, ctx)
    try:
        with record.timeline_lock:
            with record.lock:
                # Пока искали адрес, таймлайн мог измениться: номера проверяются ещё раз.
                _session(record)
                _check_known(record, event)
                cursor = record.cursor
                entry = record.timeline.create(event, geo, variant=variant)
            if event.time <= cursor:
                step = insert_and_replay(record, ctx, entry)
                if step is not None and step.reason is not None:
                    raise HTTPException(status_code=422, detail=step.reason)
            else:
                with record.lock:
                    record.timeline.insert(entry)
            settle(record, ctx)
            return planning_state(record)
    finally:
        ensure_precompute(record, ctx, deps.run_background)


@router.delete("/datasets/{dataset_id}/timeline/events/{entry_id}", response_model=PlanningState)
def delete_timeline_event(dataset_id: str, entry_id: str, deps: Deps) -> PlanningState:
    """Убирает событие со шкалы. Если оно было применено, план на текущее время пересчитывается от его места."""
    record = _record(deps, dataset_id)
    ctx = deps.ingest.planning
    try:
        with record.timeline_lock:
            with record.lock:
                _session(record)
                if record.timeline.remove(entry_id) is None:
                    raise HTTPException(status_code=404, detail=f"Событие {entry_id} не найдено.")
            settle(record, ctx)
            return planning_state(record)
    finally:
        ensure_precompute(record, ctx, deps.run_background)


@router.delete("/datasets/{dataset_id}/timeline", response_model=PlanningState)
def clear_timeline(dataset_id: str, deps: Deps) -> PlanningState:
    """Сброс событий: шкала пустеет, план — утренний план дня без пересчёта, текущее время 00:00.

    Нагрузка и обед остаются как у дня, решатель не запускается.
    """
    record = _record(deps, dataset_id)
    with record.timeline_lock:
        with record.lock:
            _session(record)
            record.start_day(record.base)
        return planning_state(record)


@router.get("/datasets/{dataset_id}/timeline/events/{entry_id}/variants", response_model=EventChoice)
def get_timeline_variants(
    dataset_id: str, entry_id: str, deps: Deps, assign: str | None = None
) -> EventChoice:
    """Варианты исправления для «ломающего» события шкалы: для окна выбора и смены выбора.

    assign — номер бригады: у срочной заявки к трём вариантам добавляется четвёртый, «отдать ей заявку».
    Такой вариант считается только по этому запросу, поэтому окно открывается без него.
    """
    record = _record(deps, dataset_id)
    with record.lock:
        _session(record)
    try:
        return event_choice(record, deps.ingest.planning, entry_id, assign)
    except VariantUnavailable as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error


@router.put("/datasets/{dataset_id}/timeline/events/{entry_id}/variant", response_model=PlanningState)
def put_timeline_variant(dataset_id: str, entry_id: str, body: VariantRequest, deps: Deps) -> PlanningState:
    """Выбор или смена стратегии события. План на текущее время пересчитывается с этого события."""
    record = _record(deps, dataset_id)
    ctx = deps.ingest.planning
    try:
        with record.timeline_lock:
            with record.lock:
                _session(record)
                entry = record.timeline.find(entry_id)
                if entry is None:
                    raise VariantUnavailable(404, f"Событие {entry_id} не найдено.")
                check_variant(record, entry.event, body.variant)
                record.timeline.set_variant(entry_id, body.variant)
            settle(record, ctx)
            return planning_state(record)
    except VariantUnavailable as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from error
    finally:
        ensure_precompute(record, ctx, deps.run_background)


@router.post("/datasets/{dataset_id}/cursor", response_model=PlanningState)
def post_cursor(dataset_id: str, body: CursorRequest, deps: Deps) -> PlanningState:
    """Переносит текущее время плана. Солвер нужен, только если планы до этого времени ещё не посчитаны."""
    record = _record(deps, dataset_id)
    ctx = deps.ingest.planning
    with record.lock:
        _session(record)
    move_cursor(record, ctx, body.time)
    state = planning_state(record)
    ensure_precompute(record, ctx, deps.run_background)
    return state


@router.get("/datasets/{dataset_id}/explain/{request_id}", response_model=Explanation)
def explain(dataset_id: str, request_id: str, deps: Deps) -> Explanation:
    record = _record(deps, dataset_id)
    with record.lock:
        session = _session(record)
    request = session.request(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Заявка {request_id} не найдена.")
    return build_explanation(session.problem, session.plan, request)


@router.get("/datasets/{dataset_id}/routes/{engineer_id}/geometry", response_model=RouteGeometry)
def geometry(
    dataset_id: str,
    engineer_id: str,
    deps: Deps,
    plan: Literal["current", "previous"] = "current",
) -> RouteGeometry:
    record = _record(deps, dataset_id)
    with record.lock:
        session = _session(record)
    try:
        return route_geometry(session, engineer_id, plan, deps.osrm, deps.kv)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
