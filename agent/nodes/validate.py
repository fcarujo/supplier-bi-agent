"""
Supplier BI Agent — Node 4b: Validate
=======================================
Semantic validation layer — sits between Generate and Review.

Re-queries BigQuery directly for key metrics and compares them
against the figures stated in the generated report narrative.
Deviations above threshold are flagged as potential hallucinations.

For ad-hoc reports, extracts the actual date range and supplier scope
from the queries that were run, so ground truth matches the report scope.

No LLM involved — entirely deterministic.
"""

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from google.cloud import bigquery


CONFIG_PATH = Path(__file__).parent.parent / "config" / "metadata.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Date filter extraction ────────────────────────────────────────────────────

def _extract_date_filter_from_sql(sql: str, date_col: str = "orderDate") -> Optional[str]:
    """
    Extract the date filter from a SQL string.
    Returns a filter string using date_col as the column name.
    """
    if not sql:
        return None

    # Match DATE_SUB patterns
    patterns = [
        # DATE_SUB(CURRENT_DATE(), INTERVAL N MONTH/DAY)
        rf"{date_col}\s*>=\s*(DATE_SUB\(CURRENT_DATE\(\),\s*INTERVAL\s*\d+\s*(?:MONTH|DAY)\))",
        # DATE_TRUNC(DATE_SUB(...)) pattern
        rf"{date_col}\s*>=\s*(DATE_TRUNC\(DATE_SUB\(CURRENT_DATE\(\),\s*INTERVAL\s*\d+\s*(?:MONTH|DAY)\),\s*\w+\))",
        # Literal date strings
        rf"{date_col}\s*>=\s*'(\d{{4}}-\d{{2}}-\d{{2}})'",
    ]

    for pattern in patterns:
        match = re.search(pattern, sql, re.IGNORECASE)
        if match:
            return f"{date_col} >= {match.group(1)}"

    # Try without column prefix (incidents/returns tables)
    generic_patterns = [
        r"(?:orderDate|incidentDate|returnDate)\s*>=\s*(DATE_SUB\(CURRENT_DATE\(\),\s*INTERVAL\s*(\d+)\s*(MONTH|DAY)\))",
        r"(?:orderDate|incidentDate|returnDate)\s*>=\s*(DATE_TRUNC\(DATE_SUB\(CURRENT_DATE\(\),\s*INTERVAL\s*(\d+)\s*(MONTH|DAY)\),\s*\w+\))",
    ]
    for pattern in generic_patterns:
        match = re.search(pattern, sql, re.IGNORECASE)
        if match:
            interval_n    = match.group(2)
            interval_unit = match.group(3).upper()
            return f"{date_col} >= DATE_SUB(CURRENT_DATE(), INTERVAL {interval_n} {interval_unit})"

    return None


def _get_date_filter(state: dict, config: dict) -> str:
    """
    Determine the correct date filter for ground truth queries.
    For ad-hoc reports: extract from the actual SQL queries run.
    For scheduled reports: extract from the SQL template in metadata.
    """
    report_type = state["report_type"]
    queries     = state.get("queries") or {}

    # For ad-hoc reports — extract from actual queries that were run
    if report_type and report_type.startswith("adhoc"):
        # Try orders query first, then incidents
        for table in ["orders", "incidents", "returns"]:
            sql = queries.get(table, "")
            if isinstance(sql, dict):
                sql = sql.get("sql", "") or str(sql)
            if sql:
                extracted = _extract_date_filter_from_sql(str(sql), "orderDate")
                if extracted:
                    print(f"  [validate] Extracted date filter from {table} query: {extracted}")
                    return extracted

        # Fall back to 30 days if nothing found
        print("  [validate] WARNING — could not extract date filter from queries, using 30 days")
        return "orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)"

    # For scheduled reports — extract from metadata SQL template
    report_config = config["reports"].get(report_type, {})
    orders_table  = report_config.get("tables", {}).get("orders", {})
    sql_template  = orders_table.get("sql_template", "")

    if sql_template:
        match = re.search(
            r"(orderDate\s*>=\s*DATE_SUB\(CURRENT_DATE\(\),\s*INTERVAL\s*\d+\s*\w+\))",
            sql_template
        )
        if match:
            return match.group(1)

    orders_filters = orders_table.get("filters", [])
    return orders_filters[0] if orders_filters else \
        "orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)"


# ── Ground truth queries ──────────────────────────────────────────────────────

def get_ground_truth(
    bq_client:   bigquery.Client,
    project:     str,
    dataset:     str,
    report_type: str,
    supplier_id: Optional[str],
    date_filter: str,
    queries:     dict = None,
) -> dict:
    """
    Re-query BigQuery for ground truth metrics matching the report's exact scope.
    Supplier filter and date window both match what the report was based on.
    """
    sup_filter = f"AND o.supplierID = '{supplier_id}'" if supplier_id else ""
    sup_filter_plain = f"AND supplierID = '{supplier_id}'" if supplier_id else ""

    is_adhoc = report_type and report_type.startswith("adhoc")

    # For ad-hoc reports that only queried incidents (no orders table),
    # derive incident date filter from the incident SQL
    inc_date_filter = date_filter.replace("orderDate", "incidentDate")
    if is_adhoc and queries:
        inc_sql = queries.get("incidents", "")
        if isinstance(inc_sql, dict):
            inc_sql = str(inc_sql)
        if inc_sql and "orderDate" not in inc_sql:
            extracted = _extract_date_filter_from_sql(str(inc_sql), "incidentDate")
            if extracted:
                inc_date_filter = extracted

    ground_truth = {}
    had_orders = queries and "orders" in queries

    # Only query orders-based metrics if the report had orders data
    if not is_adhoc or had_orders:
        try:
            row = list(bq_client.query(f"""
                SELECT
                    COUNT(o.orderID) AS total_orders,
                    SUM(CASE WHEN o.hasIncident THEN 1 ELSE 0 END) AS total_incidents,
                    ROUND(AVG(CASE WHEN o.hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2) AS incident_rate_pct
                FROM `{project}.{dataset}.orders` o
                WHERE {date_filter} {sup_filter}
            """).result())[0]
            ground_truth["total_orders"]      = float(row["total_orders"] or 0)
            ground_truth["total_incidents"]   = float(row["total_incidents"] or 0)
            ground_truth["incident_rate_pct"] = float(row["incident_rate_pct"] or 0)
        except Exception as e:
            print(f"  [validate] WARNING — incident query failed: {e}")

        try:
            row = list(bq_client.query(f"""
                SELECT
                    ROUND(AVG(CASE WHEN o.hasReturn THEN 1.0 ELSE 0.0 END) * 100, 2) AS return_rate_pct
                FROM `{project}.{dataset}.orders` o
                WHERE {date_filter} {sup_filter}
            """).result())[0]
            ground_truth["return_rate_pct"] = float(row["return_rate_pct"] or 0)
        except Exception as e:
            print(f"  [validate] WARNING — return query failed: {e}")

        try:
            row = list(bq_client.query(f"""
                SELECT ROUND(SUM(grossRevenue), 2) AS total_gross_revenue
                FROM `{project}.{dataset}.orders` o
                WHERE {date_filter} {sup_filter}
            """).result())[0]
            ground_truth["total_gross_revenue"] = float(row["total_gross_revenue"] or 0)
        except Exception as e:
            print(f"  [validate] WARNING — revenue query failed: {e}")

    # Resolution cost — always query from incidents with matching scope
    try:
        if is_adhoc and not had_orders:
            # No orders join available — query incidents directly
            row = list(bq_client.query(f"""
                SELECT ROUND(SUM(resolutionCost), 2) AS total_resolution_cost
                FROM `{project}.{dataset}.incidents`
                WHERE {inc_date_filter} {sup_filter_plain}
            """).result())[0]
        else:
            # Join incidents to orders for aligned date window
            row = list(bq_client.query(f"""
                SELECT ROUND(SUM(i.resolutionCost), 2) AS total_resolution_cost
                FROM `{project}.{dataset}.incidents` i
                INNER JOIN `{project}.{dataset}.orders` o ON i.orderID = o.orderID
                WHERE {date_filter} {sup_filter}
            """).result())[0]
        ground_truth["total_resolution_cost"] = float(row["total_resolution_cost"] or 0)
    except Exception as e:
        print(f"  [validate] WARNING — resolution cost query failed: {e}")

    return ground_truth


# ── Metric extractor ──────────────────────────────────────────────────────────

def extract_reported_metrics(narrative: str, analysis: dict) -> dict:
    reported = {}

    overall = analysis.get("overall_metrics", {})
    if overall.get("overall_incident_rate_pct"):
        reported["incident_rate_pct"] = float(overall["overall_incident_rate_pct"])
    if overall.get("overall_return_rate_pct"):
        reported["return_rate_pct"] = float(overall["overall_return_rate_pct"])
    if overall.get("total_resolution_cost"):
        reported["total_resolution_cost"] = float(overall["total_resolution_cost"])
    if overall.get("total_gross_revenue"):
        reported["total_gross_revenue"] = float(overall["total_gross_revenue"])
    if overall.get("total_orders"):
        reported["total_orders"] = float(overall["total_orders"])

    if "incident_rate_pct" not in reported:
        matches = re.findall(
            r"(?:incident rate|incident_rate)[^\d]*(\d+\.?\d*)\s*%",
            narrative, re.IGNORECASE
        )
        if matches:
            reported["incident_rate_pct"] = float(matches[0])

    if "return_rate_pct" not in reported:
        matches = re.findall(
            r"(?:return rate|return_rate)[^\d]*(\d+\.?\d*)\s*%",
            narrative, re.IGNORECASE
        )
        if matches:
            reported["return_rate_pct"] = float(matches[0])

    return reported


# ── Deviation calculator ──────────────────────────────────────────────────────

def calculate_deviation(expected: float, reported: float) -> float:
    if expected == 0:
        return 0.0 if reported == 0 else 100.0
    return abs((reported - expected) / expected) * 100.0


# ── Improvement action validator ──────────────────────────────────────────────

def validate_improvement_actions(analysis: dict) -> list:
    results = []
    actions = analysis.get("improvement_actions") or []

    for i, action in enumerate(actions):
        target      = str(action.get("target", ""))
        scope       = str(action.get("scope", "")).lower()
        has_sku     = bool(re.search(r"[A-Z]{3}-\d{4}", target))
        has_cat     = any(c.lower() in target.lower() for c in [
            "Electronics", "Home", "Garden", "Clothing", "Apparel",
            "Sports", "Outdoors", "Toys", "Games", "Beauty", "Health",
            "Kitchen", "Dining"
        ])
        has_sup     = bool(re.search(r"SUP\d{3}", target))
        is_specific = has_sku or has_cat or has_sup or scope in (
            "sku", "category", "supplier", "portfolio"
        )

        results.append({
            "validation_id":     str(uuid.uuid4()),
            "metric_name":       f"improvement_action_{i+1}_specificity",
            "expected_value":    None,
            "reported_value":    None,
            "deviation_pct":     None,
            "passed":            is_specific,
            "hallucination_flag":False,
            "details":           f"Action {i+1}: target='{target}' scope='{scope}' — "
                                 f"{'specific' if is_specific else 'vague'}",
        })

    return results


# ── Write results ─────────────────────────────────────────────────────────────

def write_validation_results(
    bq_client:   bigquery.Client,
    project:     str,
    dataset:     str,
    run_id:      str,
    report_type: str,
    audience:    str,
    supplier_id: Optional[str],
    results:     list,
):
    table_id = f"{project}.{dataset}.validation_results"
    now      = datetime.now(timezone.utc).isoformat()

    rows = []
    for r in results:
        rows.append({
            "validationID":     r.get("validation_id", str(uuid.uuid4())),
            "runID":            run_id,
            "reportType":       report_type,
            "audience":         audience,
            "supplierID":       supplier_id,
            "validatedAt":      now,
            "metricName":       r.get("metric_name", ""),
            "expectedValue":    r.get("expected_value"),
            "reportedValue":    r.get("reported_value"),
            "deviationPct":     r.get("deviation_pct"),
            "passed":           r.get("passed", True),
            "hallucinationFlag":r.get("hallucination_flag", False),
            "details":          r.get("details", ""),
            "category":         r.get("category"),
            "supplierIDRef":    r.get("supplier_id_ref"),
        })

    if rows:
        errors = bq_client.insert_rows_json(table_id, rows)
        if errors:
            print(f"  [validate] WARNING — failed to write validation results: {errors}")
        else:
            print(f"  [validate] Wrote {len(rows)} validation results to BigQuery")


# ── Validate node ─────────────────────────────────────────────────────────────

def validate_node(state: dict) -> dict:
    print("  [validate] Starting semantic validation...")

    config      = _load_config()
    project     = config["project"]
    dataset     = config["dataset"]
    report_type = state["report_type"]
    audience    = state["audience"]
    supplier_id = state.get("supplier_id")
    run_id      = state.get("run_id", str(uuid.uuid4()))
    narrative   = state.get("report_narrative", "")
    analysis    = state.get("analysis", {})
    queries     = state.get("queries") or {}
    errors      = list(state.get("errors") or [])

    bq_client   = bigquery.Client(project=project)
    all_results = []

    # ── Determine date filter matching the report's actual scope ──────────────
    date_filter = _get_date_filter(state, config)
    print(f"  [validate] Using date filter: {date_filter}")

    # ── Get ground truth from BigQuery ────────────────────────────────────────
    print("  [validate] Querying BigQuery for ground truth metrics...")
    ground_truth = get_ground_truth(
        bq_client   = bq_client,
        project     = project,
        dataset     = dataset,
        report_type = report_type,
        supplier_id = supplier_id,
        date_filter = date_filter,
        queries     = queries,
    )
    print(f"  [validate] Ground truth: {ground_truth}")

    # ── Extract reported metrics ──────────────────────────────────────────────
    reported_metrics = extract_reported_metrics(narrative, analysis)
    print(f"  [validate] Reported metrics: {reported_metrics}")

    # ── Compare metrics ───────────────────────────────────────────────────────
    DEVIATION_THRESHOLD  = 10.0
    HALLUCINATION_THRESHOLD = 20.0

    metric_map = {
        "incident_rate_pct":     "Overall incident rate (%)",
        "return_rate_pct":       "Overall return rate (%)",
        "total_resolution_cost": "Total resolution cost ($)",
        "total_gross_revenue":   "Total gross revenue ($)",
        "total_orders":          "Total orders",
    }

    for metric_key, metric_label in metric_map.items():
        expected = ground_truth.get(metric_key)
        reported = reported_metrics.get(metric_key)

        if expected is None or reported is None:
            all_results.append({
                "validation_id":     str(uuid.uuid4()),
                "metric_name":       metric_key,
                "expected_value":    expected,
                "reported_value":    reported,
                "deviation_pct":     None,
                "passed":            True,
                "hallucination_flag":False,
                "details":           f"{metric_label}: could not compare — "
                                     f"expected={expected}, reported={reported}",
            })
            continue

        deviation        = calculate_deviation(expected, reported)
        passed           = deviation <= DEVIATION_THRESHOLD
        is_hallucination = deviation > HALLUCINATION_THRESHOLD

        status = "✓" if passed else f"✗ ({deviation:.1f}% deviation)"
        print(f"  [validate] {status} {metric_label}: expected={expected}, reported={reported}")

        all_results.append({
            "validation_id":     str(uuid.uuid4()),
            "metric_name":       metric_key,
            "expected_value":    expected,
            "reported_value":    reported,
            "deviation_pct":     round(deviation, 2),
            "passed":            passed,
            "hallucination_flag":is_hallucination,
            "details":           f"{metric_label}: expected {expected}, "
                                 f"reported {reported} ({deviation:.1f}% deviation)",
        })

        if is_hallucination:
            errors.append(
                f"Potential hallucination on {metric_key}: "
                f"expected {expected}, reported {reported} ({deviation:.1f}% deviation)"
            )

    # ── Validate improvement actions ──────────────────────────────────────────
    print("  [validate] Validating improvement action specificity...")
    action_results = validate_improvement_actions(analysis)
    all_results.extend(action_results)

    vague_count = sum(1 for r in action_results if not r["passed"])
    if vague_count > 0:
        print(f"  [validate] {vague_count} improvement action(s) lack specific SKU/category references")

    # ── Write to BigQuery ─────────────────────────────────────────────────────
    write_validation_results(
        bq_client   = bq_client,
        project     = project,
        dataset     = dataset,
        run_id      = run_id,
        report_type = report_type,
        audience    = audience,
        supplier_id = supplier_id,
        results     = all_results,
    )

    # ── Build summary ─────────────────────────────────────────────────────────
    passed_count        = sum(1 for r in all_results if r["passed"])
    failed_count        = len(all_results) - passed_count
    hallucination_count = sum(1 for r in all_results if r.get("hallucination_flag"))
    pass_rate           = passed_count / len(all_results) if all_results else 1.0

    validation_summary = {
        "results":             all_results,
        "total_checks":        len(all_results),
        "passed":              passed_count,
        "failed":              failed_count,
        "hallucination_flags": hallucination_count,
        "pass_rate":           round(pass_rate, 3),
        "ground_truth":        ground_truth,
    }

    print(f"  [validate] Complete — {passed_count}/{len(all_results)} checks passed, "
          f"{hallucination_count} hallucination flags")

    return {
        "validation":  validation_summary,
        "current_node":"validate",
        "errors":      errors,
    }
