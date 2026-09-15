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
