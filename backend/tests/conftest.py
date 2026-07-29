import pytest

_AGENT_ENV_VARS = (
    "FRAUD_AGENT_ENABLED",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "FRAUD_AGENT_MODEL_CALL_CAP",
)


@pytest.fixture(autouse=True)
def isolated_agent_environment(monkeypatch):
    """Keep the suite deterministic regardless of a developer's local .env.

    settings.py loads .env at import time (override=False), so whatever is on
    disk ends up in os.environ before pytest ever runs. AgentSettings reads
    os.environ lazily at call time, so clearing these here still controls
    behaviour and lets tests opt back in explicitly with monkeypatch.setenv.
    """
    for var in _AGENT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
