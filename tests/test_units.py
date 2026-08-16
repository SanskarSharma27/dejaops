"""Unit tests that run with no database and no AWS (pure functions + fakes)."""

import math

import pytest

from dejaops import db
from dejaops.embeddings import _fake_embedding, l2_normalize
from dejaops.ledger import idempotency_key
from dejaops.memory import _TS_RE


def test_l2_normalize_unit_length():
    vec = l2_normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-9)


def test_l2_normalize_zero_vector_safe():
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]


def test_fake_embedding_deterministic_and_unit():
    a = _fake_embedding("connection pool exhausted on payment gateway", 256)
    b = _fake_embedding("connection pool exhausted on payment gateway", 256)
    assert a == b
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0, rel_tol=1e-6)


def test_fake_embedding_similar_texts_rank_higher():
    def cos(u, v):
        return sum(x * y for x, y in zip(u, v))

    base = _fake_embedding("checkout timeouts and connection pool exhaustion", 512)
    related = _fake_embedding("timeouts on checkout, the connection pool is exhausted", 512)
    unrelated = _fake_embedding("ios push notifications failing with apns 403", 512)
    assert cos(base, related) > cos(base, unrelated)


def test_pgvector_roundtrip():
    vec = [0.1, -0.25, 3.0, 0.0]
    text = db.to_pgvector(vec)
    assert text.startswith("[") and text.endswith("]")
    back = db.from_pgvector(text)
    assert all(math.isclose(x, y, rel_tol=1e-6) for x, y in zip(vec, back))


def test_idempotency_key_stable_and_distinct():
    k1 = idempotency_key("inc-1", "restart_service", "auth-service")
    k2 = idempotency_key("inc-1", "restart_service", "auth-service")
    k3 = idempotency_key("inc-1", "restart_service", "orders-api")
    assert k1 == k2 != k3
    assert len(k1) == 32


@pytest.mark.parametrize(
    "ts,ok",
    [
        ("2026-08-16T14:30:00Z", True),
        ("2026-08-16 14:30:00", True),
        ("2026-08-16T14:30:00+05:30", True),
        ("now() - INTERVAL '1h'; DROP TABLE incidents;--", False),
        ("yesterday", False),
    ],
)
def test_replay_timestamp_validation(ts, ok):
    assert bool(_TS_RE.match(ts)) == ok
