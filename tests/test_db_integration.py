"""Integration tests against a real CockroachDB (skipped unless DATABASE_URL set).

Run: DATABASE_URL=... FAKE_EMBEDDINGS=1 pytest tests/test_db_integration.py -v

Covers the load-bearing claims: vector recall returns ranked results, the
action ledger is exactly-once under retry, working memory round-trips, and
replay reconstructs prior knowledge state.
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"
)

os.environ.setdefault("FAKE_EMBEDDINGS", "1")

from dejaops import db, ledger, memory  # noqa: E402


@pytest.fixture()
def incident_id():
    row = db.query_one(
        "INSERT INTO incidents (service, title, severity, summary) "
        "VALUES ('test-service', 'integration test incident', 'SEV4', 'test') RETURNING id"
    )
    iid = str(row["id"])
    yield iid
    db.execute("DELETE FROM action_ledger WHERE incident_id = %s", (iid,))
    db.execute("DELETE FROM memory_versions WHERE incident_id = %s", (iid,))
    db.execute("DELETE FROM working_memory WHERE incident_id = %s", (iid,))
    db.execute("DELETE FROM incidents WHERE id = %s", (iid,))


def test_vector_recall_ranks_by_similarity(incident_id):
    marker = uuid.uuid4().hex[:8]
    memory.store_chunk(
        tier="episodic", source_kind="incident", source_id=incident_id,
        service="test-service",
        content=f"{marker} database connection pool exhausted causing checkout timeouts",
    )
    memory.store_chunk(
        tier="episodic", source_kind="incident", source_id=incident_id,
        service="test-service",
        content=f"{marker} completely unrelated: printer out of toner on floor three",
    )
    hits = memory.recall(f"{marker} connection pool exhaustion timeouts", tier="episodic", k=5)
    contents = [h["content"] for h in hits]
    pool_rank = next(i for i, c in enumerate(contents) if "connection pool" in c)
    toner_rank = next((i for i, c in enumerate(contents) if "toner" in c), len(contents))
    assert pool_rank < toner_rank
    assert 0 <= hits[0]["similarity"] <= 1
    for chunk in db.query("SELECT id FROM memory_chunks WHERE content LIKE %s", (f"{marker}%",)):
        db.execute("DELETE FROM memory_chunks WHERE id = %s", (chunk["id"],))


def test_ledger_exactly_once(incident_id):
    first = ledger.execute_exactly_once(
        incident_id=incident_id, action="restart_service",
        target="test-service", reason="integration test",
    )
    assert first["status"] == "applied"

    retry = ledger.execute_exactly_once(
        incident_id=incident_id, action="restart_service",
        target="test-service", reason="retried after fake timeout",
    )
    assert retry["status"] == "duplicate_suppressed"
    assert retry["idempotency_key"] == first["idempotency_key"]

    rows = db.query(
        "SELECT * FROM action_ledger WHERE incident_id = %s AND action = 'restart_service'",
        (incident_id,),
    )
    assert len(rows) == 1  # one ledger row, one side effect

    events = db.query(
        "SELECT * FROM incident_events WHERE incident_id = %s AND kind = 'action'",
        (incident_id,),
    )
    assert len(events) == 1  # memory write happened exactly once too


def test_working_memory_roundtrip(incident_id):
    memory.set_working_memory(incident_id, "hypothesis", "pool exhaustion")
    memory.set_working_memory(incident_id, "hypothesis", "pool exhaustion after deploy")
    rows = memory.get_working_memory(incident_id)
    assert len(rows) == 1  # upsert, not append
    assert rows[0]["value"] == "pool exhaustion after deploy"


def test_replay_returns_prior_state(incident_id):
    memory.remember_event(incident_id, "observation", "first observation")
    out = memory.replay(incident_id, "2100-01-01T00:00:00Z")  # future => sees everything
    assert out["source"] in ("as_of_system_time", "memory_versions_fallback")
    # A "future" AOST read fails (cannot read future timestamps), so this also
    # exercises the memory_versions fallback path.
    assert any("first observation" in (e.get("body") or "") for e in out["events"])
