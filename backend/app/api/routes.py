from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi import Request as HttpRequest

from app.api.deps import AppDeps
from app.api.geometry import route_geometry
from app.api.ingest_service import preprocess_upload
from app.api.registry import DatasetRecord
from app.api.schemas import (
    ClientConfig,
    DatasetStatus,
    PlanningState,
    PlanRequest,
    RouteGeometry,
    to_planning_state,
)
from app.domain.models import Event
from app.planning.explain import build_explanation
from app.planning.models import Explanation
from app.planning.session import (
    EventRejected,
    PlanningSession,
    apply_event,
    geocode_before_lock,
    start_session,
)

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


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/config", response_model=ClientConfig)
def client_config(deps: Deps) -> ClientConfig:
    return ClientConfig(
        yandex_maps_api_key=deps.settings.yandex_maps_api_key,
        llm_enabled=deps.llm is not None,
        osrm_available=deps.osrm.health() if deps.osrm is not None else False,
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


@router.get("/datasets/{dataset_id}", response_model=DatasetStatus)
def dataset_status(dataset_id: str, deps: Deps) -> DatasetStatus:
    return _record(deps, dataset_id).status_model()


@router.post("/datasets/{dataset_id}/plan", response_model=PlanningState)
def build_plan(dataset_id: str, deps: Deps, body: PlanRequest | None = None) -> PlanningState:
    """План дня. После событий или со сменой уровня нагрузки день пересчитывается с нуля.

    Без тела или без поля workload_level остаётся уровень сессии. Если событий не было и уровень тот же,
    возвращается предподсчитанный план без изменений.
    """
    record = _record(deps, dataset_id)
    with record.lock:
        session = _session(record)
        level = session.workload_level if body is None or body.workload_level is None else body.workload_level
        if session.events or level != session.workload_level:
            day = record.prepared
            fresh = start_session(
                dataset_id,
                day.region,
                day.office,
                day.requests,
                day.engineers,
                day.control,
                deps.ingest.planning,
                workload_level=level,
            )
            session = replace(fresh, version=session.version + 1)
            record.session = session
        return to_planning_state(session)


@router.get("/datasets/{dataset_id}/state", response_model=PlanningState)
def get_state(dataset_id: str, deps: Deps) -> PlanningState:
    record = _record(deps, dataset_id)
    with record.lock:
        return to_planning_state(_session(record))


@router.post("/datasets/{dataset_id}/events", response_model=PlanningState)
def post_event(dataset_id: str, event: Event, deps: Deps) -> PlanningState:
    record = _record(deps, dataset_id)
    with record.lock:
        snapshot = _session(record)
    # Геокодер может отвечать долго: адрес срочной заявки и новый адрес изменённой заявки ищем до блокировки
    # датасета, чтобы не держать остальные запросы к нему. Повторно под блокировкой не геокодируем, даже если
    # адрес не нашёлся.
    event, ctx = geocode_before_lock(event, snapshot, deps.ingest.planning)
    with record.lock:
        session = _session(record)
        try:
            updated = apply_event(session, event, ctx)
        except EventRejected as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        record.session = updated
        return to_planning_state(updated)


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
