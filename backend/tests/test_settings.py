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
    assert (settings.solver_time_limit_s, settings.solver_time_limit_lunch_s) == (5, 30)


def test_solver_time_limits_default_to_five_and_fifteen_seconds_everywhere():
    """День с обедом считается до 30 секунд, день без обеда и перепланирование по событию до 5 секунд."""
    assert (DEFAULT_SOLVER_TIME_LIMIT_S, DEFAULT_SOLVER_TIME_LIMIT_LUNCH_S) == (5, 30)
    assert OrToolsSolver().time_limit_s == 5
    ctx = PlanningContext(model=TravelModel(), traffic=TrafficProfile({}))
    assert (ctx.time_limit_s, ctx.time_limit_lunch_s) == (5, 30)
    overridden = Settings.from_env({"SOLVER_TIME_LIMIT_S": "2", "SOLVER_TIME_LIMIT_LUNCH_S": "7"})
    assert (overridden.solver_time_limit_s, overridden.solver_time_limit_lunch_s) == (2, 7)


def test_planning_context_gets_both_time_limits_from_settings(tmp_path):
    settings = Settings.from_env(
        {
            "DATA_DIR": str(tmp_path),
            "GEOCODER": "cache-only",
            "SOLVER_TIME_LIMIT_S": "2",
            "SOLVER_TIME_LIMIT_LUNCH_S": "7",
            "SOLVER_WORKERS": "1",
        }
    )
    planning = build_deps(settings).ingest.planning
    assert (planning.time_limit_s, planning.time_limit_lunch_s) == (2, 7)
    assert planning.solver_pool is None


def test_solver_workers_default_to_the_cores_up_to_four_and_one_turns_the_pool_off(monkeypatch, tmp_path):
    monkeypatch.setattr("os.cpu_count", lambda: 10)
    assert Settings.from_env({}).solver_workers == 4
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    assert Settings.from_env({}).solver_workers == 2
    monkeypatch.setattr("os.cpu_count", lambda: None)
    assert Settings.from_env({}).solver_workers == 1
    assert Settings.from_env({"SOLVER_WORKERS": "3"}).solver_workers == 3
    with pytest.raises(ValueError, match="SOLVER_WORKERS"):
        Settings.from_env({"SOLVER_WORKERS": "0"})
    # Настройки, собранные вручную (тесты API), работают без пула.
    assert (
        Settings(
            data_dir=tmp_path,
            cache_path=tmp_path / "c.sqlite",
            transit_dir=tmp_path / "t",
            osrm_url=None,
            yandex_maps_api_key=None,
            llm_base_url=None,
            llm_api_key=None,
            llm_models=(),
            geocoder="cache-only",
            solver_time_limit_s=1,
        ).solver_workers
        == 1
    )

    pooled = build_deps(
        Settings.from_env({"DATA_DIR": str(tmp_path), "GEOCODER": "cache-only", "SOLVER_WORKERS": "2"})
    )
    pool = pooled.ingest.planning.solver_pool
    assert pool is not None and pool.workers == 2
    pool.shutdown()


def test_settings_reject_unknown_geocoder():
    with pytest.raises(ValueError):
        Settings.from_env({"GEOCODER": "google"})
