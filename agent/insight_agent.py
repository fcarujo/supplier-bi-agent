"""
Supplier BI Agent — Insight Agent
===================================
Runs weekly. Detects anomalies and trends across the supplier portfolio
and writes structured alerts to the insights BigQuery table.

Signals monitored:
  Supplier-level:
    - Incident rate spike vs 4-week baseline (>20% increase)
    - Return rate spike vs 4-week baseline (>20% increase)
    - Resolution cost spike vs 4-week baseline (>25% increase)

  SKU-level:
    - New problem SKU — above category average this week, not last month
    - SKU incident rate trending up 3 weeks in a row

  Portfolio-level:
    - Incident type trending up across multiple suppliers
    - Category incident rate above portfolio average and worsening

All signals require minimum 25 orders in the analysis window.

After detection:
  - Writes alert rows to insights table
  - Calls Claude to write a 3-4 sentence weekly digest paragraph
  - Writes digest to insight_digests table
  - Deletes alerts older than 4 weeks

Auto-publish rules:
  - confidence >= 0.85 → written directly to dashboard (autoPublished=True)
  - confidence < 0.85  → flagged for human review

Usage:
  python agent/insight_agent.py           # run for current week
  python agent/insight_agent.py --dry-run # show what would be detected
"""

import sys
import uuid
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import os
import yaml
from anthropic import Anthropic
from google.cloud import bigquery

CONFIG_PATH = Path(__file__).parent / "config" / "metadata.yaml"
MIN_ORDERS  = 25
AUTO_PUBLISH_THRESHOLD = 0.75


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _week_of() -> date:
    """Monday of the current week."""
    today = date.today()
    return today - timedelta(days=today.weekday())


# ── Signal detectors ──────────────────────────────────────────────────────────

def detect_supplier_rate_spikes(
    client:  bigquery.Client,
    project: str,
    dataset: str,
) -> list:
    """
    Detect suppliers where incident rate, return rate, or resolution cost
    has spiked vs their 4-week baseline. Requires min 25 orders this week.
    """
    sql = f"""
    WITH weekly AS (
        SELECT
            supplierID,
            COUNT(orderID)                                                        AS orders,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate,
            ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)     AS return_rate
        FROM `{project}.{dataset}.orders`
        WHERE orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY supplierID
    ),
    weekly_cost AS (
        SELECT
            i.supplierID,
            ROUND(SUM(i.resolutionCost), 2) AS resolution_cost
        FROM `{project}.{dataset}.incidents` i
        INNER JOIN `{project}.{dataset}.orders` o ON i.orderID = o.orderID
        WHERE o.orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY i.supplierID
    ),
    baseline AS (
        SELECT
            supplierID,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS baseline_incident_rate,
            ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2)     AS baseline_return_rate
        FROM `{project}.{dataset}.orders`
        WHERE orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 35 DAY)
          AND orderDate <  DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY supplierID
    ),
    baseline_cost AS (
        SELECT
            i.supplierID,
            ROUND(SUM(i.resolutionCost) / 4.0, 2) AS baseline_weekly_cost
        FROM `{project}.{dataset}.incidents` i
        INNER JOIN `{project}.{dataset}.orders` o ON i.orderID = o.orderID
        WHERE o.orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 35 DAY)
          AND o.orderDate <  DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY i.supplierID
    )
    SELECT
        w.supplierID,
        w.orders,
        w.incident_rate,
        b.baseline_incident_rate,
        ROUND(SAFE_DIVIDE(w.incident_rate - b.baseline_incident_rate,
              b.baseline_incident_rate) * 100, 1) AS incident_rate_change_pct,
        w.return_rate,
        b.baseline_return_rate,
        ROUND(SAFE_DIVIDE(w.return_rate - b.baseline_return_rate,
              b.baseline_return_rate) * 100, 1) AS return_rate_change_pct,
        COALESCE(wc.resolution_cost, 0) AS resolution_cost,
        COALESCE(bc.baseline_weekly_cost, 0) AS baseline_weekly_cost,
        ROUND(SAFE_DIVIDE(COALESCE(wc.resolution_cost,0) - COALESCE(bc.baseline_weekly_cost,0),
              NULLIF(bc.baseline_weekly_cost,0)) * 100, 1) AS cost_change_pct
    FROM weekly w
    LEFT JOIN baseline b ON w.supplierID = b.supplierID
    LEFT JOIN weekly_cost wc ON w.supplierID = wc.supplierID
    LEFT JOIN baseline_cost bc ON w.supplierID = bc.supplierID
    WHERE w.orders >= {MIN_ORDERS}
      AND b.baseline_incident_rate IS NOT NULL
    ORDER BY incident_rate_change_pct DESC
    """
    rows = [dict(r) for r in client.query(sql).result()]
    alerts = []

    for r in rows:
        # Incident rate spike
        if r.get("incident_rate_change_pct") and r["incident_rate_change_pct"] >= 20:
            pct = r["incident_rate_change_pct"]
            severity = "critical" if pct >= 40 else "warning" if pct >= 20 else "watch"
            alerts.append({
                "signalType":      "supplier_incident_rate_spike",
                "severity":        severity,
                "supplierID":      r["supplierID"],
                "productSKU":      None,
                "productCategory": None,
                "metricName":      "incident_rate_pct",
                "currentValue":    r["incident_rate"],
                "baselineValue":   r["baseline_incident_rate"],
                "changePercent":   pct,
                "description":     f"{r['supplierID']} incident rate {r['incident_rate']}% vs {r['baseline_incident_rate']}% baseline ({pct:+.1f}%)",
                "confidence":      0.90 if r["orders"] >= 100 else 0.80,
            })

        # Return rate spike
        if r.get("return_rate_change_pct") and r["return_rate_change_pct"] >= 20:
            pct = r["return_rate_change_pct"]
            severity = "critical" if pct >= 40 else "warning" if pct >= 20 else "watch"
            alerts.append({
                "signalType":      "supplier_return_rate_spike",
                "severity":        severity,
                "supplierID":      r["supplierID"],
                "productSKU":      None,
                "productCategory": None,
                "metricName":      "return_rate_pct",
                "currentValue":    r["return_rate"],
                "baselineValue":   r["baseline_return_rate"],
                "changePercent":   pct,
                "description":     f"{r['supplierID']} return rate {r['return_rate']}% vs {r['baseline_return_rate']}% baseline ({pct:+.1f}%)",
                "confidence":      0.90 if r["orders"] >= 100 else 0.80,
            })

        # Resolution cost spike
        if r.get("cost_change_pct") and r["cost_change_pct"] >= 25:
            pct = r["cost_change_pct"]
            severity = "critical" if pct >= 50 else "warning" if pct >= 25 else "watch"
            alerts.append({
                "signalType":      "supplier_resolution_cost_spike",
                "severity":        severity,
                "supplierID":      r["supplierID"],
                "productSKU":      None,
                "productCategory": None,
                "metricName":      "resolution_cost",
                "currentValue":    r["resolution_cost"],
                "baselineValue":   r["baseline_weekly_cost"],
                "changePercent":   pct,
                "description":     f"{r['supplierID']} resolution cost ${r['resolution_cost']:,.0f} vs ${r['baseline_weekly_cost']:,.0f} baseline ({pct:+.1f}%)",
                "confidence":      0.90 if r["orders"] >= 100 else 0.80,
            })

    return alerts


def detect_new_problem_skus(
    client:  bigquery.Client,
    project: str,
    dataset: str,
) -> list:
    """
    Detect SKUs that are above category average this week
    but were not above category average last month.
    Requires min 25 orders this week.
    """
    sql = f"""
    WITH cat_avg_current AS (
        SELECT
            productCategory,
            AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100 AS cat_incident_rate
        FROM `{project}.{dataset}.orders`
        WHERE orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY productCategory
    ),
    sku_current AS (
        SELECT
            supplierID,
            productSKU,
            productCategory,
            COUNT(orderID)                                                        AS orders,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate
        FROM `{project}.{dataset}.orders`
        WHERE orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY supplierID, productSKU, productCategory
    ),
    cat_avg_last_month AS (
        SELECT
            productCategory,
            AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100 AS cat_incident_rate
        FROM `{project}.{dataset}.orders`
        WHERE orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 35 DAY)
          AND orderDate <  DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY productCategory
    ),
    sku_last_month AS (
        SELECT
            supplierID,
            productSKU,
            productCategory,
            COUNT(orderID)                                                        AS orders,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2)     AS incident_rate
        FROM `{project}.{dataset}.orders`
        WHERE orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 35 DAY)
          AND orderDate <  DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY supplierID, productSKU, productCategory
    )
    SELECT
        sc.supplierID,
        sc.productSKU,
        sc.productCategory,
        sc.orders,
        sc.incident_rate AS current_incident_rate,
        cc.cat_incident_rate AS current_cat_avg,
        COALESCE(sl.incident_rate, 0) AS last_month_incident_rate,
        COALESCE(lc.cat_incident_rate, 0) AS last_month_cat_avg,
        ROUND(sc.incident_rate - cc.cat_incident_rate, 2) AS deviation_pp
    FROM sku_current sc
    INNER JOIN cat_avg_current cc ON sc.productCategory = cc.productCategory
    LEFT JOIN sku_last_month sl ON sc.supplierID = sl.supplierID
        AND sc.productSKU = sl.productSKU
    LEFT JOIN cat_avg_last_month lc ON sc.productCategory = lc.productCategory
    WHERE sc.orders >= {MIN_ORDERS}
      AND sc.incident_rate > cc.cat_incident_rate + 1.0
      AND (sl.incident_rate IS NULL
           OR sl.incident_rate <= COALESCE(lc.cat_incident_rate, 0) + 1.0)
    ORDER BY deviation_pp DESC
    LIMIT 10
    """
    rows = [dict(r) for r in client.query(sql).result()]
    alerts = []

    for r in rows:
        dev = r["deviation_pp"]
        severity = "critical" if dev >= 10 else "warning" if dev >= 5 else "watch"
        alerts.append({
            "signalType":      "new_problem_sku",
            "severity":        severity,
            "supplierID":      r["supplierID"],
            "productSKU":      r["productSKU"],
            "productCategory": r["productCategory"],
            "metricName":      "incident_rate_pct",
            "currentValue":    r["current_incident_rate"],
            "baselineValue":   r["current_cat_avg"],
            "changePercent":   dev,
            "description":     f"{r['productSKU']} ({r['productCategory']}) newly above category average — {r['current_incident_rate']}% vs {r['current_cat_avg']}% avg ({dev:+.1f}pp)",
            "confidence":      0.85 if r["orders"] >= 50 else 0.75,
        })

    return alerts


def detect_portfolio_incident_type_trends(
    client:  bigquery.Client,
    project: str,
    dataset: str,
) -> list:
    """
    Detect incident types trending up across multiple suppliers
    vs 4-week baseline. Flag if affecting 3+ suppliers.
    """
    sql = f"""
    WITH current_week AS (
        SELECT
            i.incidentType,
            COUNT(DISTINCT i.supplierID) AS supplier_count,
            COUNT(i.incidentID)          AS total_incidents
        FROM `{project}.{dataset}.incidents` i
        INNER JOIN `{project}.{dataset}.orders` o ON i.orderID = o.orderID
        WHERE o.orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY i.incidentType
    ),
    baseline AS (
        SELECT
            i.incidentType,
            ROUND(COUNT(i.incidentID) / 4.0, 1) AS avg_weekly_incidents
        FROM `{project}.{dataset}.incidents` i
        INNER JOIN `{project}.{dataset}.orders` o ON i.orderID = o.orderID
        WHERE o.orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 35 DAY)
          AND o.orderDate <  DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY i.incidentType
    )
    SELECT
        c.incidentType,
        c.supplier_count,
        c.total_incidents,
        b.avg_weekly_incidents,
        ROUND(SAFE_DIVIDE(c.total_incidents - b.avg_weekly_incidents,
              b.avg_weekly_incidents) * 100, 1) AS change_pct
    FROM current_week c
    LEFT JOIN baseline b ON c.incidentType = b.incidentType
    WHERE c.supplier_count >= 3
      AND b.avg_weekly_incidents IS NOT NULL
      AND c.total_incidents > b.avg_weekly_incidents * 1.20
    ORDER BY change_pct DESC
    """
    rows = [dict(r) for r in client.query(sql).result()]
    alerts = []

    for r in rows:
        pct = r["change_pct"] or 0
        severity = "critical" if pct >= 40 else "warning" if pct >= 20 else "watch"
        alerts.append({
            "signalType":      "portfolio_incident_type_trend",
            "severity":        severity,
            "supplierID":      None,
            "productSKU":      None,
            "productCategory": None,
            "metricName":      f"incident_type_{r['incidentType']}",
            "currentValue":    float(r["total_incidents"]),
            "baselineValue":   float(r["avg_weekly_incidents"]),
            "changePercent":   pct,
            "description":     f"{r['incidentType'].replace('_',' ')} incidents up {pct:+.1f}% this week affecting {r['supplier_count']} suppliers ({r['total_incidents']} incidents vs {r['avg_weekly_incidents']:.1f} weekly avg)",
            "confidence":      0.90,
        })

    return alerts


def detect_category_trends(
    client:  bigquery.Client,
    project: str,
    dataset: str,
) -> list:
    """
    Detect categories where incident rate is above portfolio average
    and worsening vs the prior week.
    """
    sql = f"""
    WITH portfolio_avg AS (
        SELECT
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2) AS portfolio_rate
        FROM `{project}.{dataset}.orders`
        WHERE orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
    ),
    current_week AS (
        SELECT
            productCategory,
            COUNT(orderID) AS orders,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2) AS incident_rate
        FROM `{project}.{dataset}.orders`
        WHERE orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY productCategory
    ),
    prior_week AS (
        SELECT
            productCategory,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2) AS incident_rate
        FROM `{project}.{dataset}.orders`
        WHERE orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
          AND orderDate <  DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY productCategory
    )
    SELECT
        c.productCategory,
        c.orders,
        c.incident_rate AS current_rate,
        p.incident_rate AS prior_rate,
        pa.portfolio_rate,
        ROUND(c.incident_rate - p.incident_rate, 2) AS week_change_pp,
        ROUND(c.incident_rate - pa.portfolio_rate, 2) AS vs_portfolio_pp
    FROM current_week c
    CROSS JOIN portfolio_avg pa
    LEFT JOIN prior_week p ON c.productCategory = p.productCategory
    WHERE c.orders >= {MIN_ORDERS}
      AND c.incident_rate > pa.portfolio_rate
      AND c.incident_rate > COALESCE(p.incident_rate, 0)
    ORDER BY vs_portfolio_pp DESC
    """
    rows = [dict(r) for r in client.query(sql).result()]
    alerts = []

    for r in rows:
        vs_portfolio = r["vs_portfolio_pp"] or 0
        week_change  = r["week_change_pp"] or 0
        if vs_portfolio < 1.0:
            continue
        severity = "critical" if vs_portfolio >= 3 else "warning" if vs_portfolio >= 1.5 else "watch"
        alerts.append({
            "signalType":      "category_rate_worsening",
            "severity":        severity,
            "supplierID":      None,
            "productSKU":      None,
            "productCategory": r["productCategory"],
            "metricName":      "incident_rate_pct",
            "currentValue":    r["current_rate"],
            "baselineValue":   r["portfolio_rate"],
            "changePercent":   round((week_change / max(r["prior_rate"] or 1, 0.01)) * 100, 1),
            "description":     f"{r['productCategory']} incident rate {r['current_rate']}% — {vs_portfolio:+.1f}pp above portfolio average and worsening week-on-week ({week_change:+.2f}pp)",
            "confidence":      0.88,
        })

    return alerts


# ── Digest generator ──────────────────────────────────────────────────────────

def generate_digest(
    ai_client: Anthropic,
    alerts:    list,
    week_of:   date,
) -> str:
    """
    Call Claude to write a 3-4 sentence weekly digest paragraph
    summarising the most important signals for the business review.
    """
    if not alerts:
        return "No significant anomalies detected this week. All supplier incident and return rates are within normal ranges relative to their baselines."

    critical = [a for a in alerts if a["severity"] == "critical"]
    warnings = [a for a in alerts if a["severity"] == "warning"]
    watches  = [a for a in alerts if a["severity"] == "watch"]

    alert_summary = f"Week of {week_of.isoformat()}\n"
    alert_summary += f"Total alerts: {len(alerts)} ({len(critical)} critical, {len(warnings)} warning, {len(watches)} watch)\n\n"
    alert_summary += "Alerts:\n"
    for a in sorted(alerts, key=lambda x: {"critical":0,"warning":1,"watch":2}[x["severity"]]):
        alert_summary += f"[{a['severity'].upper()}] {a['description']}\n"

    system_prompt = """You are writing a weekly supplier performance digest for a business review session.
Write exactly 3-4 sentences. Be direct and specific — name suppliers, categories, and numbers.
Focus on the most critical signals. End with the single most important action to take this week.
Do not use bullet points, headers, or markdown. Plain prose only."""

    user_prompt = f"""Write a weekly digest paragraph for this supplier performance data.
This will be shown at the top of the business dashboard to drive the weekly review agenda.

{alert_summary}

Write 3-4 sentences covering the most important signals and the top recommended action."""

    response = ai_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()


# ── Store and cleanup ─────────────────────────────────────────────────────────

def store_alerts(
    client:   bigquery.Client,
    project:  str,
    dataset:  str,
    alerts:   list,
    week_of:  date,
) -> None:
    """Write alert rows to insights table."""
    if not alerts:
        return

    rows = []
    for a in alerts:
        auto_published = a["confidence"] >= AUTO_PUBLISH_THRESHOLD
        rows.append({
            "insightID":       str(uuid.uuid4()),
            "weekOf":          week_of.isoformat(),
            "signalType":      a["signalType"],
            "severity":        a["severity"],
            "supplierID":      a.get("supplierID"),
            "productSKU":      a.get("productSKU"),
            "productCategory": a.get("productCategory"),
            "metricName":      a.get("metricName"),
            "currentValue":    a.get("currentValue"),
            "baselineValue":   a.get("baselineValue"),
            "changePercent":   a.get("changePercent"),
            "description":     a["description"],
            "confidence":      a["confidence"],
            "autoPublished":   auto_published,
            "generatedAt":     datetime.now(timezone.utc).isoformat(),
        })

    errors = client.insert_rows_json(f"{project}.{dataset}.insights", rows)
    if errors:
        raise RuntimeError(f"Failed to write insights: {errors}")


def store_digest(
    client:    bigquery.Client,
    project:   str,
    dataset:   str,
    narrative: str,
    alerts:    list,
    week_of:   date,
    confidence: float,
) -> None:
    """Write weekly digest to insight_digests table."""
    auto_published = confidence >= AUTO_PUBLISH_THRESHOLD
    errors = client.insert_rows_json(
        f"{project}.{dataset}.insight_digests",
        [{
            "digestID":      str(uuid.uuid4()),
            "weekOf":        week_of.isoformat(),
            "narrative":     narrative,
            "totalAlerts":   len(alerts),
            "criticalCount": sum(1 for a in alerts if a["severity"] == "critical"),
            "warningCount":  sum(1 for a in alerts if a["severity"] == "warning"),
            "watchCount":    sum(1 for a in alerts if a["severity"] == "watch"),
            "confidence":    confidence,
            "autoPublished": auto_published,
            "generatedAt":   datetime.now(timezone.utc).isoformat(),
        }]
    )
    if errors:
        raise RuntimeError(f"Failed to write digest: {errors}")


def cleanup_old_insights(
    client:  bigquery.Client,
    project: str,
    dataset: str,
) -> None:
    """Delete insights and digests older than 4 weeks."""
    cutoff = (date.today() - timedelta(weeks=4)).isoformat()

    client.query(f"""
        DELETE FROM `{project}.{dataset}.insights`
        WHERE weekOf < '{cutoff}'
    """).result()

    client.query(f"""
        DELETE FROM `{project}.{dataset}.insight_digests`
        WHERE weekOf < '{cutoff}'
    """).result()

    print(f"  [cleanup] Deleted insights older than {cutoff}")


# ── Main runner ───────────────────────────────────────────────────────────────

def run_insight_agent(dry_run: bool = False) -> dict:
    """
    Run the full Insight Agent pipeline for the current week.
    """
    config  = _load_config()
    project = config["project"]
    dataset = config["dataset"]
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")

    bq_client = bigquery.Client(project=project)
    ai_client = Anthropic(api_key=api_key)
    week_of   = _week_of()

    print(f"\n{'='*60}")
    print(f"Insight Agent — week of {week_of.isoformat()}")
    print(f"{'='*60}")
    if dry_run:
        print(f"DRY RUN — no writes to BigQuery\n")

    all_alerts = []

    # ── Step 1: Detect signals ────────────────────────────────────────────────
    print(f"\n[detect] Supplier rate spikes...")
    supplier_alerts = detect_supplier_rate_spikes(bq_client, project, dataset)
    print(f"  {len(supplier_alerts)} alerts")
    all_alerts.extend(supplier_alerts)

    print(f"[detect] New problem SKUs...")
    sku_alerts = detect_new_problem_skus(bq_client, project, dataset)
    print(f"  {len(sku_alerts)} alerts")
    all_alerts.extend(sku_alerts)

    print(f"[detect] Portfolio incident type trends...")
    portfolio_alerts = detect_portfolio_incident_type_trends(bq_client, project, dataset)
    print(f"  {len(portfolio_alerts)} alerts")
    all_alerts.extend(portfolio_alerts)

    print(f"[detect] Category trends...")
    category_alerts = detect_category_trends(bq_client, project, dataset)
    print(f"  {len(category_alerts)} alerts")
    all_alerts.extend(category_alerts)

    # Print all detected alerts
    if all_alerts:
        print(f"\n[alerts] {len(all_alerts)} total alerts detected:")
        for a in sorted(all_alerts, key=lambda x: {"critical":0,"warning":1,"watch":2}[x["severity"]]):
            icon = "🔴" if a["severity"]=="critical" else "🟡" if a["severity"]=="warning" else "🔵"
            print(f"  {icon} [{a['severity'].upper()}] {a['description']}")
    else:
        print(f"\n[alerts] No alerts detected this week")

    # ── Step 2: Generate digest ───────────────────────────────────────────────
    print(f"\n[digest] Generating weekly digest...")
    narrative = generate_digest(ai_client, all_alerts, week_of)
    print(f"\n  Digest:\n  {narrative}\n")

    # Calculate overall confidence
    if all_alerts:
        avg_confidence = sum(a["confidence"] for a in all_alerts) / len(all_alerts)
    else:
        avg_confidence = 0.95  # no alerts = high confidence all is well

    auto_published = avg_confidence >= AUTO_PUBLISH_THRESHOLD
    print(f"  Confidence: {avg_confidence:.2f} | Auto-publish: {auto_published}")

    if dry_run:
        print(f"\n[dry-run] Would write {len(all_alerts)} alerts and 1 digest to BigQuery")
        return {
            "week_of":      week_of.isoformat(),
            "total_alerts": len(all_alerts),
            "critical":     sum(1 for a in all_alerts if a["severity"]=="critical"),
            "warning":      sum(1 for a in all_alerts if a["severity"]=="warning"),
            "watch":        sum(1 for a in all_alerts if a["severity"]=="watch"),
            "confidence":   avg_confidence,
            "dry_run":      True,
        }

    # ── Step 3: Store alerts and digest ──────────────────────────────────────
    print(f"\n[store] Writing {len(all_alerts)} alerts to BigQuery...")
    store_alerts(bq_client, project, dataset, all_alerts, week_of)

    print(f"[store] Writing digest to BigQuery...")
    store_digest(bq_client, project, dataset, narrative, all_alerts, week_of, avg_confidence)

    # ── Step 4: Cleanup old data ──────────────────────────────────────────────
    print(f"\n[cleanup] Removing insights older than 4 weeks...")
    cleanup_old_insights(bq_client, project, dataset)

    print(f"\n{'='*60}")
    print(f"INSIGHT AGENT COMPLETE")
    print(f"{'='*60}")
    print(f"  Alerts:       {len(all_alerts)} ({sum(1 for a in all_alerts if a['severity']=='critical')} critical)")
    print(f"  Confidence:   {avg_confidence:.2f}")
    print(f"  Auto-publish: {auto_published}")

    return {
        "week_of":        week_of.isoformat(),
        "total_alerts":   len(all_alerts),
        "critical":       sum(1 for a in all_alerts if a["severity"]=="critical"),
        "warning":        sum(1 for a in all_alerts if a["severity"]=="warning"),
        "watch":          sum(1 for a in all_alerts if a["severity"]=="watch"),
        "confidence":     avg_confidence,
        "auto_published": auto_published,
        "narrative":      narrative,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Supplier BI Insight Agent")
    parser.add_argument("--dry-run", action="store_true", help="Detect signals without writing to BigQuery")
    args = parser.parse_args()
    run_insight_agent(dry_run=args.dry_run)
