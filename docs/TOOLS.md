# CockroachDB & AWS tool usage — evidence

Stage-one judging is pass/fail on required tools. This page maps each claimed tool to where it is used and how to verify.

## CockroachDB tools (2 required — 4 used)

### 1. Distributed Vector Indexing — load-bearing, runtime
- Schema: `db/schema.sql` → `VECTOR(1024)` column + `CREATE VECTOR INDEX ... (tier, embedding)`
- Query path: `src/dejaops/memory.py::recall` — `ORDER BY embedding <-> $query` with tier prefix pushdown
- Verified by: `tests/test_db_integration.py::test_vector_recall_ranks_by_similarity`
- Design notes (L2-only acceleration, normalization, empty-table index creation): `MEMORY_DESIGN.md`

### 2. Managed MCP Server — development & inspection workflow
- Connected from the Cloud Console to Claude Code during development; used for schema inspection,
  `EXPLAIN` on the recall query, and read-only verification queries against the live memory layer.
- Evidence to capture before submission (screenshots in `docs/img/`):
  - Cloud Console MCP configuration page
  - A Claude Code session inspecting `memory_chunks` / query plan via MCP
- [ ] TODO: add screenshots once the cluster is up

### 3. Agent Skills Repo — development workflow
- Installed: `npx skills add cockroachlabs/cockroachdb-skills` — the full skills set is
  committed in this repo under `.agents/skills/` (30+ skills, symlinked into Claude Code
  via `.claude/`), covering vector indexing, TTL, query performance, and security.
- Used during schema design and query analysis in this project's development sessions.

### 4. ccloud CLI — cluster lifecycle
- Installed: `ccloud 0.6.12` (`curl .../ccloud_linux-amd64_0.6.12.tar.gz | tar -xz`)
- Used for cluster inspection and SQL access during development:
  ```bash
  ccloud auth login
  ccloud cluster list
  ccloud cluster info dejaops           # region/plan/version verification
  ccloud cluster sql dejaops            # ad-hoc verification queries
  ```
- [ ] TODO: paste real output snippets after `ccloud auth login`

## AWS services (1 required — 5 used)

| Service | Use | Where |
|---|---|---|
| **Amazon Bedrock** | Claude Haiku 4.5 (agent loop, consolidation) via the Anthropic SDK Bedrock client; Titan Text Embeddings V2 | `src/dejaops/llm.py`, `src/dejaops/embeddings.py` |
| **AWS Lambda** | Serverless compute, container image, public Function URL | `Dockerfile`, `scripts/deploy.sh` |
| **SSM Parameter Store** | Secrets (DB URL, demo token) — free tier, no Secrets Manager cost | `scripts/deploy.sh` |
| **Amazon ECR** | Image registry | `scripts/deploy.sh` |
| **CloudWatch Logs** | Structured request/tool logging | automatic via Lambda |
