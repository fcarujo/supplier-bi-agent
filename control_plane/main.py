"""
Supplier BI Agent — Control Plane API + Static Server
=======================================================
Serves the React frontend and the FastAPI backend from
a single Cloud Run container.

React app is served at /
API endpoints are served at /api/*
Supplier-facing view at /supplier/:id
"""

import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google.cloud import bigquery
from pydantic import BaseModel

# ── Firebase Admin — JWT verification ────────────────────────────────────────
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth

_firebase_app = None

def _get_firebase_app():
    global _firebase_app
    if _firebase_app is None:
        sa_path = Path(__file__).parent.parent / "firebase-service-account.json"
        if sa_path.exists():
            cred = credentials.Certificate(str(sa_path))
            _firebase_app = firebase_admin.initialize_app(cred)
        else:
            # In Cloud Run use Application Default Credentials
            _firebase_app = firebase_admin.initialize_app()
    return _firebase_app

_get_firebase_app()


class AuthUser:
    def __init__(self, uid: str, email: str, role: str, supplier_id: Optional[str] = None):
        self.uid         = uid
        self.email       = email
        self.role        = role          # "admin" | "business" | "supplier"
        self.supplier_id = supplier_id   # set for role=supplier only


def get_current_user(request: Request) -> AuthUser:
    """FastAPI dependency — verifies Firebase JWT and extracts role claims."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header.split("Bearer ", 1)[1].strip()
    try:
        decoded = firebase_auth.verify_id_token(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    role        = decoded.get("role")
    supplier_id = decoded.get("supplierID")

    if not role:
        raise HTTPException(status_code=403, detail="No role assigned to this account")

    return AuthUser(
        uid         = decoded["uid"],
        email       = decoded.get("email", ""),
        role        = role,
        supplier_id = supplier_id,
    )


def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Only admin role — write operations (decisions, queue management)."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_internal(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Admin, business, or demo role — internal dashboard access."""
    if user.role not in ("admin", "business", "demo"):
        raise HTTPException(status_code=403, detail="Internal access required")
    return user


def require_reporter(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Admin or business role — can create reports. Demo cannot."""
    if user.role not in ("admin", "business"):
        raise HTTPException(status_code=403, detail="Reporter access required")
    return user


# ── Agent import — must be at module level so it resolves at container startup
sys.path.insert(0, "/app")
from agent.graph import run_agent as _run_agent

app = FastAPI(title="Supplier BI Agent Control Plane")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GCP_PROJECT = os.environ.get("GCP_PROJECT", "supplier-bi-agent-2025")
BQ_DATASET  = os.environ.get("BQ_DATASET",  "supplier_bi")

# ── Session store — in-memory, ephemeral ──────────────────────────────────────
_sessions: dict = {}
MAX_SESSIONS = 100


def bq():
    return bigquery.Client(project=GCP_PROJECT)


# ── Models ────────────────────────────────────────────────────────────────────

class DecisionRequest(BaseModel):
    runID:             str
    decision:          str
    reviewer:          str
    reason:            Optional[str]  = None
    editedNarrative:   Optional[str]  = None
    shareWithSupplier: Optional[bool] = False
    triggerRerun:      Optional[bool] = True

class RunRequest(BaseModel):
    reportType: str
    reportTitle: Optional[str] = None
    supplierID: Optional[str] = None
    goal:       str
    dateFrom:   Optional[str] = None
    dateTo:     Optional[str] = None

class AskRequest(BaseModel):
    question:   str
    supplierID: Optional[str] = None
    sessionID:  Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_json(val):
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    # BigQuery JSON columns return a special object — convert via str first
    try:
        s = str(val) if not isinstance(val, str) else val
        return json.loads(s)
    except Exception:
        return val


def _row_to_dict(row):
    d = dict(row)
    for key in ["flags", "errors", "selectedTables", "queries",
                "rowCounts", "pullValidation", "reportJSON",
                "validationSummary", "policyOutcome"]:
        if key in d:
            d[key] = _safe_json(d[key])
    for key in ["startedAt", "completedAt", "decidedAt", "queuedAt",
                "validatedAt", "approvedAt", "onboardingDate"]:
        if key in d and d[key] and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    for key in ["reportDate", "orderDate", "weekOf", "generatedAt"]:
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


# ── Date helpers ──────────────────────────────────────────────────────────────

def _build_date_filter(date_from: str = None, date_to: str = None, table_alias: str = None) -> str:
    col = f"{table_alias}.orderDate" if table_alias else "orderDate"
    if date_from and date_to:
        return f"{col} >= '{date_from}' AND {col} <= '{date_to}'"
    return f"""
        {col} >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 7 MONTH)
        AND {col} < DATE_TRUNC(CURRENT_DATE(), MONTH)
    """


# ── GET /health ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "project": GCP_PROJECT, "dataset": BQ_DATASET}


# ── GET /api/queue  (admin only) ──────────────────────────────────────────────

@app.get("/api/queue")
def get_queue(user: AuthUser = Depends(require_internal)):
    client = bq()
    rows = list(client.query(f"""
        SELECT
            p.runID, p.reportType, p.audience, p.supplierID,
            p.queuedAt, p.status, p.confidence, p.policyDecision,
            p.validationPassed, p.validationFailed, p.hallucinationFlags,
            p.reportNarrative, p.errors,
            a.flags, a.goal
        FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports` p
        LEFT JOIN (
            SELECT runID, flags, goal,
                   ROW_NUMBER() OVER (PARTITION BY runID ORDER BY startedAt DESC) as rn
            FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
        ) a ON p.runID = a.runID AND a.rn = 1
        WHERE p.status = 'pending'
          AND (
            p.runID LIKE 'DEMO_%'
            OR p.runID NOT IN (
              SELECT runID FROM `{GCP_PROJECT}.{BQ_DATASET}.human_decisions`
              WHERE decision IN ('approved','rejected','edited_and_approved')
            )
          )
          {"AND p.runID LIKE 'DEMO_%'" if user.role == "demo" else ""}
        ORDER BY p.queuedAt DESC
        LIMIT 50
    """).result())

    results = []
    for row in rows:
        d = _row_to_dict(row)
        d["flags"] = _safe_json(d.get("flags")) or []
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


# ── GET /api/runs/{run_id}  (admin only) ──────────────────────────────────────

@app.get("/api/runs/{run_id}")
def get_run(run_id: str, user: AuthUser = Depends(require_internal)):
    client = bq()
    run_rows = list(client.query(f"""
        SELECT * FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
        WHERE runID = '{run_id}'
        ORDER BY startedAt DESC LIMIT 1
    """).result())
    if not run_rows:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    run = _row_to_dict(run_rows[0])

    pending_rows = list(client.query(f"""
        SELECT reportNarrative, reportJSON, policyDecision,
               validationPassed, validationFailed, hallucinationFlags
        FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
        WHERE runID = '{run_id}'
        ORDER BY queuedAt DESC LIMIT 1
    """).result())
    if pending_rows:
        p = _row_to_dict(pending_rows[0])
        report_json = _safe_json(p.get("reportJSON")) or {}
        policy_outcome = (
            report_json.get("policy_outcome") or
            report_json.get("policyOutcome") or
            {"decision": p.get("policyDecision"), "rule_results": [], "rules_passed": 0, "rules_evaluated": 0}
        )
        run.update({
            "reportNarrative":    p.get("reportNarrative"),
            "reportJSON":         report_json,
            "policyOutcome":      policy_outcome,
            "policyDecision":     p.get("policyDecision"),
            "validationPassed":   p.get("validationPassed", 0),
            "validationFailed":   p.get("validationFailed", 0),
            "hallucinationFlags": p.get("hallucinationFlags", 0),
        })
        if not run.get("confidence"):
            rj = _safe_json(p.get("reportJSON"))
            if isinstance(rj, dict) and rj.get("confidence"):
                run["confidence"] = rj["confidence"]

    val_rows = list(client.query(f"""
        SELECT validationID, metricName, expectedValue, reportedValue,
               deviationPct, passed, hallucinationFlag, details
        FROM `{GCP_PROJECT}.{BQ_DATASET}.validation_results`
        WHERE runID = '{run_id}' ORDER BY validatedAt DESC
    """).result())
    run["validationResults"] = [_row_to_dict(r) for r in val_rows]

    dec_rows = list(client.query(f"""
        SELECT decision, reviewer, reason, decidedAt, editedNarrative
        FROM `{GCP_PROJECT}.{BQ_DATASET}.human_decisions`
        WHERE runID = '{run_id}' ORDER BY decidedAt DESC LIMIT 1
    """).result())
    if dec_rows:
        run["humanDecision"] = _row_to_dict(dec_rows[0])

    # If narrative not yet found, check approved_reports (post-decision)
    if not run.get("reportNarrative"):
        approved_rows = list(client.query(f"""
            SELECT reportNarrative, reportDate, approvedBy, approvedAt
            FROM `{GCP_PROJECT}.{BQ_DATASET}.approved_reports`
            WHERE agentRunID = '{run_id}'
            ORDER BY approvedAt DESC LIMIT 1
        """).result())
        if approved_rows:
            a = _row_to_dict(approved_rows[0])
            run["reportNarrative"] = a.get("reportNarrative")
            run["approvedBy"]      = a.get("approvedBy")
            run["approvedAt"]      = a.get("approvedAt")

    return run


# ── GET /api/runs/{run_id}/status  (admin only) ───────────────────────────────

@app.get("/api/runs/{run_id}/status")
def get_run_status(run_id: str, user: AuthUser = Depends(require_internal)):
    client = bq()

    pending = list(client.query(f"""
        SELECT status, policyDecision, confidence
        FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
        WHERE runID = '{run_id}' LIMIT 1
    """).result())
    if pending:
        p        = dict(pending[0])
        terminal = "escalated" if p.get("policyDecision") == "escalate" else "pending_review"

        conf_rows = list(client.query(f"""
            SELECT confidence FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
            WHERE runID = '{run_id}'
            ORDER BY startedAt DESC LIMIT 1
        """).result())
        conf_val = dict(conf_rows[0]).get("confidence") if conf_rows else None

        if conf_val is None:
            rj_rows = list(client.query(f"""
                SELECT reportJSON FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
                WHERE runID = '{run_id}' LIMIT 1
            """).result())
            if rj_rows:
                rj = _safe_json(dict(rj_rows[0]).get("reportJSON"))
                if isinstance(rj, dict):
                    conf_val = rj.get("confidence")

        return {"runID": run_id, "status": terminal, "confidence": conf_val, "policyDecision": p.get("policyDecision")}

    rows = list(client.query(f"""
        SELECT status, confidence, policyDecision
        FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
        WHERE runID = '{run_id}'
        ORDER BY startedAt DESC LIMIT 1
    """).result())
    if not rows:
        return {"runID": run_id, "status": "running"}
    r = dict(rows[0])
    agent_status = r.get("status") or "running"
    # If agent_runs shows running but row exists in pending_reports
    # (streaming buffer may hide it from SELECT above), treat as pending_review
    if agent_status == "running":
        try:
            pr = list(client.query(f"""
                SELECT COUNT(*) as cnt
                FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
                WHERE runID = '{run_id}'
            """).result())
            if pr and dict(pr[0]).get("cnt", 0) > 0:
                agent_status = "pending_review"
        except Exception:
            pass
    return {"runID": run_id, "status": agent_status, "confidence": r.get("confidence"), "policyDecision": r.get("policyDecision")}


# ── POST /api/decisions  (admin only) ─────────────────────────────────────────

@app.post("/api/decisions")
def post_decision(body: DecisionRequest, user: AuthUser = Depends(require_internal)):
    client      = bq()
    decision_id = str(uuid.uuid4())
    now         = datetime.now(timezone.utc).isoformat()

    if body.decision not in ("approved", "rejected", "edited_and_approved"):
        raise HTTPException(status_code=400, detail="Invalid decision value")
    if body.decision == "rejected" and not body.reason:
        raise HTTPException(status_code=400, detail="Rejection reason required")

    supplier_id = _get_run_field(client, body.runID, "supplierID")

    errors = client.insert_rows_json(
        f"{GCP_PROJECT}.{BQ_DATASET}.human_decisions",
        [{
            "decisionID":        decision_id,
            "runID":             body.runID,
            "reportType":        _get_run_field(client, body.runID, "reportType"),
            "audience":          _get_run_field(client, body.runID, "audience"),
            "supplierID":        supplier_id,
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

    try:
        if not body.runID.startswith("DEMO_"):
            client.query(f"""
                UPDATE `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
                SET status = 'decided' WHERE runID = '{body.runID}'
            """).result()
    except Exception:
        pass  # Row may still be in streaming buffer — safe to ignore

    agent_status = "approved" if body.decision in ("approved", "edited_and_approved") else "rejected"
    try:
        client.query(f"""
            UPDATE `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
            SET status = '{agent_status}' WHERE runID = '{body.runID}'
        """).result()
    except Exception:
        pass  # Row may still be in streaming buffer — safe to ignore

    if body.decision in ("approved", "edited_and_approved") and body.shareWithSupplier and supplier_id:
        narrative = body.editedNarrative or _get_pending_field(client, body.runID, "reportNarrative")
        if narrative:
            client.insert_rows_json(
                f"{GCP_PROJECT}.{BQ_DATASET}.approved_reports",
                [{
                    "reportID":        body.runID,
                    "supplierID":      supplier_id,
                    "reportType":      _get_run_field(client, body.runID, "reportType") or "adhoc_supplier",
                    "audience":        "supplier",
                    "reportDate":      datetime.now(timezone.utc).date().isoformat(),
                    "approvedAt":      now,
                    "approvedBy":      body.reviewer,
                    "reportJSON":      "{}",
                    "reportNarrative": narrative,
                    "confidence":      0.0,
                    "gcsPath":         "",
                    "agentRunID":      body.runID,
                }]
            )

    return {
        "decisionID": decision_id,
        "runID": body.runID,
        "decision": body.decision,
        "decidedAt": now,
        "sharedWithSupplier": bool(body.shareWithSupplier),
    }


# ── POST /api/runs/rerun/{run_id} ────────────────────────────────────────────

@app.post("/api/runs/rerun/{run_id}")
def trigger_rerun(run_id: str, user: AuthUser = Depends(require_reporter)):
    import threading
    client = bq()

    # Check retry count — max 2
    retry_rows = list(client.query(f"""
        SELECT COUNT(*) as cnt
        FROM `{GCP_PROJECT}.{BQ_DATASET}.human_decisions`
        WHERE retryTriggered = TRUE
          AND (runID = '{run_id}' OR retryRunID IN (
            SELECT runID FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
            WHERE goal LIKE '%parentRunID:{run_id}%'
          ))
    """).result())
    retry_count = dict(retry_rows[0])["cnt"] if retry_rows else 0
    if retry_count >= 2:
        raise HTTPException(status_code=400, detail="Maximum re-run attempts (2) reached for this report.")

    # Fetch original run details
    orig_goal        = _get_run_field(client, run_id, "goal") or ""
    orig_report_type = _get_run_field(client, run_id, "reportType") or "adhoc_business"
    orig_supplier_id = _get_run_field(client, run_id, "supplierID")
    orig_audience    = "supplier" if orig_supplier_id else "business"

    if not orig_goal:
        raise HTTPException(status_code=400, detail="Original run has no goal — cannot re-run.")

    # Fetch rejection reason from human_decisions
    reason_rows = list(client.query(f"""
        SELECT reason FROM `{GCP_PROJECT}.{BQ_DATASET}.human_decisions`
        WHERE runID = '{run_id}' AND decision = 'rejected'
        ORDER BY decidedAt DESC LIMIT 1
    """).result())
    reason = dict(reason_rows[0])["reason"] if reason_rows else ""

    # Build corrected goal
    corrected_goal = (
        orig_goal + "\n\n" +
        "CORRECTION FROM REVIEWER: The previous attempt was rejected. " +
        "Reason: " + (reason or "See reviewer notes.") + "\n" +
        "Please fix the above issue in your SQL and analysis. " +
        "parentRunID:" + run_id
    )

    rerun_id = str(uuid.uuid4())

    # Mark decision as having triggered a rerun
    try:
        client.query(f"""
            UPDATE `{GCP_PROJECT}.{BQ_DATASET}.human_decisions`
            SET retryTriggered = TRUE, retryRunID = '{rerun_id}'
            WHERE runID = '{run_id}' AND decision = 'rejected'
        """).result()
    except Exception:
        pass

    def _rerun():
        try:
            print(f"[api/runs/rerun] Starting re-run {rerun_id} for rejected run {run_id}")
            _run_agent(
                report_type = orig_report_type,
                goal        = corrected_goal,
                audience    = orig_audience,
                supplier_id = orig_supplier_id,
                date_from   = None,
                date_to     = None,
                thread_id   = rerun_id,
            )
        except Exception as e:
            print(f"[api/runs/rerun] Re-run {rerun_id} failed: {e}")

    threading.Thread(target=_rerun, daemon=True).start()
    print(f"[api/runs/rerun] Re-run {rerun_id} triggered for run {run_id}")
    return {"rerunID": rerun_id, "originalRunID": run_id, "status": "running"}


# ── GET /api/history  (admin only) ────────────────────────────────────────────

@app.get("/api/history")
def get_history(limit: int = 50, user: AuthUser = Depends(require_reporter)):
    client = bq()
    rows = list(client.query(f"""
        SELECT
            a.runID, a.reportType, a.audience, a.supplierID,
            a.status, a.confidence, a.startedAt, a.policyDecision,
            a.reportDate, d.decision, d.reviewer, d.decidedAt, d.reason
        FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs` a
        LEFT JOIN `{GCP_PROJECT}.{BQ_DATASET}.human_decisions` d ON a.runID = d.runID
        WHERE a.status IN ('approved','rejected','pending_review','escalated','pending_publish')
        ORDER BY a.startedAt DESC LIMIT {limit}
    """).result())
    return {"history": [_row_to_dict(r) for r in rows], "total": len(rows)}

# ── GET /api/observability/security  (internal: admin + demo) ────────────────

@app.get("/api/observability/security")
def get_security_events(
    limit:    int = 100,
    severity: str = None,
    user:     AuthUser = Depends(require_internal),
):
    """
    Returns security events from the security_events table.
    Optionally filter by severity: HIGH, MEDIUM, LOW.
    """
    client = bq()

    severity_filter = ""
    if severity and severity.upper() in ("HIGH", "MEDIUM", "LOW"):
        severity_filter = f"AND severity = '{severity.upper()}'"

    rows = list(client.query(f"""
        SELECT
            eventID, timestamp, runID, userUID, userRole,
            eventType, severity, detail, rawContent,
            endpoint, sourceNode
        FROM `{GCP_PROJECT}.{BQ_DATASET}.security_events`
        WHERE 1=1 {severity_filter}
        ORDER BY timestamp DESC
        LIMIT {limit}
    """).result())

    return {
        "events": [_row_to_dict(r) for r in rows],
        "total":  len(rows),
    }


# ── GET /api/observability/performance  (internal: admin + demo) ──────────────

@app.get("/api/observability/performance")
def get_performance(
    days: int = 30,
    user: AuthUser = Depends(require_internal),
):
    client = bq()

    by_type_rows = list(client.query(f"""
        WITH deduped AS (
            SELECT runID, reportType, confidence,
                   CAST(policyDecision AS STRING) AS policyDecision,
                   status, errors, startedAt,
                   ROW_NUMBER() OVER (PARTITION BY runID ORDER BY startedAt DESC) AS rn
            FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
            WHERE status IN ('approved','pending_review','escalated','pending_publish','rejected')
              AND startedAt >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        )
        SELECT
            reportType,
            COUNT(*)                                                       AS total_runs,
            ROUND(AVG(confidence), 3)                                      AS avg_confidence,
            COUNTIF(policyDecision = 'auto_approve')                       AS auto_approved,
            COUNTIF(policyDecision = 'route_to_queue')                     AS routed_to_queue,
            COUNTIF(policyDecision = 'escalate')                           AS escalated,
            ROUND(COUNTIF(policyDecision = 'auto_approve') * 100.0 / COUNT(*), 1) AS auto_approval_rate_pct,
            ROUND(COUNTIF(policyDecision = 'escalate')    * 100.0 / COUNT(*), 1) AS escalation_rate_pct
        FROM deduped WHERE rn = 1
        GROUP BY reportType
        ORDER BY total_runs DESC
    """).result())

    trend_rows = list(client.query(f"""
        WITH deduped AS (
            SELECT runID, confidence,
                   CAST(policyDecision AS STRING) AS policyDecision,
                   startedAt,
                   ROW_NUMBER() OVER (PARTITION BY runID ORDER BY startedAt DESC) AS rn
            FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
            WHERE status IN ('approved','pending_review','escalated','pending_publish','rejected')
              AND startedAt >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        )
        SELECT
            DATE_TRUNC(DATE(startedAt), WEEK)          AS week,
            ROUND(AVG(confidence), 3)                   AS avg_confidence,
            COUNT(*)                                    AS total_runs,
            COUNTIF(policyDecision = 'auto_approve')    AS auto_approved,
            COUNTIF(policyDecision = 'escalate')        AS escalated
        FROM deduped WHERE rn = 1
        GROUP BY week ORDER BY week ASC
    """).result())

    error_rows = list(client.query(f"""
        WITH deduped AS (
            SELECT runID, reportType, errors, startedAt,
                   ROW_NUMBER() OVER (PARTITION BY runID ORDER BY startedAt DESC) AS rn
            FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
            WHERE status IN ('pending_review','escalated','rejected')
              AND errors IS NOT NULL AND TO_JSON_STRING(errors) != '[]'
              AND startedAt >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        )
        SELECT reportType, TO_JSON_STRING(errors) AS errors, startedAt
        FROM deduped WHERE rn = 1
        ORDER BY startedAt DESC LIMIT 50
    """).result())

    import json as _json
    error_patterns = {}
    for row in error_rows:
        d = _row_to_dict(row)
        try:
            errs = _json.loads(d.get("errors") or "[]")
        except Exception:
            errs = []
        for e in errs:
            if "hallucination" in e.lower():
                key = "Hallucination flags"
            elif "required section" in e.lower():
                key = "Missing report sections"
            elif "guardrail" in e.lower():
                key = "Guardrail violations"
            elif "sql" in e.lower() or "query" in e.lower():
                key = "SQL errors"
            else:
                key = "Other"
            error_patterns[key] = error_patterns.get(key, 0) + 1

    return {
        "period_days":    days,
        "by_report_type": [_row_to_dict(r) for r in by_type_rows],
        "weekly_trend":   [_row_to_dict(r) for r in trend_rows],
        "error_patterns": [{"pattern": k, "count": v}
                           for k, v in sorted(error_patterns.items(), key=lambda x: -x[1])],
    }



# ── GET /api/suppliers  (internal: admin + business) ──────────────────────────

@app.get("/api/suppliers")
def get_suppliers(user: AuthUser = Depends(require_internal)):
    client = bq()
    rows = list(client.query(f"""
        SELECT supplierID, supplierName, supplierTier, supplierRegion, categorySpeciality
        FROM `{GCP_PROJECT}.{BQ_DATASET}.suppliers`
        ORDER BY supplierTier ASC, supplierName ASC
    """).result())
    return {"suppliers": [_row_to_dict(r) for r in rows]}


# ── GET /api/dashboard/business  (internal: admin + business) ─────────────────

@app.get("/api/dashboard/business")
def get_business_dashboard(
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    user:      AuthUser      = Depends(require_internal),
):
    client       = bq()
    order_filter = _build_date_filter(date_from, date_to)
    inc_filter   = _build_date_filter(date_from, date_to, "o")

    def _q(sql):
        return list(client.query(sql).result())

    with ThreadPoolExecutor(max_workers=7) as ex:
        f_sc = ex.submit(_q, f"""
            SELECT
                COUNT(orderID)                                                        AS total_orders,
                ROUND(SUM(grossRevenue), 2)                                           AS total_gross_revenue,
                ROUND(SUM(netRevenue), 2)                                             AS total_net_revenue,
                ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate_pct,
                ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)     AS return_rate_pct,
                ROUND(SUM(CASE WHEN hasReturn   THEN grossRevenue ELSE 0 END), 2)     AS returned_revenue
            FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
            WHERE {order_filter}
        """)
        f_res_cost = ex.submit(_q, f"""
            SELECT ROUND(SUM(i.resolutionCost), 2) AS total_resolution_cost
            FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
            INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
            WHERE {inc_filter}
        """)
        f_trend = ex.submit(_q, f"""
            SELECT
                FORMAT_DATE('%Y-%m', orderDate)                                       AS month,
                COUNT(orderID)                                                        AS total_orders,
                ROUND(SUM(grossRevenue), 2)                                           AS gross_revenue,
                ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate_pct,
                ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)     AS return_rate_pct
            FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
            WHERE {order_filter}
            GROUP BY month ORDER BY month ASC
        """)
        f_res_trend = ex.submit(_q, f"""
            SELECT
                FORMAT_DATE('%Y-%m', o.orderDate)                                       AS month,
                ROUND(SUM(o.grossRevenue), 2)                                           AS gross_revenue,
                ROUND(SUM(i.resolutionCost), 2)                                         AS resolution_cost,
                ROUND(SAFE_DIVIDE(SUM(i.resolutionCost), SUM(o.grossRevenue)) * 100, 2) AS resolution_cost_pct
            FROM `{GCP_PROJECT}.{BQ_DATASET}.orders` o
            LEFT JOIN `{GCP_PROJECT}.{BQ_DATASET}.incidents` i ON i.orderID = o.orderID
            WHERE {inc_filter}
            GROUP BY month ORDER BY month ASC
        """)
        f_suppliers = ex.submit(_q, f"""
            SELECT
                o.supplierID, s.supplierName, s.supplierTier,
                COUNT(o.orderID)                                                      AS total_orders,
                ROUND(AVG(CASE WHEN o.hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)   AS incident_rate_pct,
                ROUND(AVG(CASE WHEN o.hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)   AS return_rate_pct,
                ROUND(SUM(CASE WHEN o.hasReturn THEN o.grossRevenue ELSE 0 END), 2)   AS returned_revenue
            FROM `{GCP_PROJECT}.{BQ_DATASET}.orders` o
            LEFT JOIN `{GCP_PROJECT}.{BQ_DATASET}.suppliers` s ON o.supplierID = s.supplierID
            WHERE {order_filter}
            GROUP BY o.supplierID, s.supplierName, s.supplierTier
            ORDER BY incident_rate_pct DESC LIMIT 10
        """)
        f_categories = ex.submit(_q, f"""
            SELECT
                productCategory,
                COUNT(orderID)                                                        AS total_orders,
                ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate_pct,
                ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)     AS return_rate_pct
            FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
            WHERE {order_filter}
            GROUP BY productCategory ORDER BY incident_rate_pct DESC
        """)
        f_res_mix = ex.submit(_q, f"""
            SELECT
                i.incidentResolution,
                COUNT(i.incidentID)              AS total_incidents,
                ROUND(SUM(i.resolutionCost), 2)  AS total_cost
            FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
            INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
            WHERE {inc_filter}
            GROUP BY i.incidentResolution ORDER BY total_incidents DESC
        """)

    sc         = f_sc.result()[0]
    res_cost   = f_res_cost.result()[0]
    trend      = f_trend.result()
    res_trend  = f_res_trend.result()
    suppliers  = f_suppliers.result()
    categories = f_categories.result()
    res_mix    = f_res_mix.result()

    return {
        "scorecards": {
            "total_orders":          float(sc["total_orders"] or 0),
            "total_gross_revenue":   float(sc["total_gross_revenue"] or 0),
            "total_net_revenue":     float(sc["total_net_revenue"] or 0),
            "incident_rate_pct":     float(sc["incident_rate_pct"] or 0),
            "return_rate_pct":       float(sc["return_rate_pct"] or 0),
            "returned_revenue":      float(sc["returned_revenue"] or 0),
            "total_resolution_cost": float(res_cost["total_resolution_cost"] or 0),
        },
        "trend":          [_row_to_dict(r) for r in trend],
        "res_trend":      [_row_to_dict(r) for r in res_trend],
        "by_supplier":    [_row_to_dict(r) for r in suppliers],
        "by_category":    [_row_to_dict(r) for r in categories],
        "resolution_mix": [_row_to_dict(r) for r in res_mix],
    }


# ── GET /api/dashboard/supplier/{supplier_id} ─────────────────────────────────
# Admin/business see any supplier. Supplier role sees only their own.

@app.get("/api/dashboard/supplier/{supplier_id}")
def get_supplier_dashboard(
    supplier_id: str,
    date_from:   Optional[str] = None,
    date_to:     Optional[str] = None,
    user:        AuthUser      = Depends(get_current_user),
):
    # Suppliers can only see their own data
    if user.role == "supplier":
        if user.supplier_id != supplier_id:
            raise HTTPException(status_code=403, detail="Access denied to this supplier's data")
    elif user.role not in ("admin", "business"):
        raise HTTPException(status_code=403, detail="Access denied")

    client       = bq()
    order_filter = _build_date_filter(date_from, date_to)
    inc_filter   = _build_date_filter(date_from, date_to, "o")

    sup_rows = list(client.query(f"""
        SELECT supplierID, supplierName, supplierTier, supplierRegion, categorySpeciality
        FROM `{GCP_PROJECT}.{BQ_DATASET}.suppliers`
        WHERE supplierID = '{supplier_id}' LIMIT 1
    """).result())
    if not sup_rows:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
    supplier = _row_to_dict(sup_rows[0])

    def _q(sql):
        return list(client.query(sql).result())

    with ThreadPoolExecutor(max_workers=11) as ex:
        f_sc = ex.submit(_q, f"""
            SELECT
                COUNT(orderID)                                                        AS total_orders,
                ROUND(SUM(grossRevenue), 2)                                           AS total_gross_revenue,
                ROUND(SUM(productCost), 2)                                            AS total_product_cost,
                ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate_pct,
                ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)     AS return_rate_pct,
                ROUND(SUM(CASE WHEN hasReturn   THEN grossRevenue ELSE 0 END), 2)     AS returned_revenue
            FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
            WHERE supplierID = '{supplier_id}' AND {order_filter}
        """)
        f_res_cost = ex.submit(_q, f"""
            SELECT ROUND(SUM(i.resolutionCost), 2) AS total_resolution_cost
            FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
            INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
            WHERE i.supplierID = '{supplier_id}' AND {inc_filter}
        """)
        f_bench = ex.submit(_q, f"""
            SELECT
                ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2) AS portfolio_incident_rate,
                ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2) AS portfolio_return_rate
            FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
            WHERE {order_filter}
        """)
        f_bench_cost = ex.submit(_q, f"""
            SELECT
                ROUND(SUM(i.resolutionCost), 2)    AS portfolio_resolution_cost,
                COUNT(DISTINCT i.supplierID)        AS supplier_count
            FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
            INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
            WHERE {inc_filter}
        """)
        f_cat_inc = ex.submit(_q, f"""
            SELECT
                productCategory,
                COUNT(orderID)                                                        AS total_orders,
                ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate_pct
            FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
            WHERE supplierID = '{supplier_id}' AND {order_filter}
            GROUP BY productCategory ORDER BY incident_rate_pct DESC
        """)
        f_cat_ret = ex.submit(_q, f"""
            SELECT
                productCategory,
                COUNT(orderID)                                                        AS total_orders,
                ROUND(AVG(CASE WHEN hasReturn THEN 1.0 ELSE 0.0 END) * 100, 2)       AS return_rate_pct
            FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
            WHERE supplierID = '{supplier_id}' AND {order_filter}
            GROUP BY productCategory ORDER BY return_rate_pct DESC
        """)
        f_sku_inc = ex.submit(_q, f"""
            SELECT
                i.productSKU, i.productCategory, i.incidentType,
                COUNT(i.incidentID)              AS total_incidents,
                ROUND(SUM(i.resolutionCost), 2)  AS total_resolution_cost,
                ROUND(AVG(i.resolutionCost), 2)  AS avg_resolution_cost,
                ROUND(AVG(i.productRating), 2)   AS avg_product_rating
            FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
            INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
            WHERE i.supplierID = '{supplier_id}' AND {inc_filter}
            GROUP BY i.productSKU, i.productCategory, i.incidentType
            ORDER BY total_incidents DESC LIMIT 100
        """)
        f_sku_ret = ex.submit(_q, f"""
            SELECT
                r.productSKU, r.productCategory, r.buyersRemorseReason,
                COUNT(r.returnID)                AS total_returns,
                ROUND(AVG(r.productRating), 2)   AS avg_product_rating
            FROM `{GCP_PROJECT}.{BQ_DATASET}.returns` r
            INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON r.orderID = o.orderID
            WHERE r.supplierID = '{supplier_id}' AND {inc_filter}
            GROUP BY r.productSKU, r.productCategory, r.buyersRemorseReason
            ORDER BY total_returns DESC LIMIT 100
        """)
        f_ret_reasons = ex.submit(_q, f"""
            SELECT
                r.buyersRemorseReason,
                COUNT(r.returnID)                AS total_returns,
                ROUND(AVG(r.productRating), 2)   AS avg_product_rating
            FROM `{GCP_PROJECT}.{BQ_DATASET}.returns` r
            INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON r.orderID = o.orderID
            WHERE r.supplierID = '{supplier_id}' AND {inc_filter}
            GROUP BY r.buyersRemorseReason ORDER BY total_returns DESC
        """)
        f_inc_types = ex.submit(_q, f"""
            SELECT
                i.incidentType,
                COUNT(i.incidentID)              AS total_incidents,
                ROUND(SUM(i.resolutionCost), 2)  AS total_cost
            FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
            INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
            WHERE i.supplierID = '{supplier_id}' AND {inc_filter}
            GROUP BY i.incidentType ORDER BY total_incidents DESC
        """)
        f_res_mix = ex.submit(_q, f"""
            SELECT
                i.incidentResolution,
                COUNT(i.incidentID)              AS total_incidents,
                ROUND(SUM(i.resolutionCost), 2)  AS total_cost
            FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
            INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
            WHERE i.supplierID = '{supplier_id}' AND {inc_filter}
            GROUP BY i.incidentResolution ORDER BY total_incidents DESC
        """)
        f_reports = ex.submit(_q, f"""
            SELECT reportNarrative, reportDate, confidence, approvedBy, approvedAt, reportType
            FROM `{GCP_PROJECT}.{BQ_DATASET}.approved_reports`
            WHERE supplierID = '{supplier_id}' AND audience = 'supplier'
            ORDER BY reportDate DESC LIMIT 5
        """)

    sc         = f_sc.result()[0]
    res_cost   = f_res_cost.result()[0]
    bench      = f_bench.result()[0]
    bench_cost = f_bench_cost.result()[0]

    portfolio_avg_res_cost = (
        float(bench_cost["portfolio_resolution_cost"] or 0) /
        max(float(bench_cost["supplier_count"] or 1), 1)
    )

    return {
        "supplier_id": supplier_id,
        "supplier":    supplier,
        "scorecards": {
            "total_orders":            float(sc["total_orders"] or 0),
            "total_gross_revenue":     float(sc["total_gross_revenue"] or 0),
            "total_product_cost":      float(sc["total_product_cost"] or 0),
            "incident_rate_pct":       float(sc["incident_rate_pct"] or 0),
            "return_rate_pct":         float(sc["return_rate_pct"] or 0),
            "returned_revenue":        float(sc["returned_revenue"] or 0),
            "total_resolution_cost":   float(res_cost["total_resolution_cost"] or 0),
            "portfolio_incident_rate": float(bench["portfolio_incident_rate"] or 0),
            "portfolio_return_rate":   float(bench["portfolio_return_rate"] or 0),
            "portfolio_avg_res_cost":  round(portfolio_avg_res_cost, 2),
        },
        "cat_incident_rate": [_row_to_dict(r) for r in f_cat_inc.result()],
        "cat_return_rate":   [_row_to_dict(r) for r in f_cat_ret.result()],
        "sku_incidents":     [_row_to_dict(r) for r in f_sku_inc.result()],
        "sku_returns":       [_row_to_dict(r) for r in f_sku_ret.result()],
        "return_reasons":    [_row_to_dict(r) for r in f_ret_reasons.result()],
        "incident_types":    [_row_to_dict(r) for r in f_inc_types.result()],
        "resolution_mix":    [_row_to_dict(r) for r in f_res_mix.result()],
        "reports":           [_row_to_dict(r) for r in f_reports.result()],
    }


# ── POST /api/runs  (admin only) ──────────────────────────────────────────────

@app.post("/api/runs")
def trigger_run(body: RunRequest, user: AuthUser = Depends(require_reporter)):
    import threading

    run_id = str(uuid.uuid4())

    def _run():
        try:
            effective_goal = ("[" + body.reportTitle + "] " + (body.goal or "")) if body.reportTitle else body.goal
            _run_agent(
                report_type = body.reportType,
                goal        = effective_goal,
                audience    = "supplier" if body.supplierID else "business",
                supplier_id = body.supplierID.upper() if body.supplierID else None,
                date_from   = body.dateFrom,
                date_to     = body.dateTo,
                thread_id   = run_id,
            )
        except Exception as e:
            print(f"[api/runs] Run {run_id} failed: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return {"runID": run_id, "status": "running"}


# ── POST /api/ask/session  (internal: admin + business) ───────────────────────

@app.post("/api/ask/session")
def create_session(
    supplierID: Optional[str] = None,
    user:       AuthUser      = Depends(require_internal),
):
    global _sessions
    if len(_sessions) >= MAX_SESSIONS:
        oldest = min(_sessions.items(), key=lambda x: x[1]["created_at"])
        del _sessions[oldest[0]]
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "messages":    [],
        "exchanges":   [],
        "supplier_id": supplierID,
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }
    return {"sessionID": session_id, "supplierID": supplierID}


# ── DELETE /api/ask/session/{session_id}  (internal) ──────────────────────────

@app.delete("/api/ask/session/{session_id}")
def delete_session(session_id: str, user: AuthUser = Depends(require_internal)):
    if session_id in _sessions:
        del _sessions[session_id]
    return {"sessionID": session_id, "deleted": True}


# ── GET /api/ask/session/{session_id}  (internal) ─────────────────────────────

@app.get("/api/ask/session/{session_id}")
def get_session(session_id: str, user: AuthUser = Depends(require_internal)):
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    session = _sessions[session_id]
    return {
        "sessionID":  session_id,
        "supplierID": session["supplier_id"],
        "exchanges":  session["exchanges"],
        "turn_count": len(session["exchanges"]),
        "created_at": session["created_at"],
    }


# ── POST /api/ask  (internal: admin + business) ───────────────────────────────

@app.post("/api/ask")
def ask_question(body: AskRequest, user: AuthUser = Depends(require_internal)):
    import yaml
    from anthropic import Anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    config_path = Path(__file__).parent / "agent" / "config" / "metadata.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    table_context = []
    ref = config["reports"].get("monthly_supplier_account", config["reports"].get("weekly_supplier_overview", {}))
    for tname, tcfg in ref.get("tables", {}).items():
        cols = tcfg.get("allowed_columns", [])
        table_context.append(f"Table: {tname}\n  Columns: {', '.join(cols)}")

    session     = None
    session_id  = body.sessionID
    supplier_id = body.supplierID

    if session_id and session_id in _sessions:
        session     = _sessions[session_id]
        supplier_id = session["supplier_id"] or supplier_id

    supplier_context = f"\nScope all queries to supplierID = '{supplier_id}' only." if supplier_id else ""

    system_prompt = f"""You are a conversational BigQuery SQL analyst for a supplier performance system.
You maintain context across a conversation — you can reference previous questions and results.

Rules:
- Use ONLY the tables and columns listed below
- Always use full table paths: `{GCP_PROJECT}.{BQ_DATASET}.table_name`
- Join incidents/returns to orders on orderID for date filtering
- Always use CURRENT_DATE() with parentheses
- Use DATE_SUB(CURRENT_DATE(), INTERVAL N DAY/MONTH) for date ranges
- For period comparisons use CASE WHEN with date ranges
- Return ONLY the SQL — no explanation, no markdown{supplier_context}
- Max 50 rows unless specified
- Never SELECT * — name all columns
- CTEs allowed for complex queries
- All string literals use single quotes
- When the user says "that supplier", "those SKUs", "drill into X" — use context from previous turns

Available tables:
{chr(10).join(table_context)}"""

    client_ai = Anthropic(api_key=api_key)
    history   = []

    if session:
        for exchange in session["exchanges"]:
            history.append({"role": "user",     "content": exchange["question"]})
            history.append({"role": "assistant", "content": exchange["sql"]})
            row_count = exchange.get("rows", 0)
            if row_count > 0 and exchange.get("data"):
                cols = list(exchange["data"][0].keys()) if exchange["data"] else []
                history.append({
                    "role": "user",
                    "content": f"[Query returned {row_count} rows with columns: {', '.join(cols)}. Now answer the next question using this context.]"
                })

    history.append({"role": "user", "content": body.question})

    last_error = None
    sql        = None
    data       = []

    for attempt in range(3):
        messages = history.copy()
        if last_error and sql:
            messages.append({"role": "assistant", "content": sql})
            messages.append({"role": "user", "content": f"That SQL failed: {last_error}\n\nFix and return only the corrected SQL."})

        resp = client_ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system_prompt,
            messages=messages,
        )
        sql = resp.content[0].text.strip()
        if sql.startswith("```"):
            sql = sql.split("```")[1]
            if sql.lower().startswith("sql"):
                sql = sql[3:]
        sql = sql.strip()

        for op in ["DROP","DELETE","UPDATE","INSERT","CREATE","ALTER","TRUNCATE"]:
            if f" {op} " in sql.upper() or sql.upper().startswith(op):
                raise HTTPException(status_code=400, detail="That question can't be answered with the available data.")

        try:
            bq_client = bq()
            rows      = list(bq_client.query(sql).result())
            data      = [_row_to_dict(r) for r in rows]
            break
        except Exception as e:
            last_error = str(e).split("\n")[0]
            if attempt == 2:
                raise HTTPException(status_code=400, detail="I wasn't able to answer that question. Try rephrasing it or being more specific.")

    if session is not None:
        session["exchanges"].append({
            "question": body.question,
            "sql":      sql,
            "data":     data[:5],
            "rows":     len(data),
        })
        session["exchanges"] = session["exchanges"][-10:]

    try:
        bq().insert_rows_json(f"{GCP_PROJECT}.{BQ_DATASET}.agent_runs", [{
            "runID":       str(uuid.uuid4()),
            "reportType":  "nl_query",
            "audience":    "supplier" if supplier_id else "business",
            "supplierID":  supplier_id,
            "goal":        body.question,
            "startedAt":   datetime.now(timezone.utc).isoformat(),
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "status":      "completed",
            "queries":     json.dumps({"nl_query": sql}),
            "reportDate":  datetime.now(timezone.utc).date().isoformat(),
        }])
    except Exception:
        pass

    return {"question": body.question, "sql": sql, "data": data, "rows": len(data), "sessionID": session_id}


# ── GET /api/insights/current  (internal: admin + business) ───────────────────

@app.get("/api/insights/current")
def get_insights_current(user: AuthUser = Depends(require_internal)):
    client = bq()

    digest_rows = list(client.query(f"""
        SELECT digestID, weekOf, narrative, totalAlerts, criticalCount,
               warningCount, watchCount, confidence, autoPublished, generatedAt
        FROM `{GCP_PROJECT}.{BQ_DATASET}.insight_digests`
        WHERE autoPublished = TRUE
        ORDER BY weekOf DESC
        LIMIT 1
    """).result())

    if not digest_rows:
        return {"digest": None, "alerts": [], "has_insights": False}

    digest   = _row_to_dict(digest_rows[0])
    week_of  = str(digest["weekOf"])

    alert_rows = list(client.query(f"""
        SELECT insightID, weekOf, signalType, severity, supplierID,
               productSKU, productCategory, metricName, currentValue,
               baselineValue, changePercent, description, confidence
        FROM `{GCP_PROJECT}.{BQ_DATASET}.insights`
        WHERE weekOf = '{week_of}'
          AND autoPublished = TRUE
        ORDER BY
            CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
            changePercent DESC
    """).result())

    return {
        "digest":       digest,
        "alerts":       [_row_to_dict(r) for r in alert_rows],
        "has_insights": True,
    }


# ── GET /api/insights/history  (internal: admin + business) ───────────────────

@app.get("/api/insights/history")
def get_insights_history(user: AuthUser = Depends(require_internal)):
    client = bq()

    rows = list(client.query(f"""
        SELECT insightID, weekOf, signalType, severity, supplierID,
               productSKU, productCategory, metricName, currentValue,
               baselineValue, changePercent, description, confidence, autoPublished
        FROM `{GCP_PROJECT}.{BQ_DATASET}.insights`
        ORDER BY weekOf DESC,
            CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
            changePercent DESC
    """).result())

    weeks = {}
    for row in rows:
        d = _row_to_dict(row)
        w = str(d["weekOf"])
        if w not in weeks:
            weeks[w] = {"weekOf": w, "alerts": [], "critical": 0, "warning": 0, "watch": 0}
        weeks[w]["alerts"].append(d)
        weeks[w][d["severity"]] = weeks[w].get(d["severity"], 0) + 1

    digest_rows = list(client.query(f"""
        SELECT weekOf, narrative, totalAlerts, criticalCount, warningCount, watchCount
        FROM `{GCP_PROJECT}.{BQ_DATASET}.insight_digests`
        ORDER BY weekOf DESC
    """).result())

    for dr in digest_rows:
        d = _row_to_dict(dr)
        w = str(d["weekOf"])
        if w in weeks:
            weeks[w]["narrative"] = d["narrative"]

    return {"weeks": list(weeks.values())}



# ── GET /api/reports  (internal: admin + business) ────────────────────────────

@app.get("/api/reports")
def get_reports(limit: int = 20, user: AuthUser = Depends(require_reporter)):
    client = bq()
    rows = list(client.query(f"""
        SELECT
            reportID, supplierID, reportType, audience,
            reportDate, approvedAt, approvedBy,
            reportNarrative, confidence
        FROM `{GCP_PROJECT}.{BQ_DATASET}.approved_reports`
        ORDER BY approvedAt DESC
        LIMIT {limit}
    """).result())
    return {"reports": [_row_to_dict(r) for r in rows]}



# ── GET /api/reports/supplier/{supplier_id} ──────────────────────────────

@app.get("/api/reports/supplier/{supplier_id}")
def get_supplier_reports(supplier_id: str, limit: int = 20, user: AuthUser = Depends(get_current_user)):
    # Suppliers can only see their own reports; business/admin can see any
    if user.role == "supplier" and user.supplier_id != supplier_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if user.role not in ("admin", "business", "demo", "supplier"):
        raise HTTPException(status_code=403, detail="Access denied")
    client = bq()
    rows = list(client.query(f"""
        SELECT
            reportID, supplierID, reportType, audience,
            reportDate, approvedAt, approvedBy,
            reportNarrative, confidence
        FROM `{GCP_PROJECT}.{BQ_DATASET}.approved_reports`
        WHERE supplierID = '{supplier_id}'
           OR (audience = 'supplier' AND supplierID = '{supplier_id}')
        ORDER BY approvedAt DESC
        LIMIT {limit}
    """).result())
    return {"reports": [_row_to_dict(r) for r in rows]}


# ── GET /api/customer-voice/{supplier_id} ────────────────────────────────────

@app.get("/api/customer-voice/{supplier_id}")
def get_customer_voice(supplier_id: str, month: str = None, user: AuthUser = Depends(require_internal)):
    client = bq()

    # Enforce supplier role can only see their own data
    if user.role == "supplier" and user.supplier_id != supplier_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get available months first
    month_rows = list(client.query(f"""
        SELECT DISTINCT analysisMonth
        FROM `{GCP_PROJECT}.{BQ_DATASET}.sku_comment_intelligence`
        WHERE supplierID = '{supplier_id}'
        ORDER BY analysisMonth DESC
        LIMIT 12
    """).result())

    months = [str(dict(r)["analysisMonth"]) for r in month_rows]
    if not months:
        return {"supplierID": supplier_id, "months": [], "skus": []}

    selected_month = month if month in months else months[0]

    rows = list(client.query(f"""
        SELECT *
        FROM `{GCP_PROJECT}.{BQ_DATASET}.sku_comment_intelligence`
        WHERE supplierID = '{supplier_id}'
          AND analysisMonth = '{selected_month}'
        ORDER BY maxDeviation DESC
    """).result())

    import json

    skus = []
    for row in rows:
        r = _row_to_dict(row)
        # Parse JSON fields
        for field in ("incidentThemes", "returnThemes", "rootCauses", "improvements"):
            val = r.get(field)
            if isinstance(val, str):
                try:
                    r[field] = json.loads(val)
                except Exception:
                    r[field] = []
            elif val is None:
                r[field] = []
        skus.append(r)

    return {
        "supplierID": supplier_id,
        "selectedMonth": selected_month,
        "months": months,
        "skus": skus,
    }

# ── GET /api/recent-reports  (reporter: admin + business) ────────────────────

@app.get("/api/recent-reports")
def get_recent_reports(limit: int = 10, user: AuthUser = Depends(require_reporter)):
    """
    Returns the last N runs with narrative sourced from the correct table:
    - pending_review / pending  → narrative from pending_reports
    - approved / auto_approved  → narrative from approved_reports
    - rejected                  → narrative from pending_reports + rejection reason
    """
    client = bq()

    # Get recent runs
    run_rows = list(client.query(f"""
        WITH all_runs AS (
            SELECT
                runID, reportType, audience, supplierID, goal,
                status, confidence, policyDecision, startedAt,
                ROW_NUMBER() OVER (PARTITION BY runID ORDER BY startedAt DESC) AS rn
            FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
            WHERE reportType IN ('adhoc_business','adhoc_supplier')
        )
        SELECT
            r.runID, r.reportType, r.audience, r.supplierID, r.goal,
            r.status, r.confidence, r.startedAt, r.policyDecision,
            d.decision, d.reviewer, d.reason, d.decidedAt
        FROM all_runs r
        LEFT JOIN `{GCP_PROJECT}.{BQ_DATASET}.human_decisions` d ON r.runID = d.runID
        WHERE r.rn = 1
        ORDER BY r.startedAt DESC
        LIMIT {limit}
    """).result())

    if not run_rows:
        return {"reports": []}

    run_ids = [dict(r)["runID"] for r in run_rows]
    ids_str  = ", ".join(f"'{rid}'" for rid in run_ids)

    # Fetch narratives from pending_reports
    pending_narratives = {}
    try:
        pr_rows = list(client.query(f"""
            SELECT runID, reportNarrative, status, confidence, queuedAt
            FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
            WHERE runID IN ({ids_str})
        """).result())
        for r in pr_rows:
            d = _row_to_dict(r)
            pending_narratives[d["runID"]] = d
    except Exception:
        pass

    # Fetch narratives from approved_reports
    approved_narratives = {}
    try:
        ar_rows = list(client.query(f"""
            SELECT agentRunID, reportNarrative, approvedBy, approvedAt, audience
            FROM `{GCP_PROJECT}.{BQ_DATASET}.approved_reports`
            WHERE agentRunID IN ({ids_str})
            ORDER BY approvedAt DESC
        """).result())
        for r in ar_rows:
            d = _row_to_dict(r)
            rid = d["agentRunID"]
            if rid not in approved_narratives:
                approved_narratives[rid] = d
    except Exception:
        pass

    results = []
    for row in run_rows:
        run = _row_to_dict(row)
        rid = run["runID"]
        decision = run.get("decision") or run.get("status") or "running"

        # Source narrative from correct table
        narrative    = None
        approved_by  = None
        approved_at  = None

        if decision in ("approved", "edited_and_approved", "auto_approved"):
            ar = approved_narratives.get(rid)
            if ar:
                narrative   = ar.get("reportNarrative")
                approved_by = ar.get("approvedBy")
                approved_at = ar.get("approvedAt")
        else:
            pr = pending_narratives.get(rid)
            if pr:
                narrative = pr.get("reportNarrative")
                if not run.get("confidence") and pr.get("confidence"):
                    run["confidence"] = pr["confidence"]

        run["reportNarrative"] = narrative
        run["approvedBy"]      = approved_by
        run["approvedAt"]      = approved_at

        # Determine display status
        if decision in ("pending_review", "pending", "pending_publish", "escalated", "running"):
            run["displayStatus"] = "pending_review"
        elif decision in ("approved", "edited_and_approved", "auto_approved"):
            run["displayStatus"] = "approved"
        elif decision == "rejected":
            run["displayStatus"] = "rejected"
        else:
            run["displayStatus"] = "pending_review"  # default to awaiting review

        results.append(run)

    return {"reports": results}


# ── GET /api/reports/supplier/{supplier_id} ──────────────────────────────

@app.get("/api/reports/supplier/{supplier_id}")
def get_supplier_reports(supplier_id: str, limit: int = 20, user: AuthUser = Depends(get_current_user)):
    # Suppliers can only see their own reports; business/admin can see any
    if user.role == "supplier" and user.supplier_id != supplier_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if user.role not in ("admin", "business", "demo", "supplier"):
        raise HTTPException(status_code=403, detail="Access denied")
    client = bq()
    rows = list(client.query(f"""
        SELECT
            reportID, supplierID, reportType, audience,
            reportDate, approvedAt, approvedBy,
            reportNarrative, confidence
        FROM `{GCP_PROJECT}.{BQ_DATASET}.approved_reports`
        WHERE supplierID = '{supplier_id}'
           OR (audience = 'supplier' AND supplierID = '{supplier_id}')
        ORDER BY approvedAt DESC
        LIMIT {limit}
    """).result())
    return {"reports": [_row_to_dict(r) for r in rows]}


# ── GET /api/customer-voice/{supplier_id} ────────────────────────────────────

@app.get("/api/customer-voice/{supplier_id}")
def get_customer_voice(supplier_id: str, month: str = None, user: AuthUser = Depends(get_current_user)):
    """Customer Voice data from sku_comment_intelligence. Accessible to all roles
    but suppliers can only see their own data."""
    if user.role == "supplier" and user.supplier_id != supplier_id:
        raise HTTPException(status_code=403, detail="Access denied")

    client = bq()

    # Get available months
    months_rows = list(client.query(f"""
        SELECT DISTINCT analysisMonth
        FROM `{GCP_PROJECT}.{BQ_DATASET}.sku_comment_intelligence`
        WHERE supplierID = '{supplier_id}'
        ORDER BY analysisMonth DESC
    """).result())

    if not months_rows:
        return {"supplierID": supplier_id, "month": None, "months": [], "skus": []}

    available_months = [str(dict(r)["analysisMonth"]) for r in months_rows]
    selected_month = month if month in available_months else available_months[0]

    rows = list(client.query(f"""
        SELECT *
        FROM `{GCP_PROJECT}.{BQ_DATASET}.sku_comment_intelligence`
        WHERE supplierID = '{supplier_id}'
        AND analysisMonth = '{selected_month}'
        ORDER BY maxDeviation DESC
    """).result())

    import json

    skus = []
    for row in rows:
        r = _row_to_dict(row)
        # Parse JSON fields
        for field in ("incidentThemes", "returnThemes", "rootCauses", "improvements"):
            val = r.get(field)
            if isinstance(val, str):
                try: r[field] = json.loads(val)
                except: r[field] = []
            elif val is None:
                r[field] = []
        skus.append(r)

    return {
        "supplierID": supplier_id,
        "month": selected_month,
        "months": available_months,
        "skus": skus,
    }

# ── Serve React frontend ──────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        index = STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"error": "Frontend not built"}
