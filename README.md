# DejaOps — an on-call copilot that never forgets

*"Have we seen this failure before?" — answered from a memory layer that is transactional, decaying, replayable, and exactly-once.*

DejaOps is an incident-response copilot whose **entire memory is CockroachDB**. It remembers every past incident, every distilled runbook, and every action it has ever taken — and it uses CockroachDB features no bolt-on vector store has to solve real agent-memory problems:

| Agent-memory problem | CockroachDB feature that solves it |
|---|---|
| "Have we seen this before?" | **Distributed vector indexing** over episodic memory (past incidents) and semantic memory (runbooks) |
| Agents duplicate side effects on retry | **Single-transaction action ledger**: the memory write and the action record commit atomically with a unique idempotency key → **exactly-once remediation** |
| Memory should decay, not hoard | **Row-level TTL** on working memory — the current-incident scratchpad evaporates on its own |
| "What did the agent believe at 02:14, and why did it act that way?" | **`AS OF SYSTEM TIME`** time-travel reads, backed by an append-only version table beyond the GC window |
| Episodes should become knowledge | **Consolidation**: resolved incidents are distilled by Claude into runbooks in the semantic tier — all written transactionally |

An agent that retries a tool call must not perform the side effect twice. Because the memory write and the action record commit in a single CockroachDB transaction, remediation is exactly-once. That single sentence is the difference between a RAG demo and a production memory layer.

## Architecture

```
Browser ──> AWS Lambda Function URL (FastAPI + Mangum, container image)
                │
                ├──> Amazon Bedrock
                │      ├── Claude Haiku 4.5 (anthropic.claude-haiku-4-5) — agent loop, consolidation
                │      └── Titan Text Embeddings V2 — 1024-dim, L2-normalized
                │
                └──> CockroachDB Cloud (psycopg3, public TLS — no VPC, no NAT)
                       ├── memory_chunks   VECTOR(1024) + CREATE VECTOR INDEX  → semantic recall
                       ├── working_memory  row-level TTL                       → decaying scratchpad
                       ├── action_ledger   UNIQUE idempotency key, single txn  → exactly-once actions
                       ├── memory_versions append-only                         → knowledge replay
                       └── incidents / incident_events / runbooks              → relational agent state
```

Vectors live **transactionally alongside** the relational agent state — one database, one transaction boundary, no sync pipeline between a vector store and a system of record.

### Hackathon tool usage

**CockroachDB (≥2 required):**
1. **Distributed Vector Indexing** — core recall path (`db/schema.sql`, `src/dejaops/memory.py`). L2-only acceleration is handled by storing unit-normalized embeddings (L2 order ≡ cosine order).
2. **CockroachDB Cloud Managed MCP Server** — wired into the development workflow (Claude Code inspects the live memory layer: schemas, query plans, read-only queries during development and demo).
3. **Agent Skills Repo** — installed via `npx skills add cockroachlabs/cockroachdb-skills`; used for operational guidance (index tuning, query analysis) during development.
4. **ccloud CLI** — cluster and service-account management (see `docs/TOOLS.md` for evidence of all four).

**AWS (≥1 required):** Amazon Bedrock (Claude + Titan), AWS Lambda (compute), SSM Parameter Store (secrets), ECR (image), CloudWatch (logs).

## Run it locally

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Point at a CockroachDB cluster (free tier works) and initialize
export DATABASE_URL='postgresql://...'
python scripts/init_db.py
FAKE_EMBEDDINGS=1 python scripts/seed.py      # offline mode; drop the flag to use Bedrock

# 3. Run — fully offline mode needs no AWS at all:
FAKE_EMBEDDINGS=1 FAKE_LLM=1 uvicorn dejaops.api:app --reload
# or with Bedrock (model access enabled, AWS creds configured):
uvicorn dejaops.api:app --reload
```

Open http://localhost:8000 — fire a simulated alert (e.g. *"checkout timing out, connection pool errors"* on `payment-gateway`) and watch the memory panel: the copilot recalls the matching past incident with similarity scores, walks the runbook, and executes remediations through the exactly-once ledger.

## Deploy to AWS

```bash
# one-time: store secrets
aws ssm put-parameter --name /dejaops/database-url --type SecureString --value "$DATABASE_URL"
aws ssm put-parameter --name /dejaops/demo-token   --type SecureString --value 'choose-a-token'

./scripts/deploy.sh    # ECR + Lambda + Function URL; prints the public URL
```

Cost posture: no VPC/NAT Gateway, no always-on compute, Haiku-class inference. Idle cost ≈ $0; the whole hackathon runs for under $20.

## Tests

```bash
pytest tests/test_units.py                                   # no DB, no AWS
DATABASE_URL=... FAKE_EMBEDDINGS=1 pytest -v                 # + integration (real CRDB)
```

The integration suite proves the load-bearing claims against a real cluster: ranked vector recall, exactly-once ledger under retry, working-memory upsert, and knowledge replay.

## Repo map

- `db/schema.sql` — the memory layer (read this first; `MEMORY_DESIGN.md` explains the why)
- `src/dejaops/memory.py` — recall / write / consolidate / replay
- `src/dejaops/ledger.py` — exactly-once action execution
- `src/dejaops/agent.py` — the tool loop (every memory op is traced to the UI)
- `docs/DEMO.md` — demo script · `docs/TOOLS.md` — tool-usage evidence

## Disclosure

Built during the submission period for the CockroachDB × AWS "Build with Agentic Memory" hackathon. AI coding assistants (Claude Code) were used throughout development, as permitted by the rules. License: MIT.
