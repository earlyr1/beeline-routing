"""Модели ответов API (docs/superpowers/specs/2026-09-15-api-contract.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.enums import Transport
from app.domain.models import Engineer, Office, Plan, Request
from app.domain.timeutil import HHMM
from app.planning.models import AppliedEvent, PlanDiff
from app.planning.session import PlanningSession

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


class PlanningState(BaseModel):
    dataset_id: str
    version: int
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
    events: list[AppliedEvent] = Field(default_factory=list)
    matrix_source: Literal["osrm", "haversine"]


class RouteLeg(BaseModel):
    to_request_id: str
    coordinates: list[list[float]]


class RouteGeometry(BaseModel):
    engineer_id: str
    transport: Transport
    source: Literal["osrm", "straight"]
    legs: list[RouteLeg]


class ClientConfig(BaseModel):
    yandex_maps_api_key: str | None
    llm_enabled: bool
    osrm_available: bool


def to_planning_state(session: PlanningSession) -> PlanningState:
    return PlanningState(
        dataset_id=session.dataset_id,
        version=session.version,
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
        events=session.events,
        matrix_source=session.problem.travel.base.source,
    )
