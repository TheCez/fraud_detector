from app.core.settings import AgentSettings
from app.core import settings


def test_agent_settings_are_isolated_from_the_developer_env_by_default():
    """Guards against regressing the autouse fixture in tests/conftest.py.

    settings.py loads the developer's real .env at import time, so without
    that fixture this would pick up live credentials and flip tests over to
    the agent code path on any machine with a populated .env.
    """
    result = AgentSettings.from_environment()

    assert result.agent_enabled is False
    assert result.openai_api_key is None
    assert result.is_configured is False


def test_per_stage_models_default_to_openai_model_when_unset(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-shared-default")

    result = AgentSettings.from_environment()

    assert result.analyst_model == "gpt-shared-default"
    assert result.gate_model == "gpt-shared-default"
    assert result.verifier_model == "gpt-shared-default"


def test_per_stage_models_are_independently_overridable(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-shared-default")
    monkeypatch.setenv("FRAUD_AGENT_ANALYST_MODEL", "gpt-analyst")
    monkeypatch.setenv("FRAUD_AGENT_GATE_MODEL", "gpt-gate")
    monkeypatch.setenv("FRAUD_AGENT_VERIFIER_MODEL", "gpt-verifier")

    result = AgentSettings.from_environment()

    assert result.analyst_model == "gpt-analyst"
    assert result.gate_model == "gpt-gate"
    assert result.verifier_model == "gpt-verifier"


def test_per_stage_model_env_var_set_but_blank_still_falls_back_to_openai_model(monkeypatch):
    """`.env.example` ships these three blank - dotenv loads a blank value as
    an empty string, not an unset variable, so the fallback must treat an
    empty string the same as missing."""
    monkeypatch.setenv("OPENAI_MODEL", "gpt-shared-default")
    monkeypatch.setenv("FRAUD_AGENT_ANALYST_MODEL", "")

    result = AgentSettings.from_environment()

    assert result.analyst_model == "gpt-shared-default"


def test_is_configured_requires_only_agent_enabled_and_openai_key(monkeypatch):
    monkeypatch.setenv("FRAUD_AGENT_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    result = AgentSettings.from_environment()

    assert result.is_configured is True


def test_model_call_cap_has_a_default_and_is_overridable(monkeypatch):
    assert AgentSettings.from_environment().model_call_cap > 0

    monkeypatch.setenv("FRAUD_AGENT_MODEL_CALL_CAP", "7")
    assert AgentSettings.from_environment().model_call_cap == 7


def test_load_local_environment_uses_project_env_file_without_overriding_process_env(
    monkeypatch, tmp_path
):
    captured: dict[str, object] = {}

    def fake_load_dotenv(path, *, override):
        captured["path"] = path
        captured["override"] = override
        return True

    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(settings, "load_dotenv", fake_load_dotenv)

    assert settings.load_local_environment() is True
    assert captured == {"path": tmp_path / ".env", "override": False}
