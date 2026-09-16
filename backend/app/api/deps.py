"""Сборка зависимостей приложения из настроек."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from app.api.ingest_service import BundleStore, IngestDeps
from app.api.registry import DatasetRegistry
from app.geo.kvcache import KVCache
from app.geo.matrix import TrafficProfile, TravelModel
from app.geo.osrm import OsrmClient
from app.geo.transit import load_transit_matrix
from app.ingest.geocode import (
    NO_ADDRESS,
    Geocoder,
    GeoResult,
    JsonGeocodeCache,
    NominatimGeocoder,
    ReverseAddress,
    ReverseGeocodeCache,
    ReverseGeocoder,
    geocode_address,
    reverse_geocode,
)
from app.llm.client import LlmClient, OpenAiLlmClient
from app.llm.store import ProposalStore
from app.planning.session import PlanningContext
from app.settings import BACKEND_DIR, Settings
from app.synth.config import SynthConfig


def _without_reverse(lat: float, lon: float) -> ReverseAddress:
    return NO_ADDRESS


def run_in_thread(task: Callable[[], None]) -> None:
    """Фоновая задача в потоке-демоне: процесс не ждёт её при остановке."""
    threading.Thread(target=task, daemon=True).start()


@dataclass
class AppDeps:
    settings: Settings
    registry: DatasetRegistry
    ingest: IngestDeps
    osrm: OsrmClient | None
    kv: KVCache
    llm: LlmClient | None = None
    proposals: ProposalStore = field(default_factory=ProposalStore)
    # Адрес точки на карте: (lat, lon) -> короткий адрес и точность.
    reverse_geocode: Callable[[float, float], ReverseAddress] = _without_reverse
    # Запуск фонового предподсчёта таймлайна; тесты подменяют его, чтобы выполнять задачи сами.
    run_background: Callable[[Callable[[], None]], None] = run_in_thread


def build_llm(settings: Settings) -> LlmClient | None:
    """Клиент OpenAI-совместимого API или None, если LLM_BASE_URL и LLM_MODEL не заданы."""
    if not settings.llm_enabled:
        return None
    return OpenAiLlmClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        mode=settings.llm_tool_mode,
    )


def build_deps(settings: Settings, geocoder_override: Geocoder | None = None) -> AppDeps:
    """Геокодер из geocoder_override ищет и по точке, если у него есть метод reverse."""
    kv = KVCache(settings.cache_path)
    osrm = OsrmClient(settings.osrm_url) if settings.osrm_url else None
    geocoder = geocoder_override or (NominatimGeocoder() if settings.geocoder == "nominatim" else None)
    reverse_geocoder = geocoder if isinstance(geocoder, ReverseGeocoder) else None
    cache = JsonGeocodeCache(settings.geocode_cache_path)
    reverse_cache = ReverseGeocodeCache()
    # Одна блокировка на поиск по адресу и по точке: к Nominatim идёт не больше одного запроса за раз.
    lock = threading.Lock()

    def geocode(address: str, district: str) -> GeoResult:
        with lock:
            try:
                return geocode_address(address, district, geocoder, cache)
            finally:
                cache.save()

    def reverse(lat: float, lon: float) -> ReverseAddress:
        with lock:
            return reverse_geocode(lat, lon, reverse_geocoder, reverse_cache)

    planning = PlanningContext(
        model=TravelModel(),
        traffic=TrafficProfile.load(BACKEND_DIR / "config" / "traffic_profile.yaml"),
        osrm=osrm,
        cache=kv,
        # Матрица 2ГИС читается один раз при старте. Файла нет или он не читается — сервис работает как без него.
        transit=load_transit_matrix(settings.transit_matrix_path),
        time_limit_s=settings.solver_time_limit_s,
        time_limit_lunch_s=settings.solver_time_limit_lunch_s,
        geocode=geocode,
    )
    ingest = IngestDeps(
        bundles=BundleStore(settings.bundles_dir),
        synth_config=SynthConfig.load(BACKEND_DIR / "config" / "synth_config.yaml"),
        geocode=geocode,
        planning=planning,
    )
    return AppDeps(
        settings=settings,
        registry=DatasetRegistry(),
        ingest=ingest,
        osrm=osrm,
        kv=kv,
        llm=build_llm(settings),
        reverse_geocode=reverse,
    )
