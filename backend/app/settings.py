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
# таблица режима prepare в README («Другие режимы»); утренний план сервис берёт из ночного поиска.
DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S = 30
# Сколько поисков OR-Tools идут одновременно в отдельных процессах: по умолчанию до 4, но не больше ядер.
MAX_DEFAULT_SOLVER_WORKERS = 4


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cache_path: Path
    # Матрицы времени на общественном транспорте от 2ГИС: по файлу <регион>.json в каталоге. Каталог лежит
    # в репозитории (data/transit) и копируется в образ backend, читается при старте.
    transit_dir: Path
    osrm_url: str | None
    yandex_maps_api_key: str | None
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str | None
    geocoder: str
    solver_time_limit_s: int
    solver_time_limit_lunch_s: int = DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S
    llm_tool_mode: str = "auto"
    # 1 — поиск в текущем процессе без пула (так в тестах); больше — пул процессов и несколько стратегий.
    solver_workers: int = 1
    # Postgres, в котором живёт день диспетчера (postgresql://…). Пусто — день живёт в памяти процесса и
    # уходит с перезапуском: это сегодняшний сервис, так работают тесты и запуск без compose.
    database_url: str | None = None

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
        solver_workers = int(
            optional("SOLVER_WORKERS") or min(MAX_DEFAULT_SOLVER_WORKERS, os.cpu_count() or 1)
        )
        if solver_workers < 1:
            raise ValueError("SOLVER_WORKERS должен быть не меньше 1")
        llm_tool_mode = optional("LLM_TOOL_MODE") or "auto"
        if llm_tool_mode not in LLM_TOOL_MODES:
            raise ValueError(f"LLM_TOOL_MODE должен быть одним из: {', '.join(LLM_TOOL_MODES)}")
        return cls(
            data_dir=data_dir,
            cache_path=Path(optional("CACHE_PATH") or data_dir / "cache.sqlite"),
            transit_dir=Path(optional("TRANSIT_MATRIX_DIR") or data_dir / "transit"),
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
            solver_workers=solver_workers,
            database_url=optional("DATABASE_URL"),
        )
