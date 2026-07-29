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
    assert result.cognee_api_key is None
    assert result.cognee_service_url is None
    assert result.openai_api_key is None


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
