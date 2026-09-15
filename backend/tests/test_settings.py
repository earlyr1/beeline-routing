import pytest

from app.geo.matrix import TrafficProfile, TravelModel
from app.planning.session import PlanningContext
from app.settings import DEFAULT_SOLVER_TIME_LIMIT_S, Settings
from app.solvers.ortools_solver import OrToolsSolver


def test_settings_from_env_defaults_and_overrides(tmp_path):
    settings = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "OSRM_URL": "http://osrm:5000",
            "LLM_BASE_URL": "x",
            "LLM_MODEL": "m",
            "YANDEX_MAPS_API_KEY": " ",
        }
    )
    assert settings.bundles_dir == tmp_path / "bundles"
    assert settings.cache_path == tmp_path / "cache.sqlite"
    assert settings.geocode_cache_path == tmp_path / "geocode_cache.json"
    assert settings.yandex_maps_api_key is None
    assert settings.llm_enabled and settings.solver_time_limit_s == 5 and settings.geocoder == "nominatim"


def test_solver_time_limit_defaults_to_five_seconds_everywhere():
    """Таблица результатов в README посчитана с лимитом 5 секунд: демо по умолчанию должно её повторять."""
    assert DEFAULT_SOLVER_TIME_LIMIT_S == 5
    assert OrToolsSolver().time_limit_s == 5
    assert PlanningContext(model=TravelModel(), traffic=TrafficProfile({})).time_limit_s == 5
    assert Settings.from_env({"SOLVER_TIME_LIMIT_S": "2"}).solver_time_limit_s == 2


def test_settings_reject_unknown_geocoder():
    with pytest.raises(ValueError):
        Settings.from_env({"GEOCODER": "google"})
