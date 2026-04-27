"""
Supplier BI Agent — Node 2: Pull
==================================
Fetches aggregated data from BigQuery for each selected table.

SQL mode is determined by the report type in metadata.yaml:

  sql_mode: template  — uses the pre-defined SQL template from metadata.yaml
                        Zero LLM involvement. Used for all scheduled reports.
                        Consistent, tested, version-controlled.

  sql_mode: llm       — LLM generates SQL at runtime.
                        Used only for ad-hoc reports with open-ended goals.
                        Always requires human review.

Security:
  - All SQL (template or LLM-generated) is validated before execution
  - Supplier-scoped reports have {supplier_id} injected automatically
  - Raw row count is capped at max_raw_rows from metadata config
  - Results are serialised — LLM never sees raw row-level data
"""

import os
import re
from pathlib import Path

import yaml
from anthropic import Anthropic
from google.cloud import bigquery


# ── Load metadata config ──────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / "config" / "metadata.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── SQL template resolver ─────────────────────────────────────────────────────

def resolve_template(
    template:    str,
    project:     str,
    dataset:     str,
    supplier_id: str = None,
    date_from:   str = None,
    date_to:     str = None,
) -> str:
    """
    Fill in placeholders in a SQL template.
    {project}, {dataset}, {supplier_id}, {date_from}, {date_to}
    """
    sql = template.format(
        project     = project,
        dataset     = dataset,
        supplier_id = supplier_id or "",
        date_from   = date_from   or "",
        date_to     = date_to     or "",
    )
    return sql.strip()


# ── SQL validator ─────────────────────────────────────────────────────────────

def validate_sql(
    sql:             str,
    allowed_columns: list,
    table_name:      str,
    project:         str,
    dataset:         str,
) -> tuple:
    """
    Validate SQL before execution.
    Returns (is_valid, list_of_issues).
    Applies to both template and LLM-generated SQL.
    """
    issues    = []
    sql_upper = sql.upper()

    # Block dangerous operations
    dangerous = ["DROP", "DELETE", "UPDATE", "INSERT",
                 "CREATE", "ALTER", "TRUNCATE", "GRANT", "REVOKE"]
    for op in dangerous:
        if re.search(rf"\b{op}\b", sql_upper):
            issues.append(f"Dangerous SQL operation detected: {op}")

    # Confirm correct table is referenced
    expected = f"{project}.{dataset}.{table_name}".upper()
    if expected not in sql_upper:
        issues.append(
            f"SQL does not reference expected table "
            f"`{project}.{dataset}.{table_name}`"
        )

    # No SELECT *
    if re.search(r"SELECT\s+\*", sql_upper):
        issues.append("SQL uses SELECT * — explicit columns required")

    return len(issues) == 0, issues


def inject_supplier_filter(sql: str, supplier_id: str) -> str:
    """
    Safety net — inject supplier filter if template forgot to include it.
    Should not be needed for template SQL but catches edge cases.
    """
    if f"'{supplier_id}'" in sql:
        return sql  # already present

    filter_clause = f"supplierID = '{supplier_id}'"
    sql_upper     = sql.upper()

    if "WHERE" in sql_upper:
        where_pos = sql_upper.index("WHERE")
        return sql[:where_pos + 5] + f" {filter_clause} AND " + sql[where_pos + 6:]
    elif "GROUP BY" in sql_upper:
        group_pos = sql_upper.index("GROUP BY")
        return sql[:group_pos] + f"WHERE {filter_clause}\n" + sql[group_pos:]
    else:
        return sql.rstrip().rstrip(";") + f"\nWHERE {filter_clause}"


# ── LLM SQL generator (ad-hoc only) ──────────────────────────────────────────

def generate_sql_llm(
    client:       Anthropic,
    table_name:   str,
    table_schema: dict,
    goal:         str,
    report_type:  str,
    audience:     str,
    supplier_id:  str,
    project:      str,
    dataset:      str,
    date_from:    str,
    date_to:      str,
) -> str:
    """
    LLM call — generate a simple flat SQL aggregation for ad-hoc reports only.
    Constrained to prevent complex queries that cause BigQuery errors.
    """
    allowed_cols    = table_schema["allowed_columns"]
    filters         = table_schema.get("filters", [])
    supplier_scoped = table_schema.get("supplier_scoped", False)

    date_context = ""
    if date_from and date_to:
        date_context = f"Date range to apply: {date_from} to {date_to}"
    elif filters:
        date_context = f"Default date filters: {'; '.join(filters)}"

    supplier_context = ""
    if supplier_scoped and supplier_id:
        supplier_context = (
            f"\nREQUIRED: Include WHERE supplierID = '{supplier_id}' in the query."
        )

    system_prompt = f"""You are the Pull node of a supplier BI agent writing SQL for an ad-hoc report.
Write ONE simple flat BigQuery SQL aggregation query.

STRICT RULES:
- Use ONLY the allowed columns listed below — no others exist
- NO SELECT * — name every column explicitly
- NO CTEs (WITH clauses)
- NO window functions (OVER, RANK, ROW_NUMBER, PARTITION BY)
- NO UNION or UNION ALL
- NO subqueries
- ONE flat SELECT ... FROM ... WHERE ... GROUP BY ... ORDER BY
- Maximum 12 columns in SELECT
- Always use full table path: `{project}.{dataset}.{table_name}`
- Return ONLY the SQL — no explanation, no markdown{supplier_context}
- Never use SUM(bool_column) — BigQuery does not support it. Always use SUM(CASE WHEN bool_column THEN 1 ELSE 0 END)
- hasIncident and hasReturn are BOOL columns — never SUM them directly

The Analyse node handles all cross-table logic. Your job is one clean flat aggregation.

Allowed columns:
{', '.join(allowed_cols)}

{date_context}"""

    user_prompt = f"""Ad-hoc report goal: {goal}

Table: {table_name}
Audience: {audience}
{"Supplier filter required: " + supplier_id if supplier_scoped and supplier_id else ""}

Write a single flat GROUP BY aggregation. Return only the SQL."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    sql = response.content[0].text.strip()

    # Strip markdown if present
    if sql.startswith("```"):
        sql = sql.split("```")[1]
        if sql.lower().startswith("sql"):
            sql = sql[3:]
    return sql.strip()


# ── Pull node ─────────────────────────────────────────────────────────────────

def pull_node(state: dict) -> dict:
    """
    Node 2 — Pull.

    Reads:  state.selected_tables, state.table_schemas, state.goal,
            state.report_type, state.audience, state.supplier_id,
            state.date_from, state.date_to

    Writes: state.queries, state.query_results, state.row_counts,
            state.pull_validation, state.errors
    """
    print("  [pull] Starting...")

    config          = _load_config()
    selected_tables = state["selected_tables"]
    table_schemas   = state["table_schemas"]
    goal            = state["goal"]
    report_type     = state["report_type"]
    audience        = state["audience"]
    supplier_id     = state.get("supplier_id")
    date_from       = state.get("date_from")
    date_to         = state.get("date_to")
    errors          = list(state.get("errors") or [])

    project         = config["project"]
    dataset         = config["dataset"]
    max_raw_rows    = config["security"]["max_raw_rows"]
    min_rows        = config["security"]["min_rows"]
    report_config   = config["reports"][report_type]
    sql_mode        = report_config.get("sql_mode", "template")

    print(f"  [pull] SQL mode: {sql_mode}")

    # Only initialise Claude client for ad-hoc reports
    claude_client = None
    if sql_mode == "llm":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set.")
        claude_client = Anthropic(api_key=api_key)

    bq_client       = bigquery.Client(project=project)
    queries         = {}
    query_results   = {}
    row_counts      = {}
    pull_validation = {}

    for table_name in selected_tables:
        print(f"  [pull] Processing table: {table_name}")
        table_schema  = table_schemas[table_name]
        report_table  = report_config["tables"].get(table_name, {})

        # ── Resolve SQL ───────────────────────────────────────────────────────
        if sql_mode == "template":
            template = report_table.get("sql_template")
            if not template:
                error_msg = f"No sql_template defined for {table_name} in {report_type}"
                print(f"  [pull] ERROR — {error_msg}")
                errors.append(error_msg)
                pull_validation[table_name] = {"status": "error", "warnings": [error_msg]}
                continue

            print(f"  [pull] Using SQL template for {table_name}")
            sql = resolve_template(
                template    = template,
                project     = project,
                dataset     = dataset,
                supplier_id = supplier_id,
                date_from   = date_from,
                date_to     = date_to,
            )

        else:  # sql_mode == "llm"
            print(f"  [pull] Generating SQL via LLM for {table_name}...")
            sql = generate_sql_llm(
                client       = claude_client,
                table_name   = table_name,
                table_schema = table_schema,
                goal         = goal,
                report_type  = report_type,
                audience     = audience,
                supplier_id  = supplier_id,
                project      = project,
                dataset      = dataset,
                date_from    = date_from,
                date_to      = date_to,
            )

        # ── Safety net: inject supplier filter if scoped and missing ──────────
        if report_config.get("supplier_scoped") and supplier_id:
            sql = inject_supplier_filter(sql, supplier_id)

        queries[table_name] = sql
        print(f"  [pull] SQL for {table_name}:\n{sql}\n")

        # ── Validate SQL ──────────────────────────────────────────────────────
        is_valid, sql_issues = validate_sql(
            sql, table_schema["allowed_columns"],
            table_name, project, dataset
        )

        if not is_valid:
            print(f"  [pull] SQL validation failed for {table_name}:")
            for issue in sql_issues:
                print(f"    - {issue}")
            errors.extend(sql_issues)
            pull_validation[table_name] = {
                "status":   "failed",
                "warnings": sql_issues,
            }
            continue

        # ── Execute on BigQuery ───────────────────────────────────────────────
        print(f"  [pull] Executing on BigQuery...")
        try:
            limited_sql = f"SELECT * FROM ({sql}) LIMIT {max_raw_rows}"
            rows        = list(bq_client.query(limited_sql).result())

        except Exception as e:
            error_msg = f"BigQuery query failed for {table_name}: {str(e)}"
            print(f"  [pull] ERROR — {error_msg}")
            errors.append(error_msg)
            pull_validation[table_name] = {
                "status":   "error",
                "warnings": [error_msg],
            }
            continue

        row_count = len(rows)
        row_counts[table_name] = row_count
        print(f"  [pull] {row_count:,} rows returned")

        # ── Validate row count ────────────────────────────────────────────────
        warnings = []
        if row_count < min_rows and table_name != "suppliers":
            warnings.append(
                f"Low row count ({row_count}) — report may not be statistically meaningful"
            )
        if row_count >= max_raw_rows:
            warnings.append(
                f"Row count hit limit ({max_raw_rows}) — results may be truncated"
            )

        # ── Serialise results ─────────────────────────────────────────────────
        result_data = [dict(row) for row in rows]
        for record in result_data:
            for key, value in record.items():
                if hasattr(value, "isoformat"):
                    record[key] = value.isoformat()
                elif hasattr(value, "__float__"):
                    record[key] = float(value)

        query_results[table_name]   = result_data
        pull_validation[table_name] = {
            "status":    "ok",
            "warnings":  warnings,
            "row_count": row_count,
        }

        if warnings:
            for w in warnings:
                print(f"  [pull] Warning: {w}")
            errors.extend(warnings)

    print(f"  [pull] Complete — tables: {list(query_results.keys())}")

    return {
        "queries":         queries,
        "query_results":   query_results,
        "row_counts":      row_counts,
        "pull_validation": pull_validation,
        "current_node":    "pull",
        "errors":          errors,
    }