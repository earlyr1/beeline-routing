from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from app.domain.enums import RequestTier, Skill, Transport
from app.domain.timeutil import HHMM
from app.domain.windows import TimeSlot, build_slots

# Доли жеребьёвки транспорта инженеров (app/synth/engineers.py) по прежним четырём типам и в прежнем порядке.
# «foot» — бывший тип «Пешеход»: теперь он часть общественного транспорта, но в жеребьёвке остаётся отдельной долей,
# иначе сдвинулись бы округление долей и перемешивание, и синтетические инженеры получили бы другой транспорт.
TransportDraw = Literal["car", "foot", "bike", "public"]
TRANSPORT_DRAW_ORDER: tuple[TransportDraw, ...] = ("car", "foot", "bike", "public")


class RegionConfig(BaseModel):
    title: str
    control: str
    synthetic: str
    # Выгрузки Билайна по региону нет, пара CSV сгенерирована нами (docs/assumptions.md). На досинтез это не влияет:
    # флаг нужен, чтобы экран загрузки честно подписывал кнопку такого региона.
    generated: bool = False


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
    # Длина окна визита: и слот сетки окон (SynthConfig.window_grid), и окно демо-события бандла. Одно число
    # на весь сервис, чтобы длина окна нигде не разошлась сама с собой.
    window_min: int


class SynthConfig(BaseModel):
    seed: int
    event_time: HHMM
    regions: dict[str, RegionConfig]
    skill_by_bk: dict[str, Skill]
    tier_by_bk: dict[str, RequestTier]
    default_duration_min: int
    duration_jitter: float
    duration_round_to: int
    duration_by_bk: dict[str, int]
    urgent_bk_types: list[str]
    cancelled_control_statuses: list[str]
    equipment_stock: int = Field(ge=0)
    equipment_hd_types: list[str]
    transport_required_rules: list[TransportRule]
    transport_mix: dict[TransportDraw, float]
    force_car_for_skills: list[Skill]
    transport_from_history: bool = False
    engineer_start: Literal["office", "history_medoid"] = "office"
    # Районы Подмосковья, где бригада может жить (значения колонки «Район»). При engineer_start: office бригада,
    # у которой больше половины истории в этих районах, начинает день дома, а не в офисе региона.
    home_districts: list[str] = []
    shifts: list[ShiftTemplate]
    urgent_event: UrgentEventConfig

    @property
    def window_grid(self) -> list[TimeSlot]:
        """Сетка окон визита (app/domain/windows.py): рабочий день смен по слоту длиной окна.

        Одно определение сетки на весь сервис: день берётся из shifts, а длина слота — из urgent_event.window_min,
        поэтому сетка не может разойтись ни со сменой, ни с длиной окна. Отсюда её отдаёт GET /api/config, по ней
        сервер проверяет окно диспетчера и по ней помощник кладёт на слот окно, которое назвал.
        """
        if not self.shifts:
            return []
        return build_slots(
            min(shift.start for shift in self.shifts),
            max(shift.end for shift in self.shifts),
            self.urgent_event.window_min,
        )

    @classmethod
    def load(cls, path: Path) -> SynthConfig:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
