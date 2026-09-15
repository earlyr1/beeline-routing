"""Сборка зависимостей приложения из настроек."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from app.api.ingest_service import BundleStore, IngestDeps
from app.api.registry import DatasetRegistry
from app.geo.kvcache import KVCache
from app.geo.matrix import TrafficProfile, TravelModel
from app.geo.osrm import OsrmClient
from app.ingest.geocode import Geocoder, GeoResult, JsonGeocodeCache, NominatimGeocoder, geocode_address
from app.llm.client import LlmClient, OpenAiLlmClient
from app.llm.store import ProposalStore
from app.planning.session import PlanningContext
from app.settings import BACKEND_DIR, Settings
from app.synth.config import SynthConfig


@dataclass
class AppDeps:
    settings: Settings
    registry: DatasetRegistry
    ingest: IngestDeps
    osrm: OsrmClient | None
    kv: KVCache
    llm: LlmClient | None = None
    proposals: ProposalStore = field(default_factory=ProposalStore)


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
    kv = KVCache(settings.cache_path)
    osrm = OsrmClient(settings.osrm_url) if settings.osrm_url else None
    geocoder = geocoder_override or (NominatimGeocoder() if settings.geocoder == "nominatim" else None)
    cache = JsonGeocodeCache(settings.geocode_cache_path)
    lock = threading.Lock()

    def geocode(address: str, district: str) -> GeoResult:
        with lock:
            try:
                return geocode_address(address, district, geocoder, cache)
            finally:
                cache.save()

    planning = PlanningContext(
        model=TravelModel(),
        traffic=TrafficProfile.load(BACKEND_DIR / "config" / "traffic_profile.yaml"),
        osrm=osrm,
        cache=kv,
        time_limit_s=settings.solver_time_limit_s,
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
    )
