"""Initialize the CockroachDB schema.

Usage: DATABASE_URL=... python scripts/init_db.py

Idempotent (IF NOT EXISTS throughout). Attempts to enable the vector-index
feature flag first — on managed tiers where cluster settings are locked down
this is skipped, which is fine when vector indexes are already GA there.
"""

import os
import sys
from pathlib import Path

import psycopg

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1

    statements = [s.strip() for s in SCHEMA.read_text().split(";") if s.strip()]

    with psycopg.connect(url, autocommit=True) as conn:
        try:
            conn.execute("SET CLUSTER SETTING feature.vector_index.enabled = true")
            print("vector index feature flag enabled")
        except psycopg.Error as exc:
            print(f"note: could not set cluster setting ({exc}); "
                  "fine if vector indexes are GA on this cluster")

        for stmt in statements:
            head = " ".join(stmt.split()[:6])
            try:
                conn.execute(stmt)
                print(f"ok: {head}")
            except psycopg.Error as exc:
                print(f"FAILED: {head}\n  -> {exc}", file=sys.stderr)
                if "VECTOR INDEX" in stmt.upper():
                    print(
                        "  Vector index creation failed. Verify the cluster is v25.2+ "
                        "and vector indexing is available on this tier.",
                        file=sys.stderr,
                    )
                return 1
    print("schema ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
