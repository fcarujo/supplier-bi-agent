"""
Supplier BI Agent — Node 5: Review
=====================================
Runs the policy engine against agent state + validation results.
Three outcomes:
  auto_approve    — all policies pass, pipeline continues to publish
  route_to_queue  — soft rule failures, writes to pending_reports queue
  escalate        — hard rule failures, writes with escalation flag
"""

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from google.cloud import bigquery

from agent.control.policy_engine import evaluate as evaluate_policy


CONFIG_PATH = Path(__file__).parent.parent / "config" / "metadata.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _write_to_pending_reports(
    bq_client:      bigquery.Client,
    project:        str,
    dataset:        str,
    state:          dict,
    policy_outcome: dict,
):
    table_id   = f"{project}.{dataset}.pending_reports"
    validation = state.get("validation", {})

    # Merge policy_outcome into reportJSON so frontend can display rule results
    report_json = state.get("report_json", {}) or {}
    if isinstance(report_json, str):
        try:
            report_json = json.loads(report_json)
        except Exception:
            report_json = {}
    report_json_with_policy = {**report_json, "policy_outcome": policy_outcome}

    row = {
        "runID":             state.get("run_id"),
        "reportType":        state["report_type"],
        "audience":          state["audience"],
        "supplierID":        state.get("supplier_id"),
        "queuedAt":          datetime.now(timezone.utc).isoformat(),
        "status":            "pending",
        "confidence":        state.get("confidence"),
        "policyDecision":    policy_outcome.get("decision"),
        "validationPassed":  validation.get("passed", 0),
        "validationFailed":  validation.get("failed", 0),
        "hallucinationFlags":validation.get("hallucination_flags", 0),
        "reportNarrative":   state.get("report_narrative"),
        "reportJSON":        json.dumps(report_json_with_policy, default=str),
        "errors":            json.dumps(state.get("errors", []), default=str),
    }

    errors = bq_client.insert_rows_json(table_id, [row])
    if errors:
        print(f"  [review] WARNING — failed to write to pending_reports: {errors}")
    else:
        print(f"  [review] Written to pending_reports queue: {state.get('run_id')}")


def _write_agent_run(
    bq_client:      bigquery.Client,
    project:        str,
    dataset:        str,
    state:          dict,
    status:         str,
    policy_outcome: dict,
    gcs_path:       str = None,
):
    table_id = f"{project}.{dataset}.agent_runs"

    row = {
        "runID":           state.get("run_id"),
        "reportType":      state["report_type"],
        "audience":        state["audience"],
        "supplierID":      state.get("supplier_id"),
        "goal":            state.get("goal", ""),
        "startedAt":       datetime.now(timezone.utc).isoformat(),
        "completedAt":     datetime.now(timezone.utc).isoformat(),
        "status":          status,
        "confidence":      state.get("confidence"),
        "flags":           json.dumps(state.get("flags", []), default=str),
        "selectedTables":  json.dumps(state.get("selected_tables", []), default=str),
        "queries":         json.dumps(state.get("queries", {}), default=str),
        "rowCounts":       json.dumps(state.get("row_counts", {}), default=str),
        "pullValidation":  json.dumps(state.get("pull_validation", {}), default=str),
        "errors":          json.dumps(state.get("errors", []), default=str),
        "policyDecision":  policy_outcome.get("decision"),
        "gcsPath":         gcs_path,
        "reportDate":      date.today().isoformat(),
    }

    errors = bq_client.insert_rows_json(table_id, [row])
    if errors:
        print(f"  [review] WARNING — failed to write agent_run: {errors}")


def review_node(state: dict) -> dict:
    """
    Node 5 — Review.

    Reads:  state.*, state.validation
    Writes: state.approved, state.policy_outcome, state.reviewer,
            state.review_notes, state.approved_at, state.errors
    """
    print("  [review] Starting...")

    config      = _load_config()
    project     = config["project"]
    dataset     = config["dataset"]
    report_type = state["report_type"]
    validation  = state.get("validation", {})
    errors      = list(state.get("errors") or [])

    bq_client   = bigquery.Client(project=project)

    # ── Run policy engine ─────────────────────────────────────────────────────
    print("  [review] Running policy engine...")
    outcome     = evaluate_policy(state, validation, report_type)
    policy_dict = outcome.to_dict()

    # ── Write agent run record ────────────────────────────────────────────────
    status_map = {
        "auto_approve":   "pending_publish",
        "route_to_queue": "pending_review",
        "escalate":       "escalated",
    }
    _write_agent_run(
        bq_client, project, dataset, state,
        status_map.get(outcome.decision, "pending_review"),
        policy_dict
    )

    # ── Handle decision ───────────────────────────────────────────────────────
    if outcome.decision == "auto_approve":
        print("  [review] AUTO-APPROVED — all policies passed")
        return {
            "approved":       True,
            "reviewer":       "system",
            "review_notes":   "Auto-approved by policy engine",
            "approved_at":    datetime.now(timezone.utc).isoformat(),
            "policy_outcome": policy_dict,
            "current_node":   "review",
            "errors":         errors,
        }

    # route_to_queue or escalate
    action = "ESCALATED" if outcome.decision == "escalate" else "ROUTED TO QUEUE"
    print(f"  [review] {action} — writing to pending_reports")
    _write_to_pending_reports(bq_client, project, dataset, state, policy_dict)

    return {
        "approved":       False,
        "reviewer":       None,
        "review_notes":   f"Pending human review — {outcome.decision}",
        "approved_at":    None,
        "policy_outcome": policy_dict,
        "current_node":   "review",
        "errors":         errors + outcome.hard_failures + outcome.soft_failures,
    }
