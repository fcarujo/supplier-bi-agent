"""
Supplier BI Agent — BigQuery Setup
====================================
Creates the dataset, tables, and row-level access policies.
Run this ONCE before running seed_data.py.

Usage:
    python setup_bigquery.py --project YOUR_PROJECT --dataset supplier_bi
"""

import argparse
import sys

try:
    from google.cloud import bigquery
except ImportError:
    print("ERROR: google-cloud-bigquery not installed.")
    print("  Run: pip install google-cloud-bigquery")
    sys.exit(1)


# ── Schemas ───────────────────────────────────────────────────────────────────

SCHEMAS = {
    "suppliers": [
        bigquery.SchemaField("supplierID",         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierName",       "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierRegion",     "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierTier",       "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("categorySpeciality", "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("onboardingDate",     "DATE",    mode="REQUIRED"),
    ],
    "orders": [
        bigquery.SchemaField("orderID",           "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierID",         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("customerID",         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productSKU",         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productCategory",    "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("orderDate",          "DATE",    mode="REQUIRED"),
        bigquery.SchemaField("unitQuantity",       "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("grossRevenue",       "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("netRevenue",         "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("productCost",        "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("fulfilmentChannel",  "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierTier",       "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierRegion",     "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productPriceTier",   "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("hasIncident",        "BOOLEAN", mode="REQUIRED"),
        bigquery.SchemaField("hasReturn",          "BOOLEAN", mode="REQUIRED"),
    ],
    "incidents": [
        bigquery.SchemaField("incidentID",                      "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("orderID",                         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierID",                      "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productSKU",                      "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productCategory",                 "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("incidentDate",                    "DATE",    mode="REQUIRED"),
        bigquery.SchemaField("resolutionDate",                  "DATE",    mode="NULLABLE"),
        bigquery.SchemaField("resolutionStatus",                "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("daysBetweenPurchaseAndIncident",  "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("incidentType",                    "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("incidentResolution",              "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("resolutionCost",                  "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("incidentCustomerComment",         "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productRating",                   "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("customerReview",                  "STRING",  mode="REQUIRED"),
    ],
    "returns": [
        bigquery.SchemaField("returnID",                       "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("orderID",                        "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("supplierID",                     "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productSKU",                     "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productCategory",                "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("returnDate",                     "DATE",    mode="REQUIRED"),
        bigquery.SchemaField("resolutionDate",                 "DATE",    mode="NULLABLE"),
        bigquery.SchemaField("resolutionStatus",               "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("daysBetweenPurchaseAndReturn",   "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("buyersRemorseReason",            "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("buyersRemorseComment",           "STRING",  mode="REQUIRED"),
        bigquery.SchemaField("productRating",                  "INTEGER", mode="REQUIRED"),
        bigquery.SchemaField("customerReview",                 "STRING",  mode="REQUIRED"),
    ],
    "approved_reports": [
        bigquery.SchemaField("reportID",        "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("supplierID",      "STRING",    mode="NULLABLE"),  # null = business overview
        bigquery.SchemaField("reportType",      "STRING",    mode="REQUIRED"),  # weekly_overview / supplier_account / adhoc
        bigquery.SchemaField("audience",        "STRING",    mode="REQUIRED"),  # business / supplier
        bigquery.SchemaField("reportDate",      "DATE",      mode="REQUIRED"),
        bigquery.SchemaField("approvedAt",      "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("approvedBy",      "STRING",    mode="REQUIRED"),
        bigquery.SchemaField("reportJSON",      "JSON",      mode="REQUIRED"),  # full structured report
        bigquery.SchemaField("reportNarrative", "STRING",    mode="REQUIRED"),  # human-readable text
        bigquery.SchemaField("confidence",      "FLOAT64",   mode="REQUIRED"),
        bigquery.SchemaField("gcsPath",         "STRING",    mode="REQUIRED"),  # path to full report in GCS
        bigquery.SchemaField("agentRunID",      "STRING",    mode="REQUIRED"),  # LangSmith trace ID
    ],
}

# Partition configs
PARTITION_CONFIG = {
    "orders":            bigquery.TimePartitioning(field="orderDate"),
    "incidents":         bigquery.TimePartitioning(field="incidentDate"),
    "returns":           bigquery.TimePartitioning(field="returnDate"),
    "approved_reports":  bigquery.TimePartitioning(field="reportDate"),
}

# Clustering configs (improves query performance and cost)
CLUSTERING_CONFIG = {
    "orders":    ["supplierID", "productCategory", "productPriceTier"],
    "incidents": ["supplierID", "incidentType", "resolutionStatus"],
    "returns":   ["supplierID", "buyersRemorseReason", "resolutionStatus"],
    "approved_reports": ["supplierID", "reportType", "audience"],
}


def create_dataset(client, project, dataset_id, location="EU"):
    dataset_ref = bigquery.Dataset(f"{project}.{dataset_id}")
    dataset_ref.location = location
    dataset_ref.description = "Supplier BI Agent — incident and returns analytics"
    try:
        dataset = client.create_dataset(dataset_ref, exists_ok=True)
        print(f"  ✓ Dataset: {project}.{dataset_id} (location: {location})")
        return dataset
    except Exception as e:
        print(f"  ✗ Dataset creation failed: {e}")
        raise


def create_table(client, project, dataset_id, table_name):
    table_ref = f"{project}.{dataset_id}.{table_name}"
    schema    = SCHEMAS[table_name]

    table = bigquery.Table(table_ref, schema=schema)

    if table_name in PARTITION_CONFIG:
        table.time_partitioning = PARTITION_CONFIG[table_name]

    if table_name in CLUSTERING_CONFIG:
        table.clustering_fields = CLUSTERING_CONFIG[table_name]

    table.description = f"Supplier BI Agent — {table_name} table"

    try:
        table = client.create_table(table, exists_ok=True)
        partitioned = "partitioned" if table_name in PARTITION_CONFIG else "unpartitioned"
        print(f"  ✓ Table: {table_ref} ({partitioned})")
    except Exception as e:
        print(f"  ✗ Table creation failed for {table_name}: {e}")
        raise


def setup_row_level_security(client, project, dataset_id):
    """
    Sets up BigQuery row-level security policies on supplier-scoped tables.

    For the portal (Phase 7): each supplier's service account or Firebase Auth
    user will be added to their policy tag group, restricting them to their
    own supplierID rows.

    This creates the policy structure — you assign specific users/service
    accounts to policies when you onboard each supplier in Phase 7.

    Note: Row Access Policies require the BigQuery Data Policy API.
    Enable it with: gcloud services enable bigquerydatapolicy.googleapis.com
    """
    print("\n  Row-level security policies (Phase 7 prep):")
    print("  ─────────────────────────────────────────────")
    print("  The following SQL creates a template policy for SUP001.")
    print("  Replicate for each supplier when building the portal.")
    print()

    # Print the SQL template — run manually in BigQuery console or via bq CLI
    for table in ["orders", "incidents", "returns"]:
        sql = f"""
-- Row Access Policy for {table} table — SUP001 example
-- Replace SUP001 and the service account with each supplier's values
-- Run this in BigQuery console for each supplier you onboard

CREATE OR REPLACE ROW ACCESS POLICY supplier_SUP001_policy
ON `{project}.{dataset_id}.{table}`
GRANT TO ("serviceAccount:sup001-portal@{project}.iam.gserviceaccount.com",
           "user:supplier001@yourdomain.com")
FILTER USING (supplierID = 'SUP001');

-- Internal team policy (sees all rows)
CREATE OR REPLACE ROW ACCESS POLICY internal_team_policy
ON `{project}.{dataset_id}.{table}`
GRANT TO ("group:bi-team@yourdomain.com",
           "serviceAccount:bi-agent@{project}.iam.gserviceaccount.com")
FILTER USING (TRUE);
"""
        print(f"  -- {table.upper()} TABLE:")
        print(sql)

    print("  Copy these SQL statements and run them in BigQuery console.")
    print("  Or use: bq query --use_legacy_sql=false '<SQL>'")


def print_next_steps(project, dataset_id):
    print("\n" + "═" * 60)
    print("NEXT STEPS")
    print("═" * 60)
    print("""
1. Run the seed data generator:
   python seed_data.py --output bigquery \\
       --project """ + project + """ \\
       --dataset """ + dataset_id + """

2. Or generate CSVs first and load via console:
   python seed_data.py --output csv
   # Then upload CSVs in BigQuery console

3. Deploy the daily append Cloud Function:
   cd daily_append/
   gcloud functions deploy supplier-bi-daily-append \\
       --gen2 \\
       --runtime python311 \\
       --region europe-west2 \\
       --source . \\
       --entry-point append_daily \\
       --trigger-topic supplier-bi-daily-trigger \\
       --memory 512MB \\
       --timeout 120s \\
       --set-env-vars GCP_PROJECT=""" + project + """,BQ_DATASET=""" + dataset_id + """

4. Create the Pub/Sub topic:
   gcloud pubsub topics create supplier-bi-daily-trigger

5. Create the Cloud Scheduler job:
   gcloud scheduler jobs create pubsub supplier-bi-daily \\
       --schedule "0 6 * * *" \\
       --topic supplier-bi-daily-trigger \\
       --message-body '{"source":"scheduler"}' \\
       --time-zone "UTC"

6. (Phase 7 prep) Apply row-level security SQL shown above
   in the BigQuery console for each supplier you onboard.
""")


def main():
    parser = argparse.ArgumentParser(description="Supplier BI Agent — BigQuery Setup")
    parser.add_argument("--project",  required=True, help="GCP project ID")
    parser.add_argument("--dataset",  default="supplier_bi", help="BigQuery dataset name")
    parser.add_argument("--location", default="EU", help="Dataset location (default: EU)")
    parser.add_argument("--rls",      action="store_true", help="Print row-level security SQL")
    args = parser.parse_args()

    print("=" * 60)
    print("Supplier BI Agent — BigQuery Setup")
    print("=" * 60)
    print(f"  Project:  {args.project}")
    print(f"  Dataset:  {args.dataset}")
    print(f"  Location: {args.location}")
    print()

    client = bigquery.Client(project=args.project)

    print("Creating dataset...")
    create_dataset(client, args.project, args.dataset, args.location)

    print("\nCreating tables...")
    for table_name in ["suppliers", "orders", "incidents", "returns", "approved_reports"]:
        create_table(client, args.project, args.dataset, table_name)

    if args.rls:
        setup_row_level_security(client, args.project, args.dataset)

    print_next_steps(args.project, args.dataset)
    print("Setup complete.")


if __name__ == "__main__":
    main()
