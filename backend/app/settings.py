"""Настройки backend из переменных окружения."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
GEOCODERS = ("nominatim", "cache-only")
LLM_TOOL_MODES = ("auto", "tools", "json")
# Лимит OR-Tools на день без обеда и на перепланирование по событию: оно стартует от текущего плана.
DEFAULT_SOLVER_TIME_LIMIT_S = 5
# Лимит OR-Tools на весь день с обедом: перерывы в модели замедляют поиск. С этим лимитом посчитаны бандлы и
# таблица результатов в README.
DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S = 15


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cache_path: Path
    osrm_url: str | None
    yandex_maps_api_key: str | None
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str | None
    geocoder: str
    solver_time_limit_s: int
    solver_time_limit_lunch_s: int = DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S
    llm_tool_mode: str = "auto"

    @property
    def bundles_dir(self) -> Path:
        return self.data_dir / "bundles"

    @property
    def geocode_cache_path(self) -> Path:
        return self.data_dir / "geocode_cache.json"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        def optional(name: str) -> str | None:
            value = (env.get(name) or "").strip()
            return value or None

        data_dir = Path(optional("DATA_DIR") or REPO_ROOT / "data")
        geocoder = optional("GEOCODER") or "nominatim"
        if geocoder not in GEOCODERS:
            raise ValueError(f"GEOCODER должен быть одним из: {', '.join(GEOCODERS)}")
        llm_tool_mode = optional("LLM_TOOL_MODE") or "auto"
        if llm_tool_mode not in LLM_TOOL_MODES:
            raise ValueError(f"LLM_TOOL_MODE должен быть одним из: {', '.join(LLM_TOOL_MODES)}")
        return cls(
            data_dir=data_dir,
            cache_path=Path(optional("CACHE_PATH") or data_dir / "cache.sqlite"),
            osrm_url=optional("OSRM_URL"),
            yandex_maps_api_key=optional("YANDEX_MAPS_API_KEY"),
            llm_base_url=optional("LLM_BASE_URL"),
            llm_api_key=optional("LLM_API_KEY"),
            llm_model=optional("LLM_MODEL"),
            geocoder=geocoder,
            solver_time_limit_s=int(optional("SOLVER_TIME_LIMIT_S") or DEFAULT_SOLVER_TIME_LIMIT_S),
            solver_time_limit_lunch_s=int(
                optional("SOLVER_TIME_LIMIT_LUNCH_S") or DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S
            ),
            llm_tool_mode=llm_tool_mode,
        )
