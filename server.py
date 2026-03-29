"""
FastAPI server — health checks + on-demand scan triggers.

This is the entrypoint when deployed on Railway. It serves two purposes:

1. HEALTH CHECK: GET /health — Railway pings this to know the service is alive.
   Returns the last scan time and current status.

2. ON-DEMAND TRIGGER: POST /run — Kicks off the full pipeline in a background
   thread and returns immediately. Railway's cron job hits this endpoint on
   schedule (Monday 6am Pacific), and you can also hit it manually.

WHY FASTAPI + BACKGROUND THREAD:
   Railway needs a process that stays alive (a web server). The scan takes
   ~7 minutes, which would timeout an HTTP request. So we start the scan
   in a background thread, return a 202 "accepted" immediately, and let
   the scan run. GET /health or GET /status shows whether it's still running.
"""

import argparse
import logging
import threading
from datetime import datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="NWTN Competitive Scanner")
logger = logging.getLogger("server")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ─── Scan State ──────────────────────────────────────────────────────────────
# Tracks whether a scan is currently running, and when the last one completed.
# Shared across requests via module-level state (single process, safe here).

class ScanState:
    def __init__(self):
        self.is_running: bool = False
        self.last_scan_at: datetime | None = None
        self.last_result: str = ""
        self.last_error: str = ""

state = ScanState()


# ─── Response Models ─────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    scan_running: bool
    last_scan_at: str | None
    last_result: str

class RunResponse(BaseModel):
    message: str
    status: str


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health_check():
    """Health check endpoint — Railway pings this to verify the service is alive."""
    return HealthResponse(
        status="healthy",
        scan_running=state.is_running,
        last_scan_at=state.last_scan_at.isoformat() if state.last_scan_at else None,
        last_result=state.last_result or "no scans yet",
    )


@app.post("/run", response_model=RunResponse, status_code=202)
def trigger_scan():
    """Trigger a full pipeline scan. Returns immediately, scan runs in background."""
    if state.is_running:
        raise HTTPException(status_code=409, detail="Scan already in progress")

    thread = threading.Thread(target=_run_scan_background, daemon=True)
    thread.start()

    return RunResponse(
        message="Scan started",
        status="accepted",
    )


@app.get("/status", response_model=HealthResponse)
def scan_status():
    """Check the current scan status and last result."""
    return HealthResponse(
        status="running" if state.is_running else "idle",
        scan_running=state.is_running,
        last_scan_at=state.last_scan_at.isoformat() if state.last_scan_at else None,
        last_result=state.last_result or "no scans yet",
    )


# ─── Background Scan ────────────────────────────────────────────────────────

def _run_scan_background() -> None:
    """Run the full scan pipeline in a background thread."""
    state.is_running = True
    state.last_error = ""
    logger.info("Background scan started")

    try:
        from main import run_scan
        args = argparse.Namespace(dry_run=False, query=[], report_only=False)
        run_scan(args)
        state.last_result = "success"
        state.last_scan_at = datetime.now()
        logger.info("Background scan completed successfully")

    except Exception as e:
        state.last_result = f"failed: {e}"
        state.last_error = str(e)
        logger.error(f"Background scan failed: {e}", exc_info=True)

    finally:
        state.is_running = False
