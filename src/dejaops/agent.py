"""The agent loop: Claude (via Bedrock) + tools that read/write CockroachDB memory.

A manual tool-use loop rather than a framework: every memory read and write is
an explicit, auditable call, and the loop records a `memory_trace` of each one
so the UI can show CockroachDB working — recalled chunks, similarity scores,
ledger outcomes — rather than asking anyone to take the agent's word for it.
"""

import logging
from typing import Any

from . import db, ledger, memory
from .config import settings
from .llm import create_message

log = logging.getLogger("dejaops.agent")

SYSTEM_PROMPT = """You are DejaOps, an on-call incident copilot. Your memory is a CockroachDB \
cluster holding every past incident, distilled runbooks, and a ledger of every action you have taken.

Working an incident:
1. Recall first: search episodic memory for similar past incidents and semantic memory for runbooks \
before proposing anything. Cite what you found (titles, root causes) so the operator can judge relevance.
2. Record findings: store important observations in working memory as you go.
3. Remediate through the ledger: use execute_remediation for any action. It is exactly-once — if it \
reports duplicate_suppressed, the action already ran; do not attempt it another way.
4. When the operator confirms the incident is fixed, call resolve_incident with root cause and resolution.

Be concise and operational. Lead with your best hypothesis and the evidence from memory."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "recall_similar_incidents",
        "description": (
            "Semantic search over episodic memory (past incidents) in CockroachDB. "
            "Call this first with the symptoms; returns past incidents with root causes, "
            "resolutions, and similarity scores."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Symptom description to search for"},
                "k": {"type": "integer", "description": "Max results (default 4)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_runbooks",
        "description": (
            "Semantic search over semantic memory (distilled runbooks). Call when you need "
            "step-by-step diagnostic or remediation guidance for a known failure class."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "record_finding",
        "description": (
            "Store an observation in working memory for this incident (auto-expires via TTL). "
            "Use for hypotheses, ruled-out causes, and key facts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Short slug, e.g. 'hypothesis'"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "execute_remediation",
        "description": (
            "Perform a remediation action through the exactly-once action ledger. The action "
            "and its memory record commit in one CockroachDB transaction; retries are "
            "deduplicated by idempotency key. Actions: restart_service, rollback_deploy, "
            "scale_up, flush_connection_pool, rotate_certificate, disable_feature_flag."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "target": {"type": "string", "description": "Service or component to act on"},
                "reason": {"type": "string", "description": "Why this action, citing recalled evidence"},
            },
            "required": ["action", "target", "reason"],
        },
    },
    {
        "name": "resolve_incident",
        "description": "Mark the incident resolved and trigger memory consolidation (episode + runbook).",
        "input_schema": {
            "type": "object",
            "properties": {
                "root_cause": {"type": "string"},
                "resolution": {"type": "string"},
            },
            "required": ["root_cause", "resolution"],
        },
    },
]


def _run_tool(name: str, args: dict, incident: dict, trace: list[dict]) -> Any:
    incident_id = str(incident["id"])
    if name == "recall_similar_incidents":
        hits = memory.recall(
            args["query"], tier="episodic", k=int(args.get("k", 4)), service=incident.get("service")
        )
        trace.append({"op": "recall", "tier": "episodic", "query": args["query"], "hits": hits})
        return hits
    if name == "search_runbooks":
        hits = memory.recall(args["query"], tier="semantic", k=3, service=incident.get("service"))
        trace.append({"op": "recall", "tier": "semantic", "query": args["query"], "hits": hits})
        return hits
    if name == "record_finding":
        memory.set_working_memory(incident_id, args["key"], args["value"])
        trace.append({"op": "working_memory_write", "key": args["key"], "value": args["value"]})
        return {"stored": True, "expires": "TTL 4h"}
    if name == "execute_remediation":
        result = ledger.execute_exactly_once(
            incident_id=incident_id,
            action=args["action"],
            target=args["target"],
            reason=args["reason"],
        )
        trace.append({"op": "ledger", "action": args["action"], "target": args["target"], "result": result})
        return result
    if name == "resolve_incident":
        db.execute(
            "UPDATE incidents SET status = 'resolved', root_cause = %s, resolution = %s, resolved_at = now() WHERE id = %s",
            (args["root_cause"], args["resolution"], incident_id),
        )
        memory.remember_event(incident_id, "agent", f"Resolved. Root cause: {args['root_cause']}")
        summary = memory.consolidate_incident(incident_id)
        trace.append({"op": "consolidate", **summary})
        return {"resolved": True, **summary}
    return {"error": f"unknown tool {name}"}


def run_turn(incident: dict, user_message: str) -> dict:
    """One agent turn for an incident. Returns final text + full memory trace."""
    incident_id = str(incident["id"])
    trace: list[dict] = []

    wm = memory.get_working_memory(incident_id)
    context = (
        f"Incident #{incident_id[:8]} — {incident['title']}\n"
        f"Service: {incident['service']} | Severity: {incident['severity']} | Status: {incident['status']}\n"
    )
    if wm:
        context += "Working memory (your earlier findings):\n" + "\n".join(
            f"- {r['key']}: {r['value']}" for r in wm
        )

    memory.remember_event(incident_id, "user", user_message)
    messages: list[dict] = [{"role": "user", "content": f"{context}\n\nOperator: {user_message}"}]

    final_text = ""
    for _ in range(settings().max_agent_iterations):
        resp = create_message(system=SYSTEM_PROMPT, messages=messages, tools=TOOLS)
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        text = "".join(b.text for b in resp.content if b.type == "text")
        if text:
            final_text = text

        if resp.stop_reason != "tool_use" or not tool_uses:
            break

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            log.info("tool call %s(%s)", tu.name, tu.input)
            try:
                out = _run_tool(tu.name, dict(tu.input), incident, trace)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": _to_json(out)})
            except Exception as exc:  # surface tool failures to the model, don't crash the turn
                log.exception("tool %s failed", tu.name)
                results.append(
                    {"type": "tool_result", "tool_use_id": tu.id, "content": str(exc), "is_error": True}
                )
        messages.append({"role": "user", "content": results})

    if final_text:
        memory.remember_event(incident_id, "agent", final_text)
    return {"reply": final_text, "memory_trace": trace}


def _to_json(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str)
