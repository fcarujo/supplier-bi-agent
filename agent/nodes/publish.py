"""
Supplier BI Agent — Node 6: Publish
======================================
Writes approved reports to:
  - GCS (markdown report file)
  - BigQuery approved_reports table (structured JSON for Looker Studio)
  - BigQuery agent_runs status update
Only runs if state.approved == True.
"""

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from google.cloud import bigquery, storage


CONFIG_PATH = Path(__file__).parent.parent / "config" / "metadata.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def publish_node(state: dict) -> dict:
    """
    Node 6 — Publish.

    Reads:  state.approved, state.report_narrative, state.report_json,
            state.run_id, state.report_type, state.audience, state.supplier_id
    Writes: state.gcs_path, state.current_node, state.errors
    """
    print("  [publish] Starting...")

    if not state.get("approved"):
        print("  [publish] Report not approved — skipping publish")
        return {"current_node": "publish"}

    config      = _load_config()
    project     = config["project"]
    dataset     = config["dataset"]
    report_type = state["report_type"]
    audience    = state["audience"]
    supplier_id = state.get("supplier_id")
    run_id      = state.get("run_id", str(uuid.uuid4()))
    narrative   = state.get("report_narrative", "")
    report_json = state.get("report_json", {})
    errors      = list(state.get("errors") or [])
    gcs_path    = None

    # ── Write to GCS ──────────────────────────────────────────────────────────
    try:
        bucket_name = f"{project}-approved-reports"
        gcs_client  = storage.Client(project=project)
        bucket      = gcs_client.bucket(bucket_name)
        today       = date.today().isoformat()
        scope       = supplier_id if supplier_id else "portfolio"
        blob_name   = f"{today}/{report_type}/{scope}/{run_id}.md"
        blob        = bucket.blob(blob_name)
        blob.upload_from_string(narrative, content_type="text/markdown")
        gcs_path    = f"gs://{bucket_name}/{blob_name}"
        print(f"  [publish] Written to GCS: {gcs_path}")

    except Exception as e:
        error_msg = f"GCS write failed: {str(e)}"
        print(f"  [publish] WARNING — {error_msg}")
        errors.append(error_msg)

    # ── Write to BigQuery approved_reports ────────────────────────────────────
    try:
        bq_client = bigquery.Client(project=project)
        table_id  = f"{project}.{dataset}.approved_reports"

        row = {
            "reportID":        run_id,
            "supplierID":      supplier_id,
            "reportType":      report_type,
            "audience":        audience,
            "reportDate":      date.today().isoformat(),
            "approvedAt":      state.get("approved_at", datetime.now(timezone.utc).isoformat()),
            "approvedBy":      state.get("reviewer", "system"),
            "reportJSON":      json.dumps(report_json, default=str),
            "reportNarrative": narrative,
            "confidence":      state.get("confidence", 0.0),
            "gcsPath":         gcs_path or "",
            "agentRunID":      run_id,
        }

        errs = bq_client.insert_rows_json(table_id, [row])
        if errs:
            print(f"  [publish] WARNING — approved_reports write failed: {errs}")
        else:
            print(f"  [publish] Written to approved_reports table")

    except Exception as e:
        error_msg = f"BigQuery approved_reports write failed: {str(e)}"
        print(f"  [publish] WARNING — {error_msg}")
        errors.append(error_msg)

    # ── Update agent_runs status ──────────────────────────────────────────────
    # BigQuery streaming buffer does not support UPDATE immediately after INSERT.
    # Insert a new row with status=approved instead — the status endpoint reads
    # the most recent row so this correctly reflects the final state.
    try:
        bq_client = bigquery.Client(project=project)
        errs = bq_client.insert_rows_json(
            f"{project}.{dataset}.agent_runs",
            [{
                "runID":          run_id,
                "reportType":     report_type,
                "audience":       audience,
                "supplierID":     supplier_id,
                "status":         "approved",
                "confidence":     state.get("confidence"),
                "gcsPath":        gcs_path or "",
                "completedAt":    datetime.now(timezone.utc).isoformat(),
                "startedAt":      datetime.now(timezone.utc).isoformat(),
                "reportDate":     date.today().isoformat(),
                "policyDecision": "auto_approve",
                "flags":          json.dumps(state.get("flags") or []),
                "errors":         json.dumps(errors),
                "selectedTables": json.dumps(state.get("selected_tables") or []),
                "queries":        json.dumps(state.get("queries") or {}),
                "rowCounts":      json.dumps(state.get("row_counts") or {}),
                "pullValidation": json.dumps(state.get("pull_validation") or {}),
                "goal":           state.get("goal", ""),
            }]
        )
        if errs:
            print(f"  [publish] WARNING — agent_runs status insert failed: {errs}")
        else:
            print(f"  [publish] agent_runs status inserted as approved")

    except Exception as e:
        print(f"  [publish] WARNING — agent_runs status insert failed: {e}")

    print(f"  [publish] Complete")

    return {
        "gcs_path":     gcs_path,
        "current_node": "publish",
        "errors":       errors,
    }
