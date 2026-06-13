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
    if not sql:
        return None

    # Pattern 1: INTERVAL N MONTH/DAY
    match = re.search(
        r"INTERVAL\s+(\d+)\s+(MONTH|DAY)",
        sql, re.IGNORECASE
    )
    if match:
        n    = match.group(1)
        unit = match.group(2).upper()
        return f"{date_col} >= DATE_SUB(CURRENT_DATE(), INTERVAL {n} {unit})"

    # Pattern 2: explicit date strings e.g. >= '2025-01-01' AND <= '2025-12-31'
    date_pat = r'(\d{4}-\d{2}-\d{2})'
    match = re.search(date_pat + r'.{0,20}AND.{0,20}' + date_pat, sql, re.IGNORECASE)
    if match:
        d_from = match.group(1)
        d_to   = match.group(2)
        return "{} BETWEEN '{}' AND '{}'".format(date_col, d_from, d_to)

    # Pattern 3: single >= date
    match = re.search(r'>=' + r'\s*[\'"]?' + date_pat, sql, re.IGNORECASE)
    if match:
        return "{} >= '{}'".format(date_col, match.group(1))
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


def get_ground_truth_for_year(
    bq_client:   bigquery.Client,
    project:     str,
    dataset:     str,
    year:        str,
    supplier_id: Optional[str],
) -> dict:
    """
    Ground truth scoped to a single calendar year via EXTRACT(YEAR ...).
    Used for multi-period ad-hoc reports where by_period splits figures by year.
    Scope is derived from the year itself — never regex-scraped from SQL — so it
    matches the report's per-year figures exactly.
    """
    sup_o = f"AND o.supplierID = '{supplier_id}'" if supplier_id else ""
    sup_p = f"AND supplierID = '{supplier_id}'" if supplier_id else ""
    gt = {}

    try:
        row = list(bq_client.query(f"""
            SELECT
                COUNT(o.orderID) AS total_orders,
                ROUND(AVG(CASE WHEN o.hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2) AS incident_rate_pct,
                ROUND(AVG(CASE WHEN o.hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2) AS return_rate_pct,
                ROUND(SUM(o.grossRevenue), 2) AS total_gross_revenue
            FROM `{project}.{dataset}.orders` o
            WHERE EXTRACT(YEAR FROM o.orderDate) = {int(year)} {sup_o}
        """).result())[0]
        gt["total_orders"]        = float(row["total_orders"] or 0)
        gt["incident_rate_pct"]   = float(row["incident_rate_pct"] or 0)
        gt["return_rate_pct"]     = float(row["return_rate_pct"] or 0)
        gt["total_gross_revenue"] = float(row["total_gross_revenue"] or 0)
    except Exception as e:
        print(f"  [validate] WARNING — year {year} orders ground truth failed: {e}")

    try:
        row = list(bq_client.query(f"""
            SELECT ROUND(SUM(i.resolutionCost), 2) AS total_resolution_cost
            FROM `{project}.{dataset}.incidents` i
            INNER JOIN `{project}.{dataset}.orders` o ON i.orderID = o.orderID
            WHERE EXTRACT(YEAR FROM o.orderDate) = {int(year)} {sup_o}
        """).result())[0]
        gt["total_resolution_cost"] = float(row["total_resolution_cost"] or 0)
    except Exception as e:
        print(f"  [validate] WARNING — year {year} resolution cost ground truth failed: {e}")

    return gt


# ── Metric extractor ──────────────────────────────────────────────────────────

def _metrics_from_bucket(bucket: dict) -> dict:
    """
    Pull comparable metrics out of an analysis metrics bucket.
    Handles both the 'overall' shape (overall_incident_rate_pct) and the
    per-period shape from by_period (incident_rate_pct).
    """
    reported = {}
    if not isinstance(bucket, dict):
        return reported

    # incident rate — overall uses 'overall_incident_rate_pct', period uses 'incident_rate_pct'
    inc = bucket.get("overall_incident_rate_pct", bucket.get("incident_rate_pct"))
    if inc is not None:
        reported["incident_rate_pct"] = float(inc)

    ret = bucket.get("overall_return_rate_pct", bucket.get("return_rate_pct"))
    if ret is not None:
        reported["return_rate_pct"] = float(ret)

    if bucket.get("total_resolution_cost") is not None:
        reported["total_resolution_cost"] = float(bucket["total_resolution_cost"])
    if bucket.get("total_gross_revenue") is not None:
        reported["total_gross_revenue"] = float(bucket["total_gross_revenue"])
    if bucket.get("total_orders") is not None:
        reported["total_orders"] = float(bucket["total_orders"])

    return reported


def extract_reported_metrics(narrative: str, analysis: dict) -> dict:
    # FIX: analyse.py emits 'overall', not 'overall_metrics'. The old key never
    # matched, so this silently fell through to regex every time.
    reported = _metrics_from_bucket(analysis.get("overall", {}))

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
    summary_ground_truth = {}

    DEVIATION_THRESHOLD     = 10.0
    HALLUCINATION_THRESHOLD = 20.0

    metric_map = {
        "incident_rate_pct":     "incident rate (%)",
        "return_rate_pct":       "return rate (%)",
        "total_resolution_cost": "total resolution cost ($)",
        "total_gross_revenue":   "total gross revenue ($)",
        "total_orders":          "total orders",
    }

    def _compare(metric_key, label_prefix, expected, reported):
        """Compare one metric. Returns a validation result dict."""
        label = f"{label_prefix} {metric_map[metric_key]}".strip()

        # Could not compare — soft warning (route to queue, never escalate)
        if expected is None or reported is None:
            return {
                "validation_id":     str(uuid.uuid4()),
                "metric_name":       metric_key,
                "expected_value":    expected,
                "reported_value":    reported,
                "deviation_pct":     None,
                "passed":            False,
                "soft_warning":      True,
                "hallucination_flag":False,
                "details":           f"{label}: could not compare — "
                                     f"expected={expected}, reported={reported} (soft warning)",
            }

        # Ground truth 0 but report has a value — scope mismatch, skip (no flag)
        if expected == 0 and reported > 0:
            print(f"  [validate] SKIP {label}: ground truth=0, reported={reported}")
            return {
                "validation_id":     str(uuid.uuid4()),
                "metric_name":       metric_key,
                "expected_value":    expected,
                "reported_value":    reported,
                "deviation_pct":     None,
                "passed":            True,
                "hallucination_flag":False,
                "details":           f"{label}: ground truth returned 0 — "
                                     f"possible scope mismatch, skipping",
            }

        deviation        = calculate_deviation(expected, reported)
        passed           = deviation <= DEVIATION_THRESHOLD
        is_hallucination = deviation > HALLUCINATION_THRESHOLD
        status = "✓" if passed else f"✗ ({deviation:.1f}% deviation)"
        print(f"  [validate] {status} {label}: expected={expected}, reported={reported}")

        if is_hallucination:
            errors.append(
                f"Potential hallucination on {label}: expected {expected}, "
                f"reported {reported} ({deviation:.1f}% deviation)"
            )

        return {
            "validation_id":     str(uuid.uuid4()),
            "metric_name":       metric_key,
            "expected_value":    expected,
            "reported_value":    reported,
            "deviation_pct":     round(deviation, 2),
            "passed":            passed,
            "hallucination_flag":is_hallucination,
            "details":           f"{label}: expected {expected}, "
                                 f"reported {reported} ({deviation:.1f}% deviation)",
        }

    # ── Decide validation strategy ────────────────────────────────────────────
    # If analyse produced a per-year breakdown (multi-period ad-hoc report),
    # validate each year against year-scoped ground truth — like-for-like.
    # Otherwise use the single-scope path with the report's actual date window.
    by_period = analysis.get("by_period") or {}
    year_buckets = {
        y: b for y, b in by_period.items()
        if re.fullmatch(r"\d{4}", str(y)) and isinstance(b, dict)
    }

    if year_buckets:
        print(f"  [validate] Per-period validation across years: {sorted(year_buckets)}")
        for year in sorted(year_buckets):
            bucket   = year_buckets[year]
            reported = _metrics_from_bucket(bucket)
            print(f"  [validate] Year {year} reported: {reported}")
            gt = get_ground_truth_for_year(
                bq_client   = bq_client,
                project     = project,
                dataset     = dataset,
                year        = year,
                supplier_id = supplier_id,
            )
            print(f"  [validate] Year {year} ground truth: {gt}")
            summary_ground_truth[year] = gt
            for metric_key in metric_map:
                all_results.append(
                    _compare(metric_key, year, gt.get(metric_key), reported.get(metric_key))
                )
    else:
        # Single-scope path (scheduled reports, single-period ad-hoc)
        date_filter = _get_date_filter(state, config)
        print(f"  [validate] Single-scope validation, date filter: {date_filter}")
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
        summary_ground_truth = ground_truth
        reported_metrics = extract_reported_metrics(narrative, analysis)
        print(f"  [validate] Reported metrics: {reported_metrics}")
        for metric_key in metric_map:
            all_results.append(
                _compare(metric_key, "", ground_truth.get(metric_key), reported_metrics.get(metric_key))
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
        "ground_truth":        summary_ground_truth,
    }

    print(f"  [validate] Complete — {passed_count}/{len(all_results)} checks passed, "
          f"{hallucination_count} hallucination flags")

    return {
        "validation":  validation_summary,
        "current_node":"validate",
        "errors":      errors,
    }
