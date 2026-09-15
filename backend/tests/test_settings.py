import pytest

from app.settings import Settings


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
    assert settings.llm_enabled and settings.solver_time_limit_s == 3 and settings.geocoder == "nominatim"


def test_settings_reject_unknown_geocoder():
    with pytest.raises(ValueError):
        Settings.from_env({"GEOCODER": "google"})
