-- DejaOps memory layer schema (CockroachDB)
--
-- Three memory tiers, all in one transactional database:
--   working  : working_memory        -- current-incident scratchpad, decays via row-level TTL
--   episodic : incidents + memory_chunks(tier='episodic')  -- past incidents, vector-indexed
--   semantic : runbooks  + memory_chunks(tier='semantic')  -- distilled knowledge, vector-indexed
--
-- Plus two tables that make the memory trustworthy rather than just searchable:
--   action_ledger   : written in the SAME transaction as the memory record -> exactly-once actions
--   memory_versions : append-only history backing replay beyond the AS OF SYSTEM TIME GC window

CREATE TABLE IF NOT EXISTS incidents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_key STRING UNIQUE,                    -- alert dedupe key from the monitoring source
    service      STRING NOT NULL,
    title        STRING NOT NULL,
    severity     STRING NOT NULL DEFAULT 'SEV3',
    status       STRING NOT NULL DEFAULT 'open',   -- open | mitigated | resolved
    summary      STRING,
    root_cause   STRING,
    resolution   STRING,
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ,
    INDEX incidents_service_idx (service, opened_at DESC)
);

CREATE TABLE IF NOT EXISTS incident_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        STRING NOT NULL,                   -- alert | observation | action | user | agent
    body        STRING NOT NULL,
    INDEX incident_events_incident_idx (incident_id, ts)
);

-- Long-term vector memory. The vector index MUST be created while the table is
-- empty: adding it to a populated table blocks writes for the duration.
CREATE TABLE IF NOT EXISTS memory_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tier        STRING NOT NULL,                   -- episodic | semantic
    source_kind STRING NOT NULL,                   -- incident | runbook
    source_id   UUID,
    service     STRING,
    content     STRING NOT NULL,
    embedding   VECTOR(1024) NOT NULL,             -- Titan V2, L2-normalized (dim fixed at creation)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Prefix column `tier` enables filter pushdown; only L2 (<->) is accelerated,
-- which is why embeddings are stored L2-normalized (L2 order == cosine order).
CREATE VECTOR INDEX IF NOT EXISTS memory_chunks_embedding_idx
    ON memory_chunks (tier, embedding);

-- Working memory: the agent's scratchpad for in-flight incidents.
-- Rows evaporate automatically via CockroachDB row-level TTL.
CREATE TABLE IF NOT EXISTS working_memory (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL,
    key         STRING NOT NULL,
    value       STRING NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE INDEX working_memory_incident_key_idx (incident_id, key)
) WITH (ttl_expire_after = '4 hours');

CREATE TABLE IF NOT EXISTS runbooks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service      STRING NOT NULL,
    title        STRING NOT NULL,
    body         STRING NOT NULL,
    derived_from UUID[],                           -- incident ids consolidated into this runbook
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Exactly-once action ledger. The INSERT here and the memory write for the same
-- action commit in ONE transaction; a retried tool call hits the UNIQUE
-- idempotency_key and performs no second side effect.
CREATE TABLE IF NOT EXISTS action_ledger (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key STRING NOT NULL UNIQUE,
    incident_id     UUID,
    action          STRING NOT NULL,
    args            JSONB NOT NULL DEFAULT '{}',
    status          STRING NOT NULL DEFAULT 'applied',
    result          STRING,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only record of every memory mutation. AS OF SYSTEM TIME can only read
-- back as far as the cluster GC window (often hours on managed tiers), so this
-- table backs "what did the agent know at time T" for arbitrary history.
CREATE TABLE IF NOT EXISTS memory_versions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    incident_id UUID,
    table_name  STRING NOT NULL,
    op          STRING NOT NULL,                   -- insert | update
    row_data    JSONB NOT NULL,
    INDEX memory_versions_incident_idx (incident_id, ts)
);
