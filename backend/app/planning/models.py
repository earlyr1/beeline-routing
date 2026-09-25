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


class LateVisit(BaseModel):
    request_id: str
    planned_start: HHMM
    forecast_start: HHMM
    late_min: int


class DelayForecast(BaseModel):
    """Что будет с оставшимися визитами задержанного инженера, если план не пересчитывать."""

    engineer_id: str
    delay_min: int
    late_without_replan: list[LateVisit] = Field(default_factory=list)
    overtime_without_replan_min: int = 0


class PlanDiff(BaseModel):
    moved: list[DiffMove] = Field(default_factory=list)
    added: list[DiffAssign] = Field(default_factory=list)
    removed: list[DiffRemove] = Field(default_factory=list)
    reordered_engineers: list[str] = Field(default_factory=list)
    time_shifts: list[DiffShift] = Field(default_factory=list)
    metrics_before: Metrics
    metrics_after: Metrics
    # Только у события «Задержка инженера», у остальных событий null.
    delay_forecast: DelayForecast | None = None


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


class PrecomputedPlan(BaseModel):
    """Утренний план взят из ночного расчёта (app/planning/night.py), а не найден при загрузке дня.

    События дня пересчитываются от него на месте, как от любого утреннего плана.
    """

    search_minutes: float  # сколько шёл ночной поиск
    computed_at: str  # когда ночной расчёт закончился, ISO 8601 с часовым поясом машины, где он шёл


BaseVariant = Literal["optimal", "stable", "keep"]
# Стратегия события: одна из трёх базовых или «assign:<инженер>» — отдать заявку выбранной бригаде (у события
# об одной заявке, которая после него остаётся в плане, см. app/planning/variants.py). Строка проверяется на границе API.
EventVariant = str


class VariantOption(BaseModel):
    """Один вариант исправления плана на событие: итоги и отличия от варианта, с которым он сравнивается."""

    variant: EventVariant
    title: str
    summary: str
    metrics: Metrics
    late: int  # визиты, которые начнутся позже конца окна
    moved: int  # заявки, переехавшие к другой бригаде относительно плана до события
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    recommended: bool = False
    # Бригада, у которой заявка события в плане этого варианта; null — заявка без инженера или событие не об
    # одной заявке (app/planning/facts.subject_request_id).
    request_engineer_id: str | None = None
    # Вариант, с которым посчитаны pros/cons; null — сравнивать не с чем. У рекомендованного это ближайший
    # вариант с другими числами, а null у него значит, что числа у всех вариантов одни и те же. Пустые pros и
    # cons при заполненном compared_to значат «то же самое, что названный вариант». У «отдать бригаде» всегда
    # «optimal»: диспетчер видит цену своего решения относительно оптимума дня.
    compared_to: EventVariant | None = None


class EventChoice(BaseModel):
    """Выбор варианта для события шкалы: варианты от одного плана до события.

    Три базовых варианта считаются заранее у любого события; у события об одной заявке к ним добавляется
    четвёртый, «отдать заявку названной бригаде», — его считают, только когда диспетчер назвал бригаду.
    current — стратегия, с которой событие применено: выбранная диспетчером или «keep», если выбирать было не из
    чего (правило окна выбора, app/planning/variants.needs_choice); null — событие ждёт выбора.
    """

    entry_id: str
    event: Event
    metrics_before: Metrics
    late_before: int
    variants: list[VariantOption]
    current: EventVariant | None = None
    # Событие об одной заявке, которая после него остаётся в плане: диспетчер может отдать её конкретной бригаде
    # (вариант «assign:<инженер>»).
    assignable: bool = False
