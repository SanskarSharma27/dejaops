"""Claude via Amazon Bedrock, using the Anthropic SDK's Bedrock Messages client
(AnthropicBedrockMantle). Bedrock model IDs carry the `anthropic.` prefix, e.g.
`anthropic.claude-haiku-4-5`. AWS credentials/billing are unchanged — this is
the Bedrock endpoint, reached with a first-class Messages API client instead of
hand-rolled InvokeModel payloads.

FAKE_LLM=1 returns canned responses so the API and tests run without AWS.
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


def _bedrock_client():
    global _client
    if _client is None:
        from anthropic import AnthropicBedrockMantle

        _client = AnthropicBedrockMantle(aws_region=settings().aws_region)
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
        "model": cfg.llm_model_id,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    resp = _bedrock_client().messages.create(**kwargs)
    log.info(
        "llm call model=%s stop=%s in=%s out=%s",
        cfg.llm_model_id,
        resp.stop_reason,
        resp.usage.input_tokens,
        resp.usage.output_tokens,
    )
    return resp
