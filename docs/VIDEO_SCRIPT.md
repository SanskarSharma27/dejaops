# Video script (~2:45 spoken, target under 3:00)

## 0:00–0:15 — Hook (title card → app)
"It's 3 AM. Checkout is down. This exact failure happened eight months ago — but the
engineer who fixed it left the company. That knowledge is gone. DejaOps is an on-call
copilot that never forgets — because its entire memory is CockroachDB."

## 0:15–0:40 — Fire the alert (fill form: payment-gateway / SEV2 / demo symptoms; click Fire)
"A monitoring alert comes in: checkout latency spiking, connection pool errors after a
deploy. The agent — Claude on Amazon Bedrock — doesn't guess. It asks its memory first."

## 0:40–1:10 — Déjà vu (linger on top recall hit + similarity bar)
"Watch the memory panel. That's a live vector search over CockroachDB's distributed
vector index — and it found this exact failure from three months ago: an N+1 query that
exhausted the connection pool after a deploy. Root cause and fix included, plus the
runbook the team wrote afterward. These vectors live inside the transactional database,
right next to the incident records — no separate vector store, no sync pipeline,
searchable the instant a transaction commits."

## 1:10–1:25 — Working memory (green trace item, TTL badge)
"The agent records its hypothesis in working memory — which expires on its own via
CockroachDB's row-level TTL. Scratchpads decay; knowledge persists."

## 1:25–1:55 — Exactly-once (chat: "the rollback timed out, run the rollback again")
"Now the important part. The agent rolled back the deploy — and I'm telling it the
command timed out, try again. Watch: duplicate suppressed. The action and its memory
record committed in a single CockroachDB transaction with a unique idempotency key. A
retried tool call cannot fire the side effect twice. That's the unsolved problem with
production agents — retries that double-charge, double-restart, double-page — solved by
making the memory layer a serializable database instead of a bolt-on vector store."

## 1:55–2:15 — Resolve + time travel (confirm fix; CONSOLIDATED; replay mid-incident ts)
"On resolve, the incident is consolidated: embedded into episodic memory, and distilled
by Claude into a new runbook. And for the postmortem — AS OF SYSTEM TIME. This replays
exactly what the agent knew at any moment during the incident. Not what it knows now —
what it knew then."

## 2:15–2:35 — Architecture + tools (README diagram)
"The stack: AWS Lambda behind API Gateway, Claude and Titan embeddings on Amazon
Bedrock, secrets in SSM — no servers, near-zero idle cost. On the CockroachDB side:
distributed vector indexing at the core, plus the managed MCP server, the agent skills
repo, and the ccloud CLI throughout development."

## 2:35–2:50 — Close (incident list)
"Institutional memory that survives team churn. Recall you can trust, actions that
happen exactly once, and a memory you can rewind. DejaOps — the on-call copilot that
never forgets."

## Recording notes
- 75% app/memory panel, 15% diagram, 10% cards. Rules require showing "the CockroachDB
  memory layer at work" — the memory panel is the evidence; keep it on screen.
- Optional: split-screen SQL shell during recall (ORDER BY embedding <-> ... + EXPLAIN
  showing the vector search node).
- Record AFTER flipping to real inference. Pre-warm the page. 1080p, hide bookmarks.
- Running long? Cut the architecture beat first — ledger and replay earn the scores.
- Use the exact alert text from docs/DEMO.md so recall hits the seeded incident.
