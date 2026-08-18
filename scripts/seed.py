"""Seed the memory layer with historical incidents and runbooks.

Usage: DATABASE_URL=... python scripts/seed.py            (Bedrock embeddings)
       DATABASE_URL=... FAKE_EMBEDDINGS=1 python scripts/seed.py   (offline)

Inserts row-by-row on purpose: IMPORT INTO is not supported on tables with a
vector index, and the corpus is small. Idempotent via external_key dedupe.

--wipe-all first truncates ALL application tables. Use it when switching
embedding modes (fake <-> Titan): vectors from different embedding spaces must
never coexist, or similarity search silently degrades.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dejaops import db  # noqa: E402
from dejaops.embeddings import embed  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "seed" / "incidents.json"


def wipe_all() -> None:
    for table in (
        "memory_versions", "action_ledger", "working_memory",
        "memory_chunks", "incident_events", "runbooks", "incidents",
    ):
        db.execute(f"DELETE FROM {table}")
        print(f"wiped {table}")


def main() -> int:
    if "--wipe-all" in sys.argv:
        wipe_all()
    payload = json.loads(DATA.read_text())
    now = datetime.now(timezone.utc)
    inserted = skipped = 0

    for inc in payload["incidents"]:
        # Require the episodic chunk too, not just the incident row: a run that
        # dies mid-embedding (rate limits) would otherwise leave the incident
        # behind and skip it forever, silently losing it from vector recall.
        done = db.query_one(
            """
            SELECT 1 FROM incidents i
            JOIN memory_chunks m ON m.source_id = i.id AND m.tier = 'episodic'
            WHERE i.external_key = %s
            """,
            (inc["external_key"],),
        )
        if done:
            skipped += 1
            continue
        if db.query_one("SELECT 1 FROM incidents WHERE external_key = %s", (inc["external_key"],)):
            print(f"repairing partial seed: {inc['title'][:50]}")
            db.execute("DELETE FROM incidents WHERE external_key = %s", (inc["external_key"],))
        opened = now - timedelta(days=inc["days_ago"])
        resolved = opened + timedelta(hours=3)
        row = db.query_one(
            """
            INSERT INTO incidents (external_key, service, title, severity, status,
                                   summary, root_cause, resolution, opened_at, resolved_at)
            VALUES (%s, %s, %s, %s, 'resolved', %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                inc["external_key"], inc["service"], inc["title"], inc["severity"],
                inc["symptoms"], inc["root_cause"], inc["resolution"], opened, resolved,
            ),
        )
        incident_id = str(row["id"])
        for kind, body in (
            ("alert", inc["symptoms"]),
            ("observation", f"Root cause identified: {inc['root_cause']}"),
            ("action", f"Resolution: {inc['resolution']}"),
        ):
            db.execute(
                "INSERT INTO incident_events (incident_id, kind, body, ts) VALUES (%s, %s, %s, %s)",
                (incident_id, kind, body, opened),
            )

        episode = (
            f"Incident: {inc['title']} (service: {inc['service']}, severity: {inc['severity']})\n"
            f"Symptoms: {inc['symptoms']}\n"
            f"Root cause: {inc['root_cause']}\n"
            f"Resolution: {inc['resolution']}"
        )
        vec = db.to_pgvector(list(embed(episode)))
        db.execute(
            """
            INSERT INTO memory_chunks (tier, source_kind, source_id, service, content, embedding, created_at)
            VALUES ('episodic', 'incident', %s, %s, %s, %s::VECTOR, %s)
            """,
            (incident_id, inc["service"], episode, vec, opened),
        )
        inserted += 1
        print(f"seeded incident: {inc['title'][:60]}")

    for rb in payload["runbooks"]:
        if db.query_one("SELECT 1 FROM runbooks WHERE title = %s", (rb["title"],)):
            continue
        row = db.query_one(
            "INSERT INTO runbooks (service, title, body) VALUES (%s, %s, %s) RETURNING id",
            (rb["service"], rb["title"], rb["body"]),
        )
        content = f"{rb['title']}\n{rb['body']}"
        vec = db.to_pgvector(list(embed(content)))
        db.execute(
            """
            INSERT INTO memory_chunks (tier, source_kind, source_id, service, content, embedding)
            VALUES ('semantic', 'runbook', %s, %s, %s, %s::VECTOR)
            """,
            (str(row["id"]), rb["service"], content, vec),
        )
        print(f"seeded runbook: {rb['title'][:60]}")

    print(f"done: {inserted} incidents inserted, {skipped} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
