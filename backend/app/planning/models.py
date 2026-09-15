"""Объекты перепланирования и объяснений (см. docs/superpowers/specs/2026-09-15-api-contract.md)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.models import Event, Metrics, Unassigned, Visit
from app.domain.timeutil import HHMM


class DiffMove(BaseModel):
    request_id: str
    from_engineer_id: str
    to_engineer_id: str


class DiffAssign(BaseModel):
    request_id: str
    engineer_id: str


class DiffRemove(BaseModel):
    request_id: str
    engineer_id: str
    reason: str


class DiffShift(BaseModel):
    request_id: str
    engineer_id: str
    old_start: HHMM
    new_start: HHMM
    delta_min: int


class PlanDiff(BaseModel):
    moved: list[DiffMove] = Field(default_factory=list)
    added: list[DiffAssign] = Field(default_factory=list)
    removed: list[DiffRemove] = Field(default_factory=list)
    reordered_engineers: list[str] = Field(default_factory=list)
    time_shifts: list[DiffShift] = Field(default_factory=list)
    metrics_before: Metrics
    metrics_after: Metrics


class ConstraintCheck(BaseModel):
    name: str
    ok: bool
    detail: str


class Alternative(BaseModel):
    engineer_id: str
    feasible: bool
    extra_km: float | None = None
    start: HHMM | None = None
    reason: str


class Explanation(BaseModel):
    request_id: str
    status: Literal["assigned", "unassigned", "cancelled"]
    engineer_id: str | None = None
    summary: str
    factors: list[str] = Field(default_factory=list)
    constraints: list[ConstraintCheck] = Field(default_factory=list)
    visit: Visit | None = None
    alternatives: list[Alternative] = Field(default_factory=list)
    unassigned: Unassigned | None = None


class AppliedEvent(BaseModel):
    id: str
    event: Event
    version: int
