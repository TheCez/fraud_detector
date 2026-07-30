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
