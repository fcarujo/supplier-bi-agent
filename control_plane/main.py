"""
Supplier BI Agent — Control Plane API + Static Server
=======================================================
Serves the React frontend and the FastAPI backend from
a single Cloud Run container.

React app is served at /
API endpoints are served at /api/*
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google.cloud import bigquery
from pydantic import BaseModel

app = FastAPI(title="Supplier BI Agent Control Plane")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GCP_PROJECT = os.environ.get("GCP_PROJECT", "supplier-bi-agent-2025")
BQ_DATASET  = os.environ.get("BQ_DATASET",  "supplier_bi")


def bq():
    return bigquery.Client(project=GCP_PROJECT)


# ── Models ────────────────────────────────────────────────────────────────────

class DecisionRequest(BaseModel):
    runID:           str
    decision:        str
    reviewer:        str
    reason:          Optional[str] = None
    editedNarrative: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_json(val):
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return val


def _row_to_dict(row):
    d = dict(row)
    for key in ["flags", "errors", "selectedTables", "queries",
                "rowCounts", "pullValidation", "reportJSON",
                "validationSummary", "policyOutcome"]:
        if key in d:
            d[key] = _safe_json(d[key])
    # Serialise timestamps
    for key in ["startedAt", "completedAt", "decidedAt", "queuedAt",
                "validatedAt", "approvedAt", "onboardingDate"]:
        if key in d and d[key] and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    return d


def _get_run_field(client, run_id: str, field: str):
    rows = list(client.query(
        f"SELECT {field} FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs` WHERE runID = '{run_id}' LIMIT 1"
    ).result())
    return dict(rows[0])[field] if rows else None


def _get_pending_field(client, run_id: str, field: str):
    rows = list(client.query(
        f"SELECT {field} FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports` WHERE runID = '{run_id}' LIMIT 1"
    ).result())
    return dict(rows[0])[field] if rows else None


# ── GET /health ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "project": GCP_PROJECT, "dataset": BQ_DATASET}


# ── GET /api/queue ────────────────────────────────────────────────────────────

@app.get("/api/queue")
def get_queue():
    client = bq()

    rows = list(client.query(f"""
        SELECT
            p.runID, p.reportType, p.audience, p.supplierID,
            p.queuedAt, p.status, p.confidence, p.policyDecision,
            p.validationPassed, p.validationFailed, p.hallucinationFlags,
            p.reportNarrative, p.errors
        FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports` p
        WHERE p.status = 'pending'
        ORDER BY p.queuedAt DESC
        LIMIT 50
    """).result())

    results = []
    for row in rows:
        d = _row_to_dict(row)

        # Fetch flags from agent_runs
        run_rows = list(client.query(f"""
            SELECT flags, errors
            FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
            WHERE runID = '{d["runID"]}' LIMIT 1
        """).result())
        if run_rows:
            run_data = _row_to_dict(run_rows[0])
            d["flags"] = _safe_json(run_data.get("flags")) or []

        # Derive soft failures
        errors = d.get("errors") or []
        if isinstance(errors, str):
            try:
                errors = json.loads(errors)
            except Exception:
                errors = [errors]
        d["softFailures"] = [e for e in errors if isinstance(e, str)]
        d["hardFailures"]  = []

        results.append(d)

    return {"queue": results, "total": len(results)}


# ── GET /api/runs/{run_id} ────────────────────────────────────────────────────

@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    client = bq()

    run_rows = list(client.query(f"""
        SELECT * FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
        WHERE runID = '{run_id}' LIMIT 1
    """).result())
    if not run_rows:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    run = _row_to_dict(run_rows[0])

    # Enrich with pending report data
    pending_rows = list(client.query(f"""
        SELECT reportNarrative, reportJSON, policyDecision,
               validationPassed, validationFailed, hallucinationFlags
        FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
        WHERE runID = '{run_id}' LIMIT 1
    """).result())
    if pending_rows:
        p = _row_to_dict(pending_rows[0])
        run.update({
            "reportNarrative":   p.get("reportNarrative"),
            "reportJSON":        _safe_json(p.get("reportJSON")),
            "validationPassed":  p.get("validationPassed", 0),
            "validationFailed":  p.get("validationFailed", 0),
            "hallucinationFlags":p.get("hallucinationFlags", 0),
        })

    # Validation results
    val_rows = list(client.query(f"""
        SELECT validationID, metricName, expectedValue, reportedValue,
               deviationPct, passed, hallucinationFlag, details
        FROM `{GCP_PROJECT}.{BQ_DATASET}.validation_results`
        WHERE runID = '{run_id}'
        ORDER BY validatedAt DESC
    """).result())
    run["validationResults"] = [_row_to_dict(r) for r in val_rows]

    # Human decision if exists
    dec_rows = list(client.query(f"""
        SELECT decision, reviewer, reason, decidedAt, editedNarrative
        FROM `{GCP_PROJECT}.{BQ_DATASET}.human_decisions`
        WHERE runID = '{run_id}'
        ORDER BY decidedAt DESC LIMIT 1
    """).result())
    if dec_rows:
        run["humanDecision"] = _row_to_dict(dec_rows[0])

    return run


# ── POST /api/decisions ───────────────────────────────────────────────────────

@app.post("/api/decisions")
def post_decision(body: DecisionRequest):
    client      = bq()
    decision_id = str(uuid.uuid4())
    now         = datetime.now(timezone.utc).isoformat()

    if body.decision not in ("approved", "rejected", "edited_and_approved"):
        raise HTTPException(status_code=400, detail="Invalid decision value")
    if body.decision == "rejected" and not body.reason:
        raise HTTPException(status_code=400, detail="Rejection reason required")

    errors = client.insert_rows_json(
        f"{GCP_PROJECT}.{BQ_DATASET}.human_decisions",
        [{
            "decisionID":        decision_id,
            "runID":             body.runID,
            "reportType":        _get_run_field(client, body.runID, "reportType"),
            "audience":          _get_run_field(client, body.runID, "audience"),
            "supplierID":        _get_run_field(client, body.runID, "supplierID"),
            "decidedAt":         now,
            "decision":          body.decision,
            "reviewer":          body.reviewer,
            "reason":            body.reason,
            "editedNarrative":   body.editedNarrative,
            "originalNarrative": _get_pending_field(client, body.runID, "reportNarrative"),
            "retryTriggered":    False,
            "retryRunID":        None,
        }]
    )
    if errors:
        raise HTTPException(status_code=500, detail=f"Failed to write decision: {errors}")

    # Update statuses
    client.query(f"""
        UPDATE `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
        SET status = 'decided' WHERE runID = '{body.runID}'
    """).result()

    agent_status = "approved" if body.decision in ("approved", "edited_and_approved") else "rejected"
    client.query(f"""
        UPDATE `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
        SET status = '{agent_status}' WHERE runID = '{body.runID}'
    """).result()

    return {"decisionID": decision_id, "runID": body.runID, "decision": body.decision, "decidedAt": now}


# ── GET /api/history ──────────────────────────────────────────────────────────

@app.get("/api/history")
def get_history(limit: int = 50):
    client = bq()

    rows = list(client.query(f"""
        SELECT
            a.runID, a.reportType, a.audience, a.supplierID,
            a.status, a.confidence, a.startedAt, a.policyDecision,
            a.reportDate, d.decision, d.reviewer, d.decidedAt, d.reason
        FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs` a
        LEFT JOIN `{GCP_PROJECT}.{BQ_DATASET}.human_decisions` d ON a.runID = d.runID
        WHERE a.status IN ('approved','rejected','pending_review','escalated','pending_publish')
        ORDER BY a.startedAt DESC
        LIMIT {limit}
    """).result())

    return {"history": [_row_to_dict(r) for r in rows], "total": len(rows)}


# ── Serve React frontend ──────────────────────────────────────────────────────
# Must come AFTER all API routes

STATIC_DIR = Path(__file__).parent / "static"

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Catch-all — serve React app for all non-API routes."""
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"error": "Frontend not built"}
