from app.core import settings


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
