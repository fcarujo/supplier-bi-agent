"""
Supplier BI Agent — Control Store Setup
=========================================
Creates the three BigQuery tables that form the semantic control plane:

  agent_runs         — every pipeline execution, full trace
  validation_results — semantic validation outcomes per run
  human_decisions    — every human approve/reject/edit with reason

Run once before building Phase 4 nodes.

Usage:
    python data/seed/setup_control_store.py \
        --project supplier-bi-agent-2025 \
        --dataset supplier_bi
"""

import argparse
import sys

try:
    from google.cloud import bigquery
except ImportError:
    print("ERROR: google-cloud-bigquery not installed.")
    sys.exit(1)


SCHEMAS = {

    "agent_runs": [
        bigquery.SchemaField("runID",           "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("reportType",       "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("audience",         "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("supplierID",       "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("goal",             "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("startedAt",        "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("completedAt",      "TIMESTAMP", mode="NULLABLE"),
        bigquery.SchemaField("status",           "STRING",    mode="REQUIRED"),
        # status: running / completed / failed / pending_review / approved / rejected
        bigquery.SchemaField("confidence",       "FLOAT64",   mode="NULLABLE"),
        bigquery.SchemaField("flags",            "JSON",      mode="NULLABLE"),
        bigquery.SchemaField("selectedTables",   "JSON",      mode="NULLABLE"),
        bigquery.SchemaField("queries",          "JSON",      mode="NULLABLE"),
        bigquery.SchemaField("rowCounts",        "JSON",      mode="NULLABLE"),
        bigquery.SchemaField("pullValidation",   "JSON",      mode="NULLABLE"),
        bigquery.SchemaField("errors",           "JSON",      mode="NULLABLE"),
        bigquery.SchemaField("inputTokens",      "INTEGER",   mode="NULLABLE"),
        bigquery.SchemaField("outputTokens",     "INTEGER",   mode="NULLABLE"),
        bigquery.SchemaField("latencySeconds",   "FLOAT64",   mode="NULLABLE"),
        bigquery.SchemaField("policyDecision",   "STRING",    mode="NULLABLE"),
        # policy_decision: auto_approved / routed_to_queue / escalated
        bigquery.SchemaField("gcsPath",          "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("reportDate",       "DATE",      mode="NULLABLE"),
    ],

    "validation_results": [
        bigquery.SchemaField("validationID",     "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("runID",            "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("reportType",       "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("audience",         "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("supplierID",       "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("validatedAt",      "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("metricName",       "STRING",    mode="REQUIRED"),
        # e.g. overall_incident_rate, total_resolution_cost, return_rate_pct
        bigquery.SchemaField("expectedValue",    "FLOAT64",   mode="NULLABLE"),
        # value computed directly from BigQuery
        bigquery.SchemaField("reportedValue",    "FLOAT64",   mode="NULLABLE"),
        # value stated in the generated report
        bigquery.SchemaField("deviationPct",     "FLOAT64",   mode="NULLABLE"),
        # abs((reported - expected) / expected) * 100
        bigquery.SchemaField("passed",           "BOOLEAN",   mode="REQUIRED"),
        bigquery.SchemaField("hallucinationFlag","BOOLEAN",   mode="REQUIRED"),
        bigquery.SchemaField("details",          "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("category",         "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("supplierIDRef",    "STRING",    mode="NULLABLE"),
    ],

    "human_decisions": [
        bigquery.SchemaField("decisionID",       "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("runID",            "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("reportType",       "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("audience",         "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("supplierID",       "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("decidedAt",        "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("decision",         "STRING",    mode="REQUIRED"),
        # decision: approved / rejected / edited_and_approved
        bigquery.SchemaField("reviewer",         "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("reason",           "STRING",    mode="NULLABLE"),
        # required on rejection — fed back to Generate on retry
        bigquery.SchemaField("editedNarrative",  "STRING",    mode="NULLABLE"),
        # populated if decision = edited_and_approved
        bigquery.SchemaField("originalNarrative","STRING",    mode="NULLABLE"),
        bigquery.SchemaField("validationSummary","JSON",      mode="NULLABLE"),
        # snapshot of validation results at time of decision
        bigquery.SchemaField("policyOutcome",    "JSON",      mode="NULLABLE"),
        # snapshot of policy engine outcome at time of decision
        bigquery.SchemaField("retryTriggered",   "BOOLEAN",   mode="NULLABLE"),
        bigquery.SchemaField("retryRunID",       "STRING",    mode="NULLABLE"),
    ],

    "pending_reports": [
        bigquery.SchemaField("runID",            "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("reportType",       "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("audience",         "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("supplierID",       "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("queuedAt",         "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("status",           "STRING",    mode="REQUIRED"),
        # status: pending / in_review / decided
        bigquery.SchemaField("confidence",       "FLOAT64",   mode="NULLABLE"),
        bigquery.SchemaField("policyDecision",   "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("validationPassed", "INTEGER",   mode="NULLABLE"),
        bigquery.SchemaField("validationFailed", "INTEGER",   mode="NULLABLE"),
        bigquery.SchemaField("hallucinationFlags","INTEGER",  mode="NULLABLE"),
        bigquery.SchemaField("reportNarrative",  "STRING",    mode="NULLABLE"),
        bigquery.SchemaField("reportJSON",       "JSON",      mode="NULLABLE"),
        bigquery.SchemaField("errors",           "JSON",      mode="NULLABLE"),
    ],
}

PARTITION_CONFIG = {
    "agent_runs":         bigquery.TimePartitioning(field="startedAt"),
    "validation_results": bigquery.TimePartitioning(field="validatedAt"),
    "human_decisions":    bigquery.TimePartitioning(field="decidedAt"),
    "pending_reports":    bigquery.TimePartitioning(field="queuedAt"),
}

CLUSTERING_CONFIG = {
    "agent_runs":         ["reportType", "audience", "status"],
    "validation_results": ["runID", "passed", "hallucinationFlag"],
    "human_decisions":    ["runID", "decision", "reviewer"],
    "pending_reports":    ["status", "reportType", "audience"],
}


def create_table(client, project, dataset, table_name):
    table_ref = f"{project}.{dataset}.{table_name}"
    table     = bigquery.Table(table_ref, schema=SCHEMAS[table_name])

    if table_name in PARTITION_CONFIG:
        table.time_partitioning = PARTITION_CONFIG[table_name]
    if table_name in CLUSTERING_CONFIG:
        table.clustering_fields = CLUSTERING_CONFIG[table_name]

    table.description = f"Supplier BI Agent control store — {table_name}"

    client.create_table(table, exists_ok=True)
    print(f"  ✓ {table_ref}")


def main():
    parser = argparse.ArgumentParser(
        description="Supplier BI Agent — Control Store Setup"
    )
    parser.add_argument("--project",  required=True,          help="GCP project ID")
    parser.add_argument("--dataset",  default="supplier_bi",  help="BigQuery dataset")
    args = parser.parse_args()

    print("=" * 60)
    print("Supplier BI Agent — Control Store Setup")
    print("=" * 60)
    print(f"  Project: {args.project}")
    print(f"  Dataset: {args.dataset}")
    print()

    client = bigquery.Client(project=args.project)

    print("Creating control store tables...")
    for table_name in SCHEMAS:
        create_table(client, args.project, args.dataset, table_name)

    print(f"\nDone. {len(SCHEMAS)} tables created.")
    print("""
Next steps:
  1. Build policies.yaml — publish rules per report type
  2. Build validate_node — semantic validation against BigQuery
  3. Build policy_engine.py — deterministic rule evaluator
  4. Build review_node + publish_node
  5. Build React control plane
""")


if __name__ == "__main__":
    main()
