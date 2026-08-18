"""Reset the memory layer to the curated seed corpus.

Working an incident writes into memory by design — and resolving one consolidates
it into a *new* episodic chunk and a generated runbook. That's the product working
correctly, but it means each rehearsal take leaves artifacts behind that compete
with the curated corpus in recall results.

This removes everything the demo created (any incident without a `seed-` key, and
any runbook not from `seed/incidents.json`) while leaving the seeded history and
its embeddings untouched — no re-embedding, so no rate-limit wait.

Usage: DATABASE_URL=... python scripts/reset_demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dejaops import db  # noqa: E402


def main() -> int:
    extra = db.query(
        "SELECT id, title FROM incidents WHERE external_key IS NULL OR external_key NOT LIKE 'seed-%'"
    )
    for row in extra:
        iid = str(row["id"])
        for table in ("action_ledger", "memory_versions", "working_memory", "incident_events"):
            db.execute(f"DELETE FROM {table} WHERE incident_id = %s", (iid,))
        db.execute("DELETE FROM memory_chunks WHERE source_id = %s", (iid,))
        db.execute("DELETE FROM incidents WHERE id = %s", (iid,))
        print(f"removed incident: {row['title'][:60]}")

    # Consolidation-generated runbooks: the seeded ones all start with "Runbook:".
    generated = db.query("SELECT id, title FROM runbooks WHERE title NOT LIKE 'Runbook:%'")
    for row in generated:
        db.execute("DELETE FROM memory_chunks WHERE source_id = %s", (str(row["id"]),))
        db.execute("DELETE FROM runbooks WHERE id = %s", (str(row["id"]),))
        print(f"removed generated runbook: {row['title'][:60]}")

    # Working memory rows are TTL'd anyway, but clear them so a take starts clean.
    db.execute("DELETE FROM working_memory")

    counts = {
        t: db.query_one(f"SELECT count(*) c FROM {t}")["c"]
        for t in ("incidents", "runbooks", "memory_chunks", "action_ledger")
    }
    print(f"\nreset complete: {counts}")
    if counts["incidents"] != 12 or counts["memory_chunks"] != 17:
        print("WARNING: expected 12 incidents / 17 chunks — reseed with scripts/seed.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
