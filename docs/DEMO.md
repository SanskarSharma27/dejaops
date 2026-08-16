# Demo & video plan

## What must be presented (judge-facing artifacts)

1. **Live demo URL** (Lambda Function URL) — up through Sept 15; share as `https://...?token=<DEMO_TOKEN>` in the Devpost "testing instructions" field.
2. **Video < 3:00** — must show "the CockroachDB memory layer at work" (rule text). The memory panel exists precisely for this.
3. **Public repo** — MIT, README with tool table + architecture, `MEMORY_DESIGN.md`.
4. **Devpost text** — problem, what it does, how CRDB is the memory layer, tools used, what's next.
5. **Architecture diagram** — export the README ASCII diagram as an image for Devpost.

## The scripted demo scenario (rehearse exactly this)

Pre-state: seeded history (12 incidents, 5 runbooks) already in CockroachDB.

1. **Fire the alert** — service `payment-gateway`, title "Checkout latency spike", SEV2, description:
   *"p99 on /charge jumped to 9s, timeouts on ~25% of checkouts, started right after the 15:40 deploy, connection pool errors in logs."*
2. **Recall moment** — memory panel shows RECALL hits: seed incident *"Checkout timeouts spiking to 30%"* at high similarity, with its root cause (N+1 query exhausted the pool after a deploy) and the checkout-latency runbook. **This is the déjà vu beat — linger on it.**
3. **Agent proposes rollback citing memory**, records hypothesis to working memory (TTL badge visible).
4. **Exactly-once beat** — tell the copilot: *"the rollback command timed out, try the rollback again"*. The ledger returns **DUPLICATE SUPPRESSED — side effect not re-executed** in red. Explain: one CockroachDB transaction, unique idempotency key.
5. **Resolve** — operator confirms fix; agent calls `resolve_incident`; consolidation creates an episode + a new runbook (CONSOLIDATED trace item).
6. **Time travel** — replay panel: pick a timestamp between the alert and the resolution; show the event set the agent knew *then* (source: `as_of_system_time`).

## Video storyboard (2:45 target)

| t | beat | on screen |
|---|---|---|
| 0:00–0:15 | Problem: 3am page, the person who saw this failure last time left the company | title card → app |
| 0:15–0:45 | Fire alert → agent triages | app UI |
| 0:45–2:00 | **The memory layer at work**: recall w/ similarity scores → working memory TTL → duplicate-suppressed ledger retry → consolidation → AS OF SYSTEM TIME replay. Split-screen with a SQL shell (`SELECT ... ORDER BY embedding <-> ...`, the ledger row, `AS OF SYSTEM TIME`) so the DB itself is visibly doing the work | app + SQL side-by-side |
| 2:00–2:20 | Architecture: one database — vectors transactional with agent state; Bedrock + Lambda | diagram |
| 2:20–2:45 | Impact: institutional memory that survives team churn; exactly-once ops | talking head or card |

Recording notes: 1080p+, hide bookmarks bar, pre-warm the Lambda (one request before recording), rehearse the duplicate-retry line — it's the originality moment.

## Submission checklist

- [ ] Demo URL live and token shared in testing instructions
- [ ] Video uploaded (YouTube unlisted is fine), < 3:00 confirmed
- [ ] Repo public, LICENSE present, README tool table accurate
- [ ] `docs/TOOLS.md` TODOs resolved (MCP screenshots, ccloud transcript)
- [ ] Architecture diagram image uploaded to Devpost
- [ ] AI-assistance disclosure included (README has it)
- [ ] Submit by **Aug 18, 14:00 ET** (3h buffer before the 17:00 deadline)
- [ ] Do NOT tear down infra — judging runs through Sept 15
