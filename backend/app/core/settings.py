"""Runtime configuration for optional cloud analysis services."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_local_environment() -> bool:
    """Load local development configuration without overriding deployment settings."""
    return load_dotenv(PROJECT_ROOT / ".env", override=False)


load_local_environment()


@dataclass(frozen=True)
class AgentSettings:
    """Server-only configuration. Secrets are never returned by this module."""

    cognee_api_key: str | None
    cognee_service_url: str | None
    openai_api_key: str | None
    openai_model: str
    agent_enabled: bool

    @property
    def is_configured(self) -> bool:
        return bool(
            self.agent_enabled
            and self.cognee_api_key
            and self.cognee_service_url
            and self.openai_api_key
        )

    @classmethod
    def from_environment(cls) -> "AgentSettings":
        enabled = os.getenv("FRAUD_AGENT_ENABLED", "false").strip().lower()
        return cls(
            cognee_api_key=os.getenv("COGNEE_API_KEY"),
            cognee_service_url=os.getenv("COGNEE_SERVICE_URL"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
            agent_enabled=enabled in {"1", "true", "yes"},
        )
