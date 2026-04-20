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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google.cloud import bigquery
from pydantic import BaseModel

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

class RunRequest(BaseModel):
    reportType: str
    supplierID: Optional[str] = None
    goal:       str
    dateFrom:   Optional[str] = None
    dateTo:     Optional[str] = None

class AskRequest(BaseModel):
    question:   str
    supplierID: Optional[str] = None


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
    for key in ["startedAt", "completedAt", "decidedAt", "queuedAt",
                "validatedAt", "approvedAt", "onboardingDate"]:
        if key in d and d[key] and hasattr(d[key], "isoformat"):
            d[key] = d[key].isoformat()
    for key in ["reportDate", "orderDate"]:
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
        run_rows = list(client.query(f"""
            SELECT flags, errors FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
            WHERE runID = '{d["runID"]}' LIMIT 1
        """).result())
        if run_rows:
            d["flags"] = _safe_json(_row_to_dict(run_rows[0]).get("flags")) or []
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

    pending_rows = list(client.query(f"""
        SELECT reportNarrative, reportJSON, policyDecision,
               validationPassed, validationFailed, hallucinationFlags
        FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
        WHERE runID = '{run_id}' LIMIT 1
    """).result())
    if pending_rows:
        p = _row_to_dict(pending_rows[0])
        run.update({
            "reportNarrative":    p.get("reportNarrative"),
            "reportJSON":         _safe_json(p.get("reportJSON")),
            "validationPassed":   p.get("validationPassed", 0),
            "validationFailed":   p.get("validationFailed", 0),
            "hallucinationFlags": p.get("hallucinationFlags", 0),
        })
        # Also read confidence from reportJSON if not in agent_runs
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

    return run


# ── GET /api/runs/{run_id}/status ─────────────────────────────────────────────

@app.get("/api/runs/{run_id}/status")
def get_run_status(run_id: str):
    client = bq()

    # Check pending_reports first — if it exists there, pipeline is done
    pending = list(client.query(f"""
        SELECT status, policyDecision, confidence
        FROM `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
        WHERE runID = '{run_id}' LIMIT 1
    """).result())
    if pending:
        p        = dict(pending[0])
        terminal = "escalated" if p.get("policyDecision") == "escalate" else "pending_review"

        # Confidence is often null in agent_runs due to BigQuery streaming buffer delay.
        # Try agent_runs first, then fall back to reportJSON.confidence in pending_reports.
        conf_rows = list(client.query(f"""
            SELECT confidence FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
            WHERE runID = '{run_id}' LIMIT 1
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

    # Fall back to agent_runs
    rows = list(client.query(f"""
        SELECT status, confidence, policyDecision
        FROM `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
        WHERE runID = '{run_id}' LIMIT 1
    """).result())
    if not rows:
        return {"runID": run_id, "status": "running"}
    r = dict(rows[0])
    return {"runID": run_id, "status": r.get("status") or "running", "confidence": r.get("confidence"), "policyDecision": r.get("policyDecision")}


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

    client.query(f"""
        UPDATE `{GCP_PROJECT}.{BQ_DATASET}.pending_reports`
        SET status = 'decided' WHERE runID = '{body.runID}'
    """).result()

    agent_status = "approved" if body.decision in ("approved", "edited_and_approved") else "rejected"
    client.query(f"""
        UPDATE `{GCP_PROJECT}.{BQ_DATASET}.agent_runs`
        SET status = '{agent_status}' WHERE runID = '{body.runID}'
    """).result()

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

    return {"decisionID": decision_id, "runID": body.runID, "decision": body.decision, "decidedAt": now, "sharedWithSupplier": bool(body.shareWithSupplier)}


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
        ORDER BY a.startedAt DESC LIMIT {limit}
    """).result())
    return {"history": [_row_to_dict(r) for r in rows], "total": len(rows)}


# ── GET /api/suppliers ────────────────────────────────────────────────────────

@app.get("/api/suppliers")
def get_suppliers():
    client = bq()
    rows = list(client.query(f"""
        SELECT supplierID, supplierName, supplierTier, supplierRegion, categorySpeciality
        FROM `{GCP_PROJECT}.{BQ_DATASET}.suppliers`
        ORDER BY supplierTier ASC, supplierName ASC
    """).result())
    return {"suppliers": [_row_to_dict(r) for r in rows]}


# ── GET /api/dashboard/business ───────────────────────────────────────────────

@app.get("/api/dashboard/business")
def get_business_dashboard(
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
):
    client       = bq()
    order_filter = _build_date_filter(date_from, date_to)
    inc_filter   = _build_date_filter(date_from, date_to, "o")

    sc = list(client.query(f"""
        SELECT
            COUNT(orderID)                                                        AS total_orders,
            ROUND(SUM(grossRevenue), 2)                                           AS total_gross_revenue,
            ROUND(SUM(netRevenue), 2)                                             AS total_net_revenue,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate_pct,
            ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)     AS return_rate_pct,
            ROUND(SUM(CASE WHEN hasReturn   THEN grossRevenue ELSE 0 END), 2)     AS returned_revenue
        FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
        WHERE {order_filter}
    """).result())[0]

    res_cost = list(client.query(f"""
        SELECT ROUND(SUM(i.resolutionCost), 2) AS total_resolution_cost
        FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
        INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
        WHERE {inc_filter}
    """).result())[0]

    trend = list(client.query(f"""
        SELECT
            FORMAT_DATE('%Y-%m', orderDate)                                       AS month,
            COUNT(orderID)                                                        AS total_orders,
            ROUND(SUM(grossRevenue), 2)                                           AS gross_revenue,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate_pct,
            ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)     AS return_rate_pct
        FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
        WHERE {order_filter}
        GROUP BY month ORDER BY month ASC
    """).result())

    res_trend = list(client.query(f"""
        SELECT
            FORMAT_DATE('%Y-%m', o.orderDate)                                       AS month,
            ROUND(SUM(o.grossRevenue), 2)                                           AS gross_revenue,
            ROUND(SUM(i.resolutionCost), 2)                                         AS resolution_cost,
            ROUND(SAFE_DIVIDE(SUM(i.resolutionCost), SUM(o.grossRevenue)) * 100, 2) AS resolution_cost_pct
        FROM `{GCP_PROJECT}.{BQ_DATASET}.orders` o
        LEFT JOIN `{GCP_PROJECT}.{BQ_DATASET}.incidents` i ON i.orderID = o.orderID
        WHERE {inc_filter}
        GROUP BY month ORDER BY month ASC
    """).result())

    suppliers = list(client.query(f"""
        SELECT
            o.supplierID,
            s.supplierName,
            s.supplierTier,
            COUNT(o.orderID)                                                      AS total_orders,
            ROUND(AVG(CASE WHEN o.hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)   AS incident_rate_pct,
            ROUND(AVG(CASE WHEN o.hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)   AS return_rate_pct,
            ROUND(SUM(CASE WHEN o.hasReturn THEN o.grossRevenue ELSE 0 END), 2)   AS returned_revenue
        FROM `{GCP_PROJECT}.{BQ_DATASET}.orders` o
        LEFT JOIN `{GCP_PROJECT}.{BQ_DATASET}.suppliers` s ON o.supplierID = s.supplierID
        WHERE {order_filter}
        GROUP BY o.supplierID, s.supplierName, s.supplierTier
        ORDER BY incident_rate_pct DESC LIMIT 10
    """).result())

    categories = list(client.query(f"""
        SELECT
            productCategory,
            COUNT(orderID)                                                        AS total_orders,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate_pct,
            ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)     AS return_rate_pct
        FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
        WHERE {order_filter}
        GROUP BY productCategory ORDER BY incident_rate_pct DESC
    """).result())

    res_mix = list(client.query(f"""
        SELECT
            i.incidentResolution,
            COUNT(i.incidentID)              AS total_incidents,
            ROUND(SUM(i.resolutionCost), 2)  AS total_cost
        FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
        INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
        WHERE {inc_filter}
        GROUP BY i.incidentResolution ORDER BY total_incidents DESC
    """).result())

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

@app.get("/api/dashboard/supplier/{supplier_id}")
def get_supplier_dashboard(
    supplier_id: str,
    date_from:   Optional[str] = None,
    date_to:     Optional[str] = None,
):
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

    sc = list(client.query(f"""
        SELECT
            COUNT(orderID)                                                        AS total_orders,
            ROUND(SUM(grossRevenue), 2)                                           AS total_gross_revenue,
            ROUND(SUM(productCost), 2)                                            AS total_product_cost,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate_pct,
            ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)     AS return_rate_pct,
            ROUND(SUM(CASE WHEN hasReturn   THEN grossRevenue ELSE 0 END), 2)     AS returned_revenue
        FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
        WHERE supplierID = '{supplier_id}' AND {order_filter}
    """).result())[0]

    res_cost = list(client.query(f"""
        SELECT ROUND(SUM(i.resolutionCost), 2) AS total_resolution_cost
        FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
        INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
        WHERE i.supplierID = '{supplier_id}' AND {inc_filter}
    """).result())[0]

    bench = list(client.query(f"""
        SELECT
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2) AS portfolio_incident_rate,
            ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2) AS portfolio_return_rate
        FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
        WHERE {order_filter}
    """).result())[0]

    bench_cost = list(client.query(f"""
        SELECT
            ROUND(SUM(i.resolutionCost), 2)    AS portfolio_resolution_cost,
            COUNT(DISTINCT i.supplierID)        AS supplier_count
        FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
        INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
        WHERE {inc_filter}
    """).result())[0]

    portfolio_avg_res_cost = (
        float(bench_cost["portfolio_resolution_cost"] or 0) /
        max(float(bench_cost["supplier_count"] or 1), 1)
    )

    cat_inc = list(client.query(f"""
        SELECT
            productCategory,
            COUNT(orderID)                                                        AS total_orders,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate_pct
        FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
        WHERE supplierID = '{supplier_id}' AND {order_filter}
        GROUP BY productCategory ORDER BY incident_rate_pct DESC
    """).result())

    cat_ret = list(client.query(f"""
        SELECT
            productCategory,
            COUNT(orderID)                                                        AS total_orders,
            ROUND(AVG(CASE WHEN hasReturn THEN 1.0 ELSE 0.0 END) * 100, 2)       AS return_rate_pct
        FROM `{GCP_PROJECT}.{BQ_DATASET}.orders`
        WHERE supplierID = '{supplier_id}' AND {order_filter}
        GROUP BY productCategory ORDER BY return_rate_pct DESC
    """).result())

    sku_inc = list(client.query(f"""
        SELECT
            i.productSKU,
            i.productCategory,
            i.incidentType,
            COUNT(i.incidentID)              AS total_incidents,
            ROUND(SUM(i.resolutionCost), 2)  AS total_resolution_cost,
            ROUND(AVG(i.resolutionCost), 2)  AS avg_resolution_cost,
            ROUND(AVG(i.productRating), 2)   AS avg_product_rating
        FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
        INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
        WHERE i.supplierID = '{supplier_id}' AND {inc_filter}
        GROUP BY i.productSKU, i.productCategory, i.incidentType
        ORDER BY total_incidents DESC LIMIT 100
    """).result())

    sku_ret = list(client.query(f"""
        SELECT
            r.productSKU,
            r.productCategory,
            r.buyersRemorseReason,
            COUNT(r.returnID)                AS total_returns,
            ROUND(AVG(r.productRating), 2)   AS avg_product_rating
        FROM `{GCP_PROJECT}.{BQ_DATASET}.returns` r
        INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON r.orderID = o.orderID
        WHERE r.supplierID = '{supplier_id}' AND {inc_filter}
        GROUP BY r.productSKU, r.productCategory, r.buyersRemorseReason
        ORDER BY total_returns DESC LIMIT 100
    """).result())

    ret_reasons = list(client.query(f"""
        SELECT
            r.buyersRemorseReason,
            COUNT(r.returnID)                AS total_returns,
            ROUND(AVG(r.productRating), 2)   AS avg_product_rating
        FROM `{GCP_PROJECT}.{BQ_DATASET}.returns` r
        INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON r.orderID = o.orderID
        WHERE r.supplierID = '{supplier_id}' AND {inc_filter}
        GROUP BY r.buyersRemorseReason ORDER BY total_returns DESC
    """).result())

    inc_types = list(client.query(f"""
        SELECT
            i.incidentType,
            COUNT(i.incidentID)              AS total_incidents,
            ROUND(SUM(i.resolutionCost), 2)  AS total_cost
        FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
        INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
        WHERE i.supplierID = '{supplier_id}' AND {inc_filter}
        GROUP BY i.incidentType ORDER BY total_incidents DESC
    """).result())

    res_mix = list(client.query(f"""
        SELECT
            i.incidentResolution,
            COUNT(i.incidentID)              AS total_incidents,
            ROUND(SUM(i.resolutionCost), 2)  AS total_cost
        FROM `{GCP_PROJECT}.{BQ_DATASET}.incidents` i
        INNER JOIN `{GCP_PROJECT}.{BQ_DATASET}.orders` o ON i.orderID = o.orderID
        WHERE i.supplierID = '{supplier_id}' AND {inc_filter}
        GROUP BY i.incidentResolution ORDER BY total_incidents DESC
    """).result())

    reports = list(client.query(f"""
        SELECT reportNarrative, reportDate, confidence, approvedBy, approvedAt, reportType
        FROM `{GCP_PROJECT}.{BQ_DATASET}.approved_reports`
        WHERE supplierID = '{supplier_id}' AND audience = 'supplier'
        ORDER BY reportDate DESC LIMIT 5
    """).result())

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
        "cat_incident_rate": [_row_to_dict(r) for r in cat_inc],
        "cat_return_rate":   [_row_to_dict(r) for r in cat_ret],
        "sku_incidents":     [_row_to_dict(r) for r in sku_inc],
        "sku_returns":       [_row_to_dict(r) for r in sku_ret],
        "return_reasons":    [_row_to_dict(r) for r in ret_reasons],
        "incident_types":    [_row_to_dict(r) for r in inc_types],
        "resolution_mix":    [_row_to_dict(r) for r in res_mix],
        "reports":           [_row_to_dict(r) for r in reports],
    }


# ── POST /api/runs ────────────────────────────────────────────────────────────

@app.post("/api/runs")
def trigger_run(body: RunRequest):
    import threading

    run_id = str(uuid.uuid4())

    def _run():
        try:
            _run_agent(
                report_type = body.reportType,
                goal        = body.goal,
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


# ── POST /api/ask ─────────────────────────────────────────────────────────────

@app.post("/api/ask")
def ask_question(body: AskRequest):
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

    supplier_context = f"\nScope all queries to supplierID = '{body.supplierID}' only." if body.supplierID else ""

    system_prompt = f"""You are a BigQuery SQL analyst for a supplier performance system.
Translate the user's question into valid BigQuery SQL.

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

Available tables:
{chr(10).join(table_context)}"""

    client_ai  = Anthropic(api_key=api_key)
    last_error = None
    sql        = None
    data       = []

    for attempt in range(3):
        messages = [{"role": "user", "content": body.question}]
        if last_error and sql:
            messages += [
                {"role": "assistant", "content": sql},
                {"role": "user", "content": f"That SQL failed: {last_error}\n\nFix and return only the corrected SQL."},
            ]

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
                raise HTTPException(
                    status_code=400,
                    detail="I wasn't able to answer that question. Try rephrasing it or being more specific about the time period or metric."
                )

    try:
        bq().insert_rows_json(f"{GCP_PROJECT}.{BQ_DATASET}.agent_runs", [{
            "runID":       str(uuid.uuid4()),
            "reportType":  "nl_query",
            "audience":    "supplier" if body.supplierID else "business",
            "supplierID":  body.supplierID,
            "goal":        body.question,
            "startedAt":   datetime.now(timezone.utc).isoformat(),
            "completedAt": datetime.now(timezone.utc).isoformat(),
            "status":      "completed",
            "queries":     json.dumps({"nl_query": sql}),
            "reportDate":  datetime.now(timezone.utc).date().isoformat(),
        }])
    except Exception:
        pass

    return {"question": body.question, "sql": sql, "data": data, "rows": len(data)}


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
