# Devpost submission text (paste-ready)

## Project name
DejaOps — the on-call copilot that never forgets

## Elevator pitch (tagline)
An incident-response copilot whose entire memory is CockroachDB: vector recall of past
incidents, TTL-decaying working memory, exactly-once remediation via a transactional
action ledger, and time-travel replay of what the agent knew.

## Inspiration
Every on-call engineer knows the 3am feeling: *this has happened before, but the person
who fixed it left the company.* Institutional memory lives in people, and people churn.
We built an agent whose memory outlives the team — and discovered that the hard part of
agent memory isn't recall, it's trust: retries that double-fire actions, postmortems that
can't reconstruct what the agent knew, scratchpads that hoard forever.

## What it does
DejaOps triages incoming alerts against everything it has ever seen. It recalls similar
past incidents (vector search over episodic memory) and distilled runbooks (semantic
memory), records findings in a working-memory scratchpad that expires on its own
(row-level TTL), and executes remediations through an exactly-once action ledger — the
side effect and its memory record commit in ONE CockroachDB transaction, so a retried
tool call is detected by idempotency key and never re-executed. When an incident is
resolved, the episode is consolidated: embedded into episodic memory and distilled by
Claude into a new runbook. And for postmortems, AS OF SYSTEM TIME replays exactly what
the agent knew at any moment — backed by an append-only version table beyond the GC window.

The UI shows the memory layer working live: recalled chunks with similarity scores,
TTL badges, and a red "DUPLICATE SUPPRESSED" when the ledger blocks a retried action.

## How we built it
- **CockroachDB Cloud (Basic, AWS ap-south-1)** — the entire memory layer: VECTOR(1024)
  columns with a distributed vector index (tier as prefix column for filter pushdown),
  row-level TTL on working memory, a UNIQUE-key action ledger, and an append-only
  memory_versions table written in the same transaction as every mutation.
- **Amazon Bedrock** — Claude Haiku 4.5 (global cross-region inference) drives the agent
  loop and runbook distillation; Titan Text Embeddings V2 (1024-dim, L2-normalized so
  CockroachDB's L2-accelerated index gives cosine semantics).
- **AWS Lambda** (container image + Function URL), **SSM Parameter Store** for secrets,
  **ECR**, **CloudWatch** — zero always-on cost, no VPC/NAT.
- Python / FastAPI / psycopg3, with serialization-retry for CockroachDB's SERIALIZABLE
  isolation — whose retries are exactly what the idempotency ledger exists to absorb.

## Challenges we ran into
- Only L2 distance is index-accelerated in CockroachDB — solved by storing unit-normalized
  embeddings (L2 ranking ≡ cosine ranking on unit vectors).
- AS OF SYSTEM TIME can't reach past the GC window — solved honestly with an append-only
  version table written transactionally with every memory mutation, and a replay API that
  labels which mechanism answered.
- Vector indexes must be created before data lands (index creation blocks writes on
  populated tables; IMPORT INTO is unsupported) — schema-first migrations.
- New AWS account Bedrock allowlisting mid-hackathon — worked around with a clean
  provider abstraction and offline modes (deterministic embeddings + canned LLM) that
  keep the entire stack testable without AWS.

## Accomplishments we're proud of
The exactly-once ledger. "An agent that retries a tool call must not perform the side
effect twice" is an unsolved pain in production agents — and it falls out naturally when
your memory layer is a serializable SQL database instead of a bolt-on vector store.
Integration tests prove it against a real cluster: one ledger row, one side effect, one
memory write, retry suppressed.

## What we learned
Agent memory is a transactional-database problem wearing a vector-search costume. Vectors
next to your system of record mean sync pipelines and lag; vectors *inside* it mean a
chunk is searchable the instant its transaction commits, and every memory claim is
auditable in time.

## What's next
- REGIONAL BY ROW: pin EU incidents' memories to EU nodes (schema is one ALTER away)
- Changefeed-driven background consolidation instead of synchronous distillation
- Real integrations (PagerDuty ingest, Kubernetes remediation executors)

## Built with
cockroachdb, aws-lambda, amazon-bedrock, claude, titan-embeddings, python, fastapi,
psycopg3, mangum, docker

## CockroachDB tools used
Distributed Vector Indexing (runtime core), Managed MCP Server (dev workflow),
Agent Skills Repo (committed in-repo), ccloud CLI — evidence: docs/TOOLS.md

## AWS services used
Amazon Bedrock, AWS Lambda, SSM Parameter Store, Amazon ECR, CloudWatch

## Try it (testing instructions field)
Demo URL: <FUNCTION_URL>?token=<DEMO_TOKEN>
Fire a simulated alert on service `payment-gateway` with symptoms like "checkout timing
out, connection pool errors after deploy" and watch the memory panel recall the matching
past incident. Then ask the copilot to retry a remediation it already ran — the ledger
suppresses the duplicate. Pick a mid-incident timestamp in the Time travel panel to see
what the agent knew then.
