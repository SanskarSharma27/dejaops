# Memory design

This document explains *why* the memory layer is shaped the way it is. The schema itself is `db/schema.sql`.

## The thesis

Most "agent memory" is a vector store bolted onto an app: embed → search → stuff into prompt. That answers *recall* and nothing else. A production agent's memory also has to be:

- **trustworthy under retry** — agents time out and retry; side effects must not double,
- **auditable in time** — postmortems ask what the agent knew *when it acted*, not what it knows now,
- **self-limiting** — working state must expire, or the memory becomes a landfill,
- **consistent with the system of record** — recall that lags reality is worse than no recall.

Each of those is a transactional-database property, not a vector-store property. That's the design bet: put the vectors *inside* the transactional database instead of next to it.

## Tiers (cognitive model → tables)

| Tier | Table(s) | Lifecycle |
|---|---|---|
| **Working** — current-incident scratchpad | `working_memory` | Row-level TTL (4h). Hypotheses and findings evaporate on their own; nothing hoards. |
| **Episodic** — what happened | `incidents`, `incident_events`, `memory_chunks(tier='episodic')` | Permanent. Vector-indexed by symptom description. |
| **Semantic** — what we learned | `runbooks`, `memory_chunks(tier='semantic')` | Permanent. Produced by consolidation, below. |

**Consolidation** is the working→long-term step: on `resolve_incident`, the full episode (timeline + root cause + resolution) is embedded into the episodic tier, and Claude distills a reusable runbook into the semantic tier. Future incidents recall both: "this happened before" *and* "here's the procedure we wrote afterward."

## Recall: vector indexing decisions

- `VECTOR(1024)` — Titan Text Embeddings V2. The dimension is fixed at column creation; changing embedding models is a migration by design.
- `CREATE VECTOR INDEX ... ON memory_chunks (tier, embedding)` — `tier` is a **prefix column**, so the tier filter is pushed into the index scan rather than post-filtered.
- **Only L2 (`<->`) is index-accelerated** in CockroachDB. All embeddings are L2-normalized to unit length before storage; on unit vectors, L2 ordering is monotonically equivalent to cosine ordering, so we get cosine semantics on the accelerated path. The UI reports cosine similarity computed as `1 − d²/2`.
- The index is created while the table is empty (creating it on a populated table blocks writes), and seeding uses `INSERT` (`IMPORT INTO` is unsupported on vector-indexed tables).
- Freshness is transactional: a chunk inserted in a committed transaction is immediately searchable. There is no indexing lag and no sync pipeline, because there is no second datastore.

## Exactly-once actions: the ledger

`action_ledger` has a `UNIQUE idempotency_key` derived deterministically from `(incident_id, action, target)`. `execute_exactly_once` runs one CockroachDB transaction that:

1. claims the key (`INSERT ... ON CONFLICT DO NOTHING RETURNING id`) — if no row returns, the action already happened; return the original result and **do not re-execute**;
2. performs the side effect;
3. writes the incident-event memory record and the `memory_versions` row.

All-or-nothing: there is no state where the action happened but memory doesn't know, or memory claims an action that never committed. CockroachDB's serializable isolation is what makes step 1 a real claim under concurrency; contention surfaces as SQLSTATE 40001 and is retried client-side (`db.with_retry`) — and those retries are exactly the case the idempotency key exists for.

## Replay: two mechanisms, honestly separated

"What did the agent know at time T?" is answered two ways:

- **`AS OF SYSTEM TIME`** — a true time-travel read, no bookkeeping needed. Its reach is bounded by the cluster's GC window (`gc.ttlseconds`, often hours on managed tiers).
- **`memory_versions`** — an append-only row written *in the same transaction* as every memory mutation. Unbounded reach.

`memory.replay()` tries AOST first and falls back to `memory_versions` when the target time is outside the window, labeling which source answered. Demoing time travel without the fallback is a demo that breaks the day after you record it; this design states the limitation and engineers around it.

## What we deliberately did not build

- **No multi-region (`REGIONAL BY ROW`)** — the schema is one `ALTER TABLE` away from pinning EU incidents' memory to EU nodes, but a single-region free-tier deployment demos better than a claimed-but-untested topology.
- **No changefeed-driven consolidation** — consolidation runs synchronously on resolve. A changefeed into a background worker is the production shape; synchronous is the honest hackathon shape.
- **No framework memory abstraction** — every read and write is explicit SQL in `memory.py`/`ledger.py`, because the memory layer *is* the project.
