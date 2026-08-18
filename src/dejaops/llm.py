"""Claude, reached either through Amazon Bedrock or the first-party API.

Both routes use the same Anthropic SDK and the identical Messages + tool-use
surface, so the agent loop is provider-agnostic — only the client class and the
model ID differ:

  LLM_PROVIDER=bedrock   (default)  AnthropicBedrock, inference-profile model ID
  LLM_PROVIDER=anthropic            Anthropic, plain model alias

On Bedrock in ap-south-1, Claude Haiku 4.5 is served through *global
cross-region inference*, so the model ID must be the inference-profile form
`global.anthropic.claude-haiku-4-5-20251001-v1:0` — the bare model ID rejects
on-demand invocation.

FAKE_LLM=1 returns canned responses so the API and tests run with no vendor
credentials at all.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from .config import settings

log = logging.getLogger("dejaops.llm")


@dataclass
class FakeBlock:
    type: str
    text: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    id: str = "fake_tool_use_1"


@dataclass
class FakeMessage:
    content: list[FakeBlock]
    stop_reason: str = "end_turn"


_client = None
_client_provider: str | None = None


def _llm_client():
    """Build (and memoize) the client for the configured provider."""
    global _client, _client_provider
    cfg = settings()
    if _client is None or _client_provider != cfg.llm_provider:
        if cfg.llm_provider == "anthropic":
            from anthropic import Anthropic

            if not cfg.anthropic_api_key:
                raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
            _client = Anthropic(api_key=cfg.anthropic_api_key)
        else:
            from anthropic import AnthropicBedrock

            _client = AnthropicBedrock(aws_region=cfg.aws_region)
        _client_provider = cfg.llm_provider
    return _client


def create_message(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 2048,
):
    """One Messages API call. Returns the SDK Message (or a FakeMessage)."""
    cfg = settings()
    if cfg.fake_llm:
        return FakeMessage(
            content=[
                FakeBlock(
                    type="text",
                    text=(
                        "FAKE_LLM mode: I recalled similar past incidents from CockroachDB "
                        "memory and would now walk the runbook. Set FAKE_LLM=0 with AWS "
                        "credentials for real inference."
                    ),
                )
            ]
        )

    kwargs: dict[str, Any] = {
        "model": cfg.resolved_llm_model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    resp = _llm_client().messages.create(**kwargs)
    log.info(
        "llm call provider=%s model=%s stop=%s in=%s out=%s",
        cfg.llm_provider,
        cfg.resolved_llm_model,
        resp.stop_reason,
        resp.usage.input_tokens,
        resp.usage.output_tokens,
    )
    return resp
