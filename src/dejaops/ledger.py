"""Exactly-once action execution backed by the CockroachDB action ledger.

The core claim of DejaOps: an agent that retries a tool call must not perform
the side effect twice. Every remediation goes through `execute_exactly_once`,
which — inside ONE CockroachDB transaction — claims the idempotency key,
performs the (simulated) side effect, and writes the memory records for it.
Either all of that commits or none of it does; a retry with the same key finds
the claimed row and returns the original result without re-executing.
"""

import hashlib
import json
import logging
import re
from typing import Any

import psycopg

from . import db

log = logging.getLogger("dejaops.ledger")


# Filler the model tends to prepend when naming a target ("service payment-gateway",
# "the payment-gateway service"). Stripped so it can't fracture the key.
_TARGET_FILLER = {"service", "the", "a", "an", "on", "for", "app", "application", "deployment"}


def canonical_target(target: str) -> str:
    """Normalize a model-supplied target into a stable identifier.

    The idempotency key must not depend on incidental wording. Claude may say
    "payment-gateway" on one call and "service payment-gateway" on the retry;
    both denote the same thing and must hash identically, or the ledger's
    exactly-once guarantee silently degrades into at-least-once.
    """
    cleaned = re.sub(r"[^a-z0-9\s\-_]", " ", target.lower())
    words = [w for w in re.split(r"[\s_]+", cleaned) if w and w not in _TARGET_FILLER]
    return "-".join("-".join(words).split("-")) if words else target.strip().lower()


def idempotency_key(incident_id: str, action: str, target: str) -> str:
    """Deterministic key: the same remediation on the same incident is one action."""
    raw = f"{incident_id}|{action.strip().lower()}|{canonical_target(target)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# Simulated remediation catalog. In production these would call real APIs
# (Kubernetes, feature flags, deploy system); the exactly-once mechanics around
# them are identical.
KNOWN_ACTIONS = {
    "restart_service": "Rolling restart issued for {target}",
    "rollback_deploy": "Deploy rolled back to previous version on {target}",
    "scale_up": "Capacity increased (+2 instances) for {target}",
    "flush_connection_pool": "Connection pool drained and rebuilt for {target}",
    "rotate_certificate": "Certificate rotation triggered for {target}",
    "disable_feature_flag": "Feature flag disabled for {target}",
}


def execute_exactly_once(
    *,
    incident_id: str,
    action: str,
    target: str,
    reason: str,
) -> dict[str, Any]:
    if action not in KNOWN_ACTIONS:
        return {
            "status": "rejected",
            "detail": f"unknown action '{action}'; known: {sorted(KNOWN_ACTIONS)}",
        }

    key = idempotency_key(incident_id, action, target)
    target = canonical_target(target)  # what actually gets acted on and recorded
    args = {"target": target, "reason": reason}

    def txn(conn: psycopg.Connection) -> dict[str, Any]:
        with conn.transaction():
            claimed = conn.execute(
                """
                INSERT INTO action_ledger (idempotency_key, incident_id, action, args, status)
                VALUES (%s, %s, %s, %s, 'in_progress')
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """,
                (key, incident_id, action, json.dumps(args)),
            ).fetchone()

            if claimed is None:
                prior = conn.execute(
                    "SELECT status, result, applied_at FROM action_ledger WHERE idempotency_key = %s",
                    (key,),
                ).fetchone()
                log.info("dedupe hit action=%s key=%s", action, key)
                return {
                    "status": "duplicate_suppressed",
                    "detail": (
                        "This exact action was already performed for this incident; "
                        "the side effect was NOT re-executed."
                    ),
                    "original_result": prior["result"] if prior else None,
                    "originally_applied_at": str(prior["applied_at"]) if prior else None,
                    "idempotency_key": key,
                }

            # --- the side effect (simulated) runs inside the claim ---
            result = KNOWN_ACTIONS[action].format(target=target)

            conn.execute(
                "UPDATE action_ledger SET status = 'applied', result = %s WHERE id = %s",
                (result, claimed["id"]),
            )
            # Memory write in the SAME transaction as the action record:
            event = conn.execute(
                """
                INSERT INTO incident_events (incident_id, kind, body)
                VALUES (%s, 'action', %s)
                RETURNING id, ts
                """,
                (incident_id, f"[{action}] {result} — reason: {reason}"),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO memory_versions (incident_id, table_name, op, row_data)
                VALUES (%s, 'incident_events', 'insert', %s)
                """,
                (
                    incident_id,
                    json.dumps(
                        {
                            "event_id": str(event["id"]),
                            "kind": "action",
                            "action": action,
                            "target": target,
                            "result": result,
                        }
                    ),
                ),
            )
        log.info("action applied action=%s target=%s key=%s", action, target, key)
        return {"status": "applied", "result": result, "idempotency_key": key}

    return db.with_retry(txn)
