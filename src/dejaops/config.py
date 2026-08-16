"""Environment-driven configuration. No secrets are ever hardcoded; in AWS they
come from SSM Parameter Store via Lambda environment configuration."""

import os
from dataclasses import dataclass, field


def _bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "us-east-1"))

    # Claude via the Bedrock Messages endpoint (Anthropic SDK Mantle client).
    llm_model_id: str = field(
        default_factory=lambda: os.environ.get("LLM_MODEL_ID", "anthropic.claude-haiku-4-5")
    )
    embed_model_id: str = field(
        default_factory=lambda: os.environ.get("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
    )
    embed_dim: int = field(default_factory=lambda: int(os.environ.get("EMBED_DIM", "1024")))

    # Offline modes: deterministic embeddings / canned LLM output, so the whole
    # stack (and the test suite) runs without AWS credentials.
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


def settings() -> Settings:
    """Re-read env each call: cheap, and lets tests monkeypatch os.environ."""
    return Settings()
