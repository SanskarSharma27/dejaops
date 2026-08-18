# CockroachDB & AWS tool usage — evidence

Stage-one judging is pass/fail on required tools. This page maps each claimed tool to where it is used and how to verify.

## CockroachDB tools (2 required — 3 used)

### 1. Distributed Vector Indexing — load-bearing, runtime
- Schema: `db/schema.sql` → `VECTOR(1024)` column + `CREATE VECTOR INDEX ... (tier, embedding)`
- Query path: `src/dejaops/memory.py::recall` — `ORDER BY embedding <-> $query` with tier prefix pushdown
- Verified by: `tests/test_db_integration.py::test_vector_recall_ranks_by_similarity`
- Design notes (L2-only acceleration, normalization, empty-table index creation): `MEMORY_DESIGN.md`

### 2. Managed MCP Server — not used

Listed here for completeness rather than claimed: the Managed MCP Server was **not** configured
for this project, so it is not counted among the tools above. The three tools documented on this
page are each backed by artifacts in this repository or reproducible command output.

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
- Captured evidence (2026-08-18):
  ```
  $ ccloud auth whoami
  logged in to "CloudAngles" (org-3bnjr) as Sanskar Sharma

  $ ccloud cluster list
  NAME     ID                                    PLAN TYPE   STATE    CLOUD  VERSION
  dejaops  9e93e905-12fa-4069-9ec8-fb59ea41ce2d  SERVERLESS  CREATED  AWS    v26.2.5
  ```

## AWS services (1 required — 5 used)

| Service | Use | Where |
|---|---|---|
| **Amazon Bedrock** | Claude Haiku 4.5 (agent loop, consolidation) via `AnthropicBedrock`; Titan Text Embeddings V2 via boto3. Integrated and verified; the recorded demo used the direct-API fallback while this account's model allowlisting was pending — see README. | `src/dejaops/llm.py`, `src/dejaops/embeddings.py` |
| **AWS Lambda** | Serverless compute; runs the whole app as a container image | `Dockerfile`, `scripts/deploy.sh` |
| **Amazon API Gateway** | HTTP API fronting Lambda — the public demo URL | `scripts/deploy.sh` |
| **SSM Parameter Store** | Secrets (DB URL, demo token) — free tier, no Secrets Manager cost | `scripts/deploy.sh` |
| **Amazon ECR** | Image registry | `scripts/deploy.sh` |
| **CloudWatch Logs** | Structured request/tool logging | automatic via Lambda |
