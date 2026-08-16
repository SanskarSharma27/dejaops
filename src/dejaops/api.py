"""FastAPI app — served locally via uvicorn, on AWS via Lambda + Mangum.

Public-URL hardening for the demo: optional X-Demo-Token gate and a small
per-IP rate limit (in-memory — one warm Lambda container per concurrent
request, so this bounds abuse without extra infrastructure).
"""

import logging
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import agent, db, memory
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("dejaops.api")

app = FastAPI(title="DejaOps", version="0.1.0")

_WEB_DIR = Path(__file__).resolve().parents[2] / "web"
_hits: dict[str, deque] = defaultdict(deque)

_PUBLIC_PATHS = {"/", "/healthz", "/favicon.ico"}


@app.middleware("http")
async def guard(request: Request, call_next):
    cfg = settings()
    path = request.url.path

    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = _hits[ip]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= cfg.rate_limit_per_minute:
        return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
    window.append(now)

    if cfg.demo_token and path.startswith("/api/"):
        supplied = request.headers.get("x-demo-token") or request.query_params.get("token", "")
        if supplied != cfg.demo_token:
            return JSONResponse({"detail": "missing or invalid demo token"}, status_code=401)

    return await call_next(request)


# --- models -------------------------------------------------------------------


class AlertIn(BaseModel):
    service: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    severity: str = Field(default="SEV3", pattern="^SEV[1-4]$")
    description: str = Field(min_length=1, max_length=5000)
    external_key: str | None = Field(default=None, max_length=200)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


# --- routes -------------------------------------------------------------------


@app.get("/healthz")
def healthz():
    try:
        db.query_one("SELECT 1 AS ok")
        return {"ok": True, "db": "up"}
    except Exception as exc:
        return JSONResponse({"ok": False, "db": str(exc)}, status_code=503)


@app.get("/")
def index():
    return FileResponse(_WEB_DIR / "index.html")


@app.post("/api/alerts")
def ingest_alert(alert: AlertIn):
    """Simulated monitoring webhook: creates (or dedupes) an incident and runs
    the agent's first turn against it."""
    existing = None
    if alert.external_key:
        existing = db.query_one(
            "SELECT * FROM incidents WHERE external_key = %s", (alert.external_key,)
        )
    if existing:
        incident = existing
    else:
        incident = db.query_one(
            """
            INSERT INTO incidents (external_key, service, title, severity, summary)
            VALUES (%s, %s, %s, %s, %s) RETURNING *
            """,
            (alert.external_key, alert.service, alert.title, alert.severity, alert.description),
        )
        memory.remember_event(str(incident["id"]), "alert", f"{alert.title} — {alert.description}")

    result = agent.run_turn(incident, f"New alert received: {alert.description}. Triage this.")
    return {"incident_id": str(incident["id"]), **result}


@app.post("/api/incidents/{incident_id}/chat")
def chat(incident_id: str, body: ChatIn):
    incident = db.query_one("SELECT * FROM incidents WHERE id = %s", (incident_id,))
    if not incident:
        raise HTTPException(404, "incident not found")
    return agent.run_turn(incident, body.message)


@app.get("/api/incidents")
def list_incidents():
    rows = db.query(
        "SELECT id, service, title, severity, status, opened_at, resolved_at FROM incidents ORDER BY opened_at DESC LIMIT 100"
    )
    return [_ser(r) for r in rows]


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    incident = db.query_one("SELECT * FROM incidents WHERE id = %s", (incident_id,))
    if not incident:
        raise HTTPException(404, "incident not found")
    events = db.query(
        "SELECT ts, kind, body FROM incident_events WHERE incident_id = %s ORDER BY ts", (incident_id,)
    )
    ledger_rows = db.query(
        "SELECT action, args, status, result, applied_at, idempotency_key FROM action_ledger WHERE incident_id = %s ORDER BY applied_at",
        (incident_id,),
    )
    return {
        "incident": _ser(incident),
        "events": [_ser(e) for e in events],
        "working_memory": memory.get_working_memory(incident_id),
        "ledger": [_ser(r) for r in ledger_rows],
    }


@app.get("/api/incidents/{incident_id}/replay")
def replay(incident_id: str, at: str):
    try:
        return memory.replay(incident_id, at)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/memory/search")
def memory_search(q: str, tier: str = "episodic", k: int = 5):
    if tier not in ("episodic", "semantic"):
        raise HTTPException(422, "tier must be episodic or semantic")
    return memory.recall(q, tier=tier, k=min(k, 20))


@app.get("/api/ledger")
def full_ledger():
    rows = db.query(
        "SELECT incident_id, action, args, status, result, applied_at, idempotency_key FROM action_ledger ORDER BY applied_at DESC LIMIT 100"
    )
    return [_ser(r) for r in rows]


def _ser(row: dict) -> dict:
    return {k: (str(v) if not isinstance(v, (int, float, bool, dict, list, type(None))) else v) for k, v in row.items()}


# Lambda entrypoint (no-op locally)
try:
    from mangum import Mangum

    handler = Mangum(app)
except ImportError:  # pragma: no cover
    handler = None
