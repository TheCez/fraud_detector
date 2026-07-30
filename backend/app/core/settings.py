"""Runtime configuration for optional agent-mode analysis."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Sane default for the hard cap on model calls per analysis run - see
# pipeline.py, which analyses one call per ledger entry and can face several
# thousand entries in a large dossier. This bounds the worst case without
# needing to be tuned per dossier; deployments with a real cost budget in
# mind can override it.
_DEFAULT_MODEL_CALL_CAP = 500

# Default worker count for the concurrent per-entry analysis pool in
# pipeline.py. The work is I/O-bound on HTTPS calls to the model provider, not
# CPU-bound, so a worker count well above the machine's core count is still
# profitable - 12 keeps run time down without so much concurrency that a
# shared-quota deployment trips the provider's rate limit on the first batch
# of requests.
_DEFAULT_MAX_WORKERS = 12


def load_local_environment() -> bool:
    """Load local development configuration without overriding deployment settings."""
    return load_dotenv(PROJECT_ROOT / ".env", override=False)


load_local_environment()


@dataclass(frozen=True)
class AgentSettings:
    """Server-only configuration. Secrets are never returned by this module.

    Per-stage model tiers replace the single ``openai_model`` this dataclass
    used to carry - the pipeline's three stages (analyst, gate, verifier) are
    expected to run different model tiers: the project owner wants a cheaper
    model for the gate and a *different* one (not necessarily weaker) for
    verification, since a model checking its own reasoning tends to accept
    its own plausible mistakes. Each field defaults to ``OPENAI_MODEL`` when
    its own environment variable is unset, so a deployment that only ever set
    ``OPENAI_MODEL`` keeps working untouched.
    """

    openai_api_key: str | None
    analyst_model: str
    gate_model: str
    verifier_model: str
    agent_enabled: bool
    model_call_cap: int
    max_workers: int

    @property
    def is_configured(self) -> bool:
        return bool(self.agent_enabled and self.openai_api_key)

    @classmethod
    def from_environment(cls) -> "AgentSettings":
        enabled = os.getenv("FRAUD_AGENT_ENABLED", "false").strip().lower()
        default_model = os.getenv("OPENAI_MODEL", "gpt-5.4")
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            analyst_model=os.getenv("FRAUD_AGENT_ANALYST_MODEL") or default_model,
            gate_model=os.getenv("FRAUD_AGENT_GATE_MODEL") or default_model,
            verifier_model=os.getenv("FRAUD_AGENT_VERIFIER_MODEL") or default_model,
            agent_enabled=enabled in {"1", "true", "yes"},
            model_call_cap=int(os.getenv("FRAUD_AGENT_MODEL_CALL_CAP", str(_DEFAULT_MODEL_CALL_CAP))),
            max_workers=int(os.getenv("FRAUD_AGENT_MAX_WORKERS", str(_DEFAULT_MAX_WORKERS))),
        )
