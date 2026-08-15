"""
Triagent -- local HTTP API / webhook receiver.

This is the "no human in the loop" interface. Instead of an engineer noticing an
error and pasting it somewhere, your alerting stack (Sentry, Grafana
Alertmanager, Datadog, a log shipper, a cron job...) POSTs the log here and gets
a triage report back as JSON.

Runs on localhost only by default -- no API keys, no cloud, nothing exposed.

Run:
    uvicorn api.main:app --reload --port 8000

Then open http://localhost:8000/docs for an interactive, self-documenting UI.
"""

import time
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from triage_agent.config import OLLAMA_CHAT_MODEL, OLLAMA_EMBED_MODEL
from triage_agent.triage import triage_log
from triage_agent.vectorstore import count as kb_count

app = FastAPI(
    title="Triagent",
    description=(
        "Send a raw error log, get back severity, likely root cause, and similar past "
        "incidents with their resolutions. Runs entirely on local Ollama."
    ),
    version="1.0.0",
)


class TriageRequest(BaseModel):
    raw_log: str = Field(..., description="The raw log text or stack trace to triage.")
    top_k: int = Field(3, ge=1, le=10, description="How many similar past incidents to return.")
    persist: bool = Field(True, description="Save this incident into the knowledge base.")


class GenericWebhook(BaseModel):
    """Accepts an arbitrary JSON body from an alerting tool.

    Different tools nest the log text in different places, so rather than binding
    to one vendor's schema we look through the common ones and fall back to
    stringifying the whole payload.
    """

    model_config = {"extra": "allow"}


# Where each alerting tool tends to put the human-readable error text. Checked in
# order; first non-empty string wins.
_LOG_TEXT_PATHS = [
    ("message",),
    ("log",),
    ("raw_log",),
    ("text",),
    ("body",),
    ("event", "message"),          # Sentry-ish
    ("alerts", 0, "annotations", "description"),  # Alertmanager
    ("alerts", 0, "annotations", "summary"),
    ("commonAnnotations", "description"),
]


def _dig(payload: Any, path: tuple) -> Optional[str]:
    node = payload
    for key in path:
        if isinstance(key, int):
            if not isinstance(node, list) or len(node) <= key:
                return None
            node = node[key]
        else:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
    return node if isinstance(node, str) and node.strip() else None


def extract_log_text(payload: dict) -> str:
    for path in _LOG_TEXT_PATHS:
        found = _dig(payload, path)
        if found:
            return found
    # Nothing matched a known shape -- triage the whole payload rather than
    # rejecting it, since an unknown-but-log-shaped body is still useful input.
    return str(payload)


@app.get("/", include_in_schema=False)
def root():
    """Send anyone who opens the bare host straight to the interactive docs.

    Without this, visiting http://localhost:8000 returns a bare
    {"detail":"Not Found"} -- technically correct (no route is defined at /) but
    it reads like the server is broken."""
    return RedirectResponse(url="/docs")


@app.get("/health", summary="Liveness + config check")
def health():
    try:
        incidents = kb_count()
        kb_ok = True
    except Exception:
        incidents, kb_ok = 0, False
    return {
        "status": "ok",
        "chat_model": OLLAMA_CHAT_MODEL,
        "embed_model": OLLAMA_EMBED_MODEL,
        "knowledge_base_ok": kb_ok,
        "incidents_indexed": incidents,
    }


@app.post("/triage", summary="Triage a log and return the full report")
def triage(req: TriageRequest):
    if not req.raw_log.strip():
        raise HTTPException(status_code=400, detail="raw_log is empty.")

    started = time.perf_counter()
    try:
        report = triage_log(req.raw_log, top_k=req.top_k, persist=req.persist)
    except Exception as exc:
        # Almost always "Ollama isn't running" -- 503 tells the caller to retry.
        raise HTTPException(status_code=503, detail=f"Triage failed: {exc}") from exc

    payload = report.to_dict()
    payload["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    return payload


@app.post("/webhook", status_code=202, summary="Fire-and-forget endpoint for alerting tools")
def webhook(payload: GenericWebhook, background: BackgroundTasks):
    """Accepts any JSON alert payload and triages it in the background.

    Returns 202 immediately: a triage takes minutes on CPU, and most alerting
    tools time out (and retry, causing duplicates) if you make them wait.
    """
    body = payload.model_dump()
    raw_log = extract_log_text(body)
    if not raw_log.strip():
        raise HTTPException(status_code=400, detail="Could not find any log text in the payload.")

    background.add_task(_triage_in_background, raw_log)
    return {
        "status": "accepted",
        "detail": "Triage started in the background; the incident will be added to the knowledge base.",
        "log_preview": raw_log[:200],
    }


def _triage_in_background(raw_log: str) -> None:
    try:
        report = triage_log(raw_log, persist=True)
        print(
            f"[webhook] {report.log_id} severity={report.severity.severity} "
            f"layer={report.root_cause.layer if report.root_cause else 'unknown'}"
        )
    except Exception as exc:
        print(f"[webhook] triage failed: {exc}")
