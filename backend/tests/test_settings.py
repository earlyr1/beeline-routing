import pytest

from app.api.deps import build_deps
from app.geo.matrix import TrafficProfile, TravelModel
from app.planning.session import PlanningContext
from app.settings import DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S, DEFAULT_SOLVER_TIME_LIMIT_S, Settings
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
    assert settings.llm_enabled and settings.geocoder == "nominatim"
    assert (settings.solver_time_limit_s, settings.solver_time_limit_lunch_s) == (5, 15)


def test_solver_time_limits_default_to_five_and_fifteen_seconds_everywhere():
    """День с обедом считается до 15 секунд, день без обеда и перепланирование по событию до 5 секунд."""
    assert (DEFAULT_SOLVER_TIME_LIMIT_S, DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S) == (5, 15)
    assert OrToolsSolver().time_limit_s == 5
    ctx = PlanningContext(model=TravelModel(), traffic=TrafficProfile({}))
    assert (ctx.time_limit_s, ctx.time_limit_lunch_s) == (5, 15)
    overridden = Settings.from_env({"SOLVER_TIME_LIMIT_S": "2", "SOLVER_TIME_LIMIT_LUNCH_S": "7"})
    assert (overridden.solver_time_limit_s, overridden.solver_time_limit_lunch_s) == (2, 7)


def test_planning_context_gets_both_time_limits_from_settings(tmp_path):
    settings = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "GEOCODER": "cache-only",
            "SOLVER_TIME_LIMIT_S": "2",
            "SOLVER_TIME_LIMIT_LUNCH_S": "7",
        }
    )
    planning = build_deps(settings).ingest.planning
    assert (planning.time_limit_s, planning.time_limit_lunch_s) == (2, 7)


def test_settings_reject_unknown_geocoder():
    with pytest.raises(ValueError):
        Settings.from_env({"GEOCODER": "google"})
