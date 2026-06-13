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
    Safety net — inject supplier filter and correct any truncated supplier IDs.
    """
    import re as _re
    # Correct any malformed supplierID value in the SQL (e.g. SU004 -> SUP004)
    sql = _re.sub(
        r"supplierID\s*=\s*'[^']*'",
        f"supplierID = '{supplier_id}'",
        sql
    )
    if f"'{supplier_id}'" in sql:
        return sql

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
    client:         Anthropic,
    table_name:     str,
    table_schema:   dict,
    goal:           str,
    report_type:    str,
    audience:       str,
    supplier_id:    str,
    project:        str,
    dataset:        str,
    date_from:      str,
    date_to:        str,
    column_schemas: dict = None,
) -> str:
    """
    LLM call — generate SQL for ad-hoc reports using enriched column schema.
    Column descriptions prevent invented column names and type errors.
    """
    allowed_cols    = table_schema["allowed_columns"]
    filters         = table_schema.get("filters", [])
    supplier_scoped = table_schema.get("supplier_scoped", False)

    date_context = ""
    if date_from and date_to:
        date_context = f"Date range requested: {date_from} to {date_to}"
    elif filters:
        date_context = f"Default date filters: {'; '.join(filters)}"

    supplier_context = ""
    if supplier_scoped and supplier_id:
        supplier_context = (
            f"\nREQUIRED: Always include WHERE supplierID = \'{supplier_id}\' "
            f"in the query. Use this EXACT string — do not shorten or modify it."
        )

    # Build enriched column descriptions from schema
    col_lines = []
    if column_schemas and table_name in column_schemas:
        schema_cols = column_schemas[table_name].get("columns", {})
        for col in allowed_cols:
            if col in schema_cols:
                info = schema_cols[col]
                col_lines.append(f"  {col} ({info['type']}): {info['description']}")
            else:
                col_lines.append(f"  {col}")
    else:
        col_lines = [f"  {c}" for c in allowed_cols]

    columns_context = "\n".join(col_lines)

    system_prompt = (
        "You are the Pull node of a supplier BI agent writing SQL for BigQuery.\n"
        "Write ONE SQL aggregation query that answers the report goal.\n"
        "\n"
        "STRICT RULES:\n"
        "- Use ONLY the exact column names from the schema below — do not invent names\n"
        "- NO SELECT * — name every column explicitly\n"
        "- NO window functions (OVER, RANK, ROW_NUMBER, PARTITION BY)\n"
        "- NO UNION or UNION ALL\n"
        "- NO subqueries in FROM clause\n"
        "- Maximum 15 columns in SELECT\n"
        f"- Always use full table path: `{project}.{dataset}.{table_name}`\n"
        "- Return ONLY the SQL — no explanation, no markdown, no code fences\n"
        + supplier_context + "\n"
        "\n"
        "MANDATORY OUTPUT COLUMNS (orders table):\n"
        "When querying the orders table you MUST include these exact aliases so the\n"
        "downstream analysis can read them. Use these names exactly:\n"
        "  COUNT(orderID) AS total_orders\n"
        "  SUM(grossRevenue) AS total_gross_revenue\n"
        "  SUM(CASE WHEN hasIncident THEN 1 ELSE 0 END) AS total_incidents\n"
        "  SUM(CASE WHEN hasReturn   THEN 1 ELSE 0 END) AS total_returns\n"
        "You MAY add extra breakdown columns (e.g. productCategory) and GROUP BY them,\n"
        "but the four canonical aggregates above must always be present.\n"
        "Do NOT pre-compute rates as percentages — return the raw counts above; the\n"
        "analysis layer computes rates itself.\n"
        "\n"
        "PERIOD COMPARISONS — when comparing time periods, suffix each canonical\n"
        "column with the year using conditional aggregation. Use EXACTLY these names:\n"
        "  SELECT\n"
        "    COUNT(CASE WHEN EXTRACT(YEAR FROM orderDate) = 2026 THEN orderID END) AS total_orders_2026,\n"
        "    SUM(CASE WHEN EXTRACT(YEAR FROM orderDate) = 2026 THEN grossRevenue ELSE 0 END) AS total_gross_revenue_2026,\n"
        "    SUM(CASE WHEN EXTRACT(YEAR FROM orderDate) = 2026 AND hasIncident THEN 1 ELSE 0 END) AS total_incidents_2026,\n"
        "    SUM(CASE WHEN EXTRACT(YEAR FROM orderDate) = 2026 AND hasReturn   THEN 1 ELSE 0 END) AS total_returns_2026,\n"
        "    COUNT(CASE WHEN EXTRACT(YEAR FROM orderDate) = 2025 THEN orderID END) AS total_orders_2025,\n"
        "    SUM(CASE WHEN EXTRACT(YEAR FROM orderDate) = 2025 THEN grossRevenue ELSE 0 END) AS total_gross_revenue_2025\n"
        f"  FROM `{project}.{dataset}.{table_name}`\n"
        "  WHERE orderDate >= '2025-01-01' AND orderDate <= CURRENT_DATE()\n"
        "\n"
        "DATE SCOPING: WHERE clause must cover ALL periods being compared.\n"
        "SINGLE PERIOD: for one period (e.g. 'sales in 2026'), do NOT use year suffixes —\n"
        "use the plain canonical names and a WHERE clause bounding that period, e.g.\n"
        "  WHERE orderDate >= '2026-01-01' AND orderDate <= '2026-12-31'\n"
        f"\nCOLUMN SCHEMA for `{project}.{dataset}.{table_name}`:\n"
        + columns_context + "\n"
        + ("\n" + date_context if date_context else "")
    )

    # Detect if this is a correction run and extract the reviewer's reason
    correction_note = ""
    if goal and "CORRECTION FROM REVIEWER:" in goal:
        import re as _re2
        m = _re2.search(r"CORRECTION FROM REVIEWER:(.+?)(?:Please fix|parentRunID)", goal, _re2.DOTALL)
        if m:
            correction_note = (
                "\n\nCRITICAL — THIS IS A CORRECTION RUN:\n"
                "The previous SQL was rejected by a human reviewer. "
                "The specific issue you MUST fix is:" + m.group(1).strip() + "\n"
                "Do NOT repeat the same mistake. Fix this exact issue in your SQL."
            )
        # Use only the original goal (before the correction block) for context
        original_goal = goal.split("\n\nCORRECTION FROM REVIEWER:")[0].strip()
    else:
        original_goal = goal

    user_prompt = (
        f"Report goal: {original_goal}\n"
        f"Table: {table_name}\n"
        f"Audience: {audience}\n"
        + (f"Supplier: {supplier_id}\n" if supplier_scoped and supplier_id else "")
        + correction_note
        + "\nWrite the SQL query. Return only the SQL, nothing else."
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}]
    )

    sql = response.content[0].text.strip()
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
            # Suppliers table is reference data — never use LLM, always simple SELECT
            if table_name == "suppliers":
                allowed_cols_list = table_schema["allowed_columns"]
                cols_str = ", ".join(allowed_cols_list)
                if supplier_id:
                    sql = f"SELECT {cols_str} FROM `{project}.{dataset}.suppliers` WHERE supplierID = '{supplier_id}'"
                else:
                    sql = f"SELECT {cols_str} FROM `{project}.{dataset}.suppliers` ORDER BY supplierID"
                print(f"  [pull] Using fixed SQL for suppliers table (reference data)")
            else:
                print(f"  [pull] Generating SQL via LLM for {table_name}...")
                sql = None
                for _attempt in range(3):
                    _sql = generate_sql_llm(
                        client         = claude_client,
                        table_name     = table_name,
                        table_schema   = table_schema,
                        goal           = goal,
                        report_type    = report_type,
                        audience       = audience,
                        supplier_id    = supplier_id,
                        project        = project,
                        dataset        = dataset,
                        date_from      = date_from,
                        date_to        = date_to,
                        column_schemas = config.get("column_schemas", {}),
                    )
                    _stripped = _sql.strip().upper()
                    if _stripped.startswith("SELECT") or _stripped.startswith("WITH"):
                        sql = _sql
                        break
                    print(f"  [pull] WARNING — non-SQL response on attempt {_attempt+1}, retrying...")
                if sql is None:
                    error_msg = f"LLM failed to produce valid SQL for {table_name} after 3 attempts"
                    print(f"  [pull] ERROR — {error_msg}")
                    errors.append(error_msg)
                    pull_validation[table_name] = {"status": "error", "warnings": [error_msg]}
                    continue

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
        rows = None
        last_bq_error = None

        for _correction in range(3):
            try:
                limited_sql = f"SELECT * FROM ({sql}) LIMIT {max_raw_rows}"
                rows        = list(bq_client.query(limited_sql).result())
                break

            except Exception as e:
                bq_error = str(e)
                last_bq_error = bq_error
                print(f"  [pull] BigQuery error (attempt {_correction+1}): {bq_error[:200]}")

                if _correction >= 2:
                    break
                if sql_mode != "llm" or table_name == "suppliers" or not claude_client:
                    break

                print(f"  [pull] Attempting SQL auto-correction...")
                allowed_cols_list = table_schema["allowed_columns"]
                col_schemas = config.get("column_schemas", {})
                col_lines = []
                if table_name in col_schemas:
                    for col in allowed_cols_list:
                        info = col_schemas[table_name]["columns"].get(col, {})
                        col_lines.append(
                            f"  {col} ({info.get('type','')}: {info.get('description','')})"
                            if info else f"  {col}"
                        )
                else:
                    col_lines = [f"  {c}" for c in allowed_cols_list]

                fix_prompt = (
                    "This BigQuery SQL failed. Fix it using ONLY the allowed columns listed.\n"
                    f"Error: {bq_error}\n\n"
                    f"Failing SQL:\n{sql}\n\n"
                    f"Allowed columns for {project}.{dataset}.{table_name}:\n"
                    + "\n".join(col_lines)
                    + "\n\nReturn ONLY the corrected SQL, nothing else."
                )
                try:
                    fix_resp = claude_client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=1500,
                        messages=[{"role": "user", "content": fix_prompt}]
                    )
                    fixed = fix_resp.content[0].text.strip()
                    if fixed.startswith("```"):
                        fixed = fixed.split("```")[1]
                        if fixed.lower().startswith("sql"):
                            fixed = fixed[3:]
                    fixed = fixed.strip()
                    if fixed.upper().startswith("SELECT") or fixed.upper().startswith("WITH"):
                        print(f"  [pull] SQL auto-corrected on attempt {_correction+1}")
                        sql = fixed
                        queries[table_name] = sql
                    else:
                        print(f"  [pull] Correction returned non-SQL, giving up")
                        break
                except Exception as fix_e:
                    print(f"  [pull] Correction call failed: {fix_e}")
                    break

        if rows is None:
            error_msg = f"BigQuery query failed for {table_name}: {last_bq_error}"
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