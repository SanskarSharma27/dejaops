"""The memory layer: recall, write, working memory, consolidation, and replay.

Tier map (all CockroachDB):
  working  -> working_memory (row-level TTL; evaporates on its own)
  episodic -> incidents + memory_chunks(tier='episodic'), vector-indexed
  semantic -> runbooks  + memory_chunks(tier='semantic'), vector-indexed

Replay ("what did the agent know at time T?") uses AS OF SYSTEM TIME for recent
history — a genuinely time-traveling read — and falls back to the append-only
memory_versions table when T is older than the cluster's GC window.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any

import psycopg

from . import db
from .embeddings import embed
from .llm import create_message

log = logging.getLogger("dejaops.memory")


# --- recall -------------------------------------------------------------------


def recall(query: str, *, tier: str, k: int = 5, service: str | None = None) -> list[dict]:
    """Semantic search over one memory tier. Returns chunks with similarity.

    The vector index is prefixed on `tier`, so this filter is pushed down into
    the index scan. Similarity is reported as cosine (vectors are unit-length,
    so cosine = 1 - L2²/2).
    """
    qvec = db.to_pgvector(list(embed(query, "query")))
    sql = """
        SELECT id, tier, source_kind, source_id, service, content,
               embedding <-> %s::VECTOR AS l2_distance
        FROM memory_chunks
        WHERE tier = %s
        ORDER BY embedding <-> %s::VECTOR
        LIMIT %s
    """
    rows = db.query(sql, (qvec, tier, qvec, k))
    out = []
    for r in rows:
        d = float(r["l2_distance"])
        r["similarity"] = round(1.0 - (d * d) / 2.0, 4)
        r.pop("l2_distance")
        r["id"] = str(r["id"])
        r["source_id"] = str(r["source_id"]) if r["source_id"] else None
        out.append(r)
    if service:
        # soft preference: same-service memories float to the top on ties
        out.sort(key=lambda r: (r.get("service") != service, -r["similarity"]))
    return out


# --- writes (every write also lands in memory_versions, same transaction) -----


def remember_event(incident_id: str, kind: str, body: str) -> None:
    def txn(conn: psycopg.Connection) -> None:
        with conn.transaction():
            ev = conn.execute(
                "INSERT INTO incident_events (incident_id, kind, body) VALUES (%s, %s, %s) RETURNING id",
                (incident_id, kind, body),
            ).fetchone()
            conn.execute(
                "INSERT INTO memory_versions (incident_id, table_name, op, row_data) VALUES (%s, 'incident_events', 'insert', %s)",
                (incident_id, json.dumps({"event_id": str(ev["id"]), "kind": kind, "body": body})),
            )

    db.with_retry(txn)


def set_working_memory(incident_id: str, key: str, value: str) -> None:
    db.execute(
        """
        UPSERT INTO working_memory (id, incident_id, key, value)
        VALUES (
            COALESCE((SELECT id FROM working_memory WHERE incident_id = %s AND key = %s), gen_random_uuid()),
            %s, %s, %s
        )
        """,
        (incident_id, key, incident_id, key, value),
    )


def get_working_memory(incident_id: str) -> list[dict]:
    rows = db.query(
        "SELECT key, value, created_at FROM working_memory WHERE incident_id = %s ORDER BY created_at",
        (incident_id,),
    )
    for r in rows:
        r["created_at"] = str(r["created_at"])
    return rows


def store_chunk(
    *,
    tier: str,
    source_kind: str,
    source_id: str | None,
    service: str | None,
    content: str,
) -> str:
    """Embed and store one memory chunk (episodic or semantic)."""
    vec = db.to_pgvector(list(embed(content)))

    def txn(conn: psycopg.Connection) -> str:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO memory_chunks (tier, source_kind, source_id, service, content, embedding)
                VALUES (%s, %s, %s, %s, %s, %s::VECTOR)
                RETURNING id
                """,
                (tier, source_kind, source_id, service, content, vec),
            ).fetchone()
            conn.execute(
                "INSERT INTO memory_versions (incident_id, table_name, op, row_data) VALUES (%s, 'memory_chunks', 'insert', %s)",
                (
                    source_id if source_kind == "incident" else None,
                    json.dumps({"chunk_id": str(row["id"]), "tier": tier, "content": content[:500]}),
                ),
            )
            return str(row["id"])

    return db.with_retry(txn)


# --- consolidation: episodic incidents -> semantic runbook --------------------


def consolidate_incident(incident_id: str) -> dict:
    """Distill a resolved incident into episodic memory + a semantic runbook.

    This is the working->long-term consolidation step: the incident's full
    story becomes an episodic chunk (searchable by symptom), and the LLM
    distills a reusable runbook into the semantic tier.
    """
    inc = db.query_one("SELECT * FROM incidents WHERE id = %s", (incident_id,))
    if not inc:
        raise ValueError(f"incident {incident_id} not found")
    events = db.query(
        "SELECT ts, kind, body FROM incident_events WHERE incident_id = %s ORDER BY ts",
        (incident_id,),
    )
    timeline = "\n".join(f"- [{e['kind']}] {e['body']}" for e in events)
    episode = (
        f"Incident: {inc['title']} (service: {inc['service']}, severity: {inc['severity']})\n"
        f"Root cause: {inc['root_cause'] or 'unknown'}\n"
        f"Resolution: {inc['resolution'] or 'unknown'}\n"
        f"Timeline:\n{timeline}"
    )
    chunk_id = store_chunk(
        tier="episodic",
        source_kind="incident",
        source_id=incident_id,
        service=inc["service"],
        content=episode,
    )

    resp = create_message(
        system=(
            "You distill incident postmortems into short, reusable runbooks. "
            "Output only the runbook: a title line, then numbered diagnostic steps, "
            "then remediation steps. No preamble."
        ),
        messages=[{"role": "user", "content": episode}],
        max_tokens=600,
    )
    runbook_body = "".join(b.text for b in resp.content if b.type == "text").strip()
    title = runbook_body.splitlines()[0][:200] if runbook_body else f"Runbook: {inc['title']}"

    def txn(conn: psycopg.Connection) -> str:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO runbooks (service, title, body, derived_from) VALUES (%s, %s, %s, %s) RETURNING id",
                (inc["service"], title, runbook_body, [incident_id]),
            ).fetchone()
            return str(row["id"])

    runbook_id = db.with_retry(txn)
    store_chunk(
        tier="semantic",
        source_kind="runbook",
        source_id=runbook_id,
        service=inc["service"],
        content=f"{title}\n{runbook_body}",
    )
    log.info("consolidated incident=%s -> episode=%s runbook=%s", incident_id, chunk_id, runbook_id)
    return {"episodic_chunk_id": chunk_id, "runbook_id": runbook_id, "runbook_title": title}


# --- replay -------------------------------------------------------------------

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def replay(incident_id: str, at: str) -> dict[str, Any]:
    """Reconstruct what the agent knew about an incident at time `at`.

    Tries a true time-travel read first (AS OF SYSTEM TIME). If `at` is beyond
    the GC window — CockroachDB refuses reads older than gc.ttlseconds — falls
    back to the append-only memory_versions table, which we write on every
    memory mutation for exactly this reason.
    """
    if not _TS_RE.match(at.strip()):
        raise ValueError("`at` must be an ISO timestamp like 2026-08-11T14:30:00Z")
    # Validated above; AS OF SYSTEM TIME does not accept bind placeholders,
    # so the literal is interpolated only after the regex check.
    ts_literal = at.strip().replace("T", " ")

    try:
        def aost(conn: psycopg.Connection) -> list[dict]:
            conn.autocommit = True  # AOST reads must be outside an explicit txn
            return conn.execute(
                f"""
                SELECT ts, kind, body FROM incident_events
                AS OF SYSTEM TIME '{ts_literal}'
                WHERE incident_id = %s ORDER BY ts
                """,
                (incident_id,),
            ).fetchall()

        rows = db.with_retry(aost)
        source = "as_of_system_time"
        events = [{"ts": str(r["ts"]), "kind": r["kind"], "body": r["body"]} for r in rows]
    except psycopg.Error as exc:
        log.info("AOST unavailable for %s (%s); using memory_versions", at, exc.sqlstate)
        rows = db.query(
            """
            SELECT ts, row_data FROM memory_versions
            WHERE incident_id = %s AND ts <= %s AND table_name = 'incident_events'
            ORDER BY ts
            """,
            (incident_id, datetime.fromisoformat(at.replace("Z", "+00:00"))),
        )
        source = "memory_versions_fallback"
        events = [
            {"ts": str(r["ts"]), "kind": r["row_data"].get("kind"), "body": r["row_data"].get("body") or json.dumps(r["row_data"])}
            for r in rows
        ]

    return {"incident_id": incident_id, "as_of": at, "source": source, "events": events}
