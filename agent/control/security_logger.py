"""
Supplier BI Agent — Security Event Logger
==========================================
Writes security events to the BigQuery security_events table.

Used by: discover.py, pull.py, main.py

Why a shared module:
  All three nodes need to write security events in the same format.
  Centralising here means one place to change the schema, one place
  to change the BigQuery project/dataset config, and consistent
  event types across the whole pipeline.

Event types:
  INJECTION_PATTERN_DETECTED  — suspicious text in user input (discover)
  PII_COLUMN_BLOCKED          — blocked column in LLM-generated SQL (pull)
  COLUMN_NOT_ON_ALLOWLIST     — column outside allowed set (pull)
  DANGEROUS_SQL_OPERATION     — DROP/DELETE/etc in generated SQL (pull)
  RATE_LIMIT_BREACH           — user exceeded request rate limit (main)

Severity levels:
  HIGH    — active attempt to extract PII or execute dangerous SQL
  MEDIUM  — policy violation that was caught and blocked
  LOW     — suspicious pattern flagged for monitoring
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from google.cloud import bigquery


# These match the values in metadata.yaml — kept in sync manually.
# If the project or dataset changes, update here once.
_PROJECT = "supplier-bi-agent-2025"
_DATASET = "supplier_bi"
_TABLE   = f"{_PROJECT}.{_DATASET}.security_events"


def log_security_event(
    event_type:   str,
    severity:     str,
    detail:       str,
    raw_content:  str        = "",
    run_id:       Optional[str] = None,
    user_uid:     Optional[str] = None,
    user_role:    Optional[str] = None,
    endpoint:     Optional[str] = None,
    source_node:  Optional[str] = None,
) -> None:
    """
    Write a security event to BigQuery.

    This function is intentionally fire-and-forget — it never raises
    an exception that would interrupt the pipeline. If the write fails
    (e.g. BigQuery is temporarily unavailable), the error is printed
    to stdout so it appears in Cloud Run logs, but the pipeline continues.

    Why fire-and-forget:
      A security logger that can crash the application creates a denial-of-
      service vector — an attacker could craft input that triggers the logger,
      causes it to fail, and crashes the pipeline. The pipeline's own error
      handling is the primary defence. The security log is an audit record,
      not a gate.
    """
    try:
        client = bigquery.Client(project=_PROJECT)
        row = {
            "eventID":     str(uuid.uuid4()),
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "runID":       run_id,
            "userUID":     user_uid,
            "userRole":    user_role,
            "eventType":   event_type,
            "severity":    severity,
            "detail":      detail,
            "rawContent":  raw_content[:1000],  # cap at 1000 chars — no need to store full payloads
            "endpoint":    endpoint,
            "sourceNode":  source_node,
        }
        errors = client.insert_rows_json(_TABLE, [row])
        if errors:
            print(f"  [security_logger] WARNING — failed to write event: {errors}")
        else:
            print(f"  [security_logger] Event logged: {event_type} | severity={severity}")

    except Exception as e:
        # Never let logging failure interrupt the pipeline
        print(f"  [security_logger] WARNING — exception writing security event: {e}")
