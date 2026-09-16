from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

from app.domain.enums import Skill, Transport
from app.domain.timeutil import HHMM


class RegionConfig(BaseModel):
    title: str
    control: str
    synthetic: str


class TransportRule(BaseModel):
    transport: Transport
    skill: Skill | None = None
    hd_contains: str | None = None

    @model_validator(mode="after")
    def _has_condition(self) -> TransportRule:
        if self.skill is None and self.hd_contains is None:
            raise ValueError("правило транспорта должно иметь skill или hd_contains")
        return self


class ShiftTemplate(BaseModel):
    start: HHMM
    end: HHMM


class UrgentEventConfig(BaseModel):
    duration_min: int
    window_min: int


class SynthConfig(BaseModel):
    seed: int
    event_time: HHMM
    regions: dict[str, RegionConfig]
    skill_by_bk: dict[str, Skill]
    default_duration_min: int
    duration_jitter: float
    duration_round_to: int
    duration_by_hd: dict[str, int]
    urgent_bk_types: list[str]
    urgent_control_statuses: list[str]
    cancelled_control_statuses: list[str]
    equipment_hd_types: list[str]
    transport_required_rules: list[TransportRule]
    transport_mix: dict[Transport, float]
    force_car_for_skills: list[Skill]
    transport_from_history: bool = False
    engineer_start: Literal["office", "history_medoid"] = "office"
    shifts: list[ShiftTemplate]
    urgent_event: UrgentEventConfig

    @classmethod
    def load(cls, path: Path) -> SynthConfig:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
