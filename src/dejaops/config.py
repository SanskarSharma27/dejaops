"""Environment-driven configuration. No secrets are ever hardcoded; in AWS they
come from SSM Parameter Store via Lambda environment configuration.

Provider selection
------------------
Inference and embeddings each have a provider switch, so the same code runs
against Amazon Bedrock (the default) or the vendors' own APIs:

    LLM_PROVIDER   = bedrock | anthropic     (default: bedrock)
    EMBED_PROVIDER = bedrock | voyage        (default: bedrock)

Bedrock is the primary, production path. The direct-API path exists because a
brand-new AWS account's Bedrock allowlisting can take days to clear — it keeps
the agent runnable without weakening the Bedrock integration.

FAKE_LLM / FAKE_EMBEDDINGS override both with offline stand-ins for local dev
and CI, so the full stack is testable with no vendor credentials at all.
"""

import os
from dataclasses import dataclass, field

# Same model, two delivery routes: Bedrock needs the cross-region inference
# profile ID, the first-party API takes the plain alias.
BEDROCK_LLM_DEFAULT = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
ANTHROPIC_LLM_DEFAULT = "claude-haiku-4-5"

BEDROCK_EMBED_DEFAULT = "amazon.titan-embed-text-v2:0"
VOYAGE_EMBED_DEFAULT = "voyage-3.5"  # natively supports 1024-dim output


def _bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "ap-south-1"))

    llm_provider: str = field(
        default_factory=lambda: os.environ.get("LLM_PROVIDER", "bedrock").lower()
    )
    embed_provider: str = field(
        default_factory=lambda: os.environ.get("EMBED_PROVIDER", "bedrock").lower()
    )

    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    voyage_api_key: str = field(default_factory=lambda: os.environ.get("VOYAGE_API_KEY", ""))

    # Empty means "use the provider's default" (see the resolved_* properties).
    llm_model_id: str = field(default_factory=lambda: os.environ.get("LLM_MODEL_ID", ""))
    embed_model_id: str = field(default_factory=lambda: os.environ.get("EMBED_MODEL_ID", ""))
    embed_dim: int = field(default_factory=lambda: int(os.environ.get("EMBED_DIM", "1024")))

    fake_embeddings: bool = field(default_factory=lambda: _bool("FAKE_EMBEDDINGS"))
    fake_llm: bool = field(default_factory=lambda: _bool("FAKE_LLM"))

    # Demo hardening: token gate + per-IP rate limit for the public URL.
    demo_token: str = field(default_factory=lambda: os.environ.get("DEMO_TOKEN", ""))
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30"))
    )

    max_agent_iterations: int = field(
        default_factory=lambda: int(os.environ.get("MAX_AGENT_ITERATIONS", "8"))
    )

    @property
    def resolved_llm_model(self) -> str:
        if self.llm_model_id:
            return self.llm_model_id
        return ANTHROPIC_LLM_DEFAULT if self.llm_provider == "anthropic" else BEDROCK_LLM_DEFAULT

    @property
    def resolved_embed_model(self) -> str:
        if self.embed_model_id:
            return self.embed_model_id
        return VOYAGE_EMBED_DEFAULT if self.embed_provider == "voyage" else BEDROCK_EMBED_DEFAULT


def settings() -> Settings:
    """Re-read env each call: cheap, and lets tests monkeypatch os.environ."""
    return Settings()
