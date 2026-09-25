import pytest

from app.api.deps import build_llm
from app.llm.client import OpenAiLlmClient
from app.settings import Settings


def test_llm_tool_mode_default_and_validation():
    assert Settings.from_env({}).llm_tool_mode == "auto"
    assert Settings.from_env({"LLM_TOOL_MODE": "json"}).llm_tool_mode == "json"
    with pytest.raises(ValueError, match="LLM_TOOL_MODE"):
        Settings.from_env({"LLM_TOOL_MODE": "xml"})


def test_build_llm_only_when_base_url_and_model_are_set(tmp_path):
    assert (
        build_llm(Settings.from_env({"DATA_DIR": str(tmp_path), "LLM_BASE_URL": "http://llm.test/v1"}))
        is None
    )
    client = build_llm(
        Settings.from_env({"DATA_DIR": str(tmp_path), "LLM_BASE_URL": "http://llm.test/v1", "LLM_MODEL": "m"})
    )
    assert isinstance(client, OpenAiLlmClient)


def test_llm_model_is_a_comma_separated_pool():
    assert Settings.from_env({}).llm_models == ()
    assert Settings.from_env({"LLM_MODEL": "m"}).llm_models == ("m",)
    pool = "gpt://folder/yandexgpt/rc , gpt://folder/qwen3-235b-a22b-fp8/latest"
    assert Settings.from_env({"LLM_MODEL": pool}).llm_models == (
        "gpt://folder/yandexgpt/rc",
        "gpt://folder/qwen3-235b-a22b-fp8/latest",
    )
    for broken in ("a,,b", "a,", ",a", "a, ,b"):
        with pytest.raises(ValueError, match="LLM_MODEL: пустое имя модели"):
            Settings.from_env({"LLM_MODEL": broken})


def test_build_llm_passes_the_whole_pool(tmp_path):
    settings = Settings.from_env(
        {"DATA_DIR": str(tmp_path), "LLM_BASE_URL": "http://llm.test/v1", "LLM_MODEL": "первая, вторая"}
    )
    client = build_llm(settings)
    assert isinstance(client, OpenAiLlmClient) and client.models == ("первая", "вторая")
