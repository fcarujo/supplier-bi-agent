"""
Supplier BI Agent — Comment Intelligence Agent
===============================================
Runs monthly. For each supplier:
  1. Identifies top 5 SKUs with rates > category average + 1pp (min 20 orders, 90-day window)
  2. Pulls all incident and return comments for those SKUs
  3. Calls Claude for exhaustive structured analysis per SKU
  4. Writes results to sku_comment_intelligence BigQuery table

The output feeds into the monthly supplier account report as a
"Customer Voice" section, giving suppliers actionable intelligence
on exactly what customers are experiencing with their problem SKUs.
"""

import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from anthropic import Anthropic
from google.cloud import bigquery

CONFIG_PATH = Path(__file__).parent / "config" / "metadata.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Filter node ───────────────────────────────────────────────────────────────

def get_flagged_skus(
    bq_client:   bigquery.Client,
    project:     str,
    dataset:     str,
    supplier_id: str,
    top_n:       int = 5,
    min_orders:  int = 20,
    deviation_threshold: float = 1.0,
    lookback_days: int = 90,
) -> list:
    """
    Returns top N SKUs where incident or return rate exceeds
    category average by more than deviation_threshold percentage points.
    Requires at least min_orders over lookback_days to be statistically meaningful.
    """
    sql = f"""
    WITH category_avg AS (
        SELECT
            productCategory,
            AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100 AS cat_incident_rate,
            AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100 AS cat_return_rate
        FROM `{project}.{dataset}.orders`
        WHERE supplierID = '{supplier_id}'
          AND orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL {lookback_days} DAY)
        GROUP BY productCategory
    ),
    sku_rates AS (
        SELECT
            productSKU,
            productCategory,
            COUNT(orderID) AS total_orders,
            ROUND(AVG(CASE WHEN hasIncident THEN 1.0 ELSE 0.0 END) * 100, 2) AS sku_incident_rate,
            ROUND(AVG(CASE WHEN hasReturn   THEN 1.0 ELSE 0.0 END) * 100, 2) AS sku_return_rate
        FROM `{project}.{dataset}.orders`
        WHERE supplierID = '{supplier_id}'
          AND orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL {lookback_days} DAY)
        GROUP BY productSKU, productCategory
    ),
    flagged AS (
        SELECT
            s.productSKU,
            s.productCategory,
            s.total_orders,
            s.sku_incident_rate,
            s.sku_return_rate,
            ROUND(c.cat_incident_rate, 4) AS cat_incident_rate,
            ROUND(c.cat_return_rate, 4)   AS cat_return_rate,
            ROUND(GREATEST(
                s.sku_incident_rate - c.cat_incident_rate,
                s.sku_return_rate   - c.cat_return_rate
            ), 2) AS max_deviation
        FROM sku_rates s
        INNER JOIN category_avg c ON s.productCategory = c.productCategory
        WHERE s.total_orders >= {min_orders}
          AND (
              s.sku_incident_rate > c.cat_incident_rate + {deviation_threshold}
              OR s.sku_return_rate > c.cat_return_rate + {deviation_threshold}
          )
    )
    SELECT *
    FROM flagged
    ORDER BY max_deviation DESC
    LIMIT {top_n}
    """
    rows = list(bq_client.query(sql).result())
    return [dict(r) for r in rows]


# ── Comment pull ──────────────────────────────────────────────────────────────

def get_sku_comments(
    bq_client:   bigquery.Client,
    project:     str,
    dataset:     str,
    supplier_id: str,
    product_sku: str,
    lookback_days: int = 90,
) -> dict:
    """
    Pulls all incident and return comments for a SKU.
    Returns structured dict with incidents and returns grouped by type/reason.
    """
    inc_sql = f"""
    SELECT
        i.incidentType,
        i.incidentResolution,
        i.resolutionStatus,
        i.incidentCustomerComment,
        i.customerReview,
        i.productRating,
        i.resolutionCost,
        i.daysBetweenPurchaseAndIncident
    FROM `{project}.{dataset}.incidents` i
    INNER JOIN `{project}.{dataset}.orders` o ON i.orderID = o.orderID
    WHERE i.supplierID = '{supplier_id}'
      AND i.productSKU = '{product_sku}'
      AND o.orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL {lookback_days} DAY)
    ORDER BY i.incidentType, i.productRating ASC
    """

    ret_sql = f"""
    SELECT
        r.buyersRemorseReason,
        r.resolutionStatus,
        r.buyersRemorseComment,
        r.customerReview,
        r.productRating,
        r.daysBetweenPurchaseAndReturn
    FROM `{project}.{dataset}.returns` r
    INNER JOIN `{project}.{dataset}.orders` o ON r.orderID = o.orderID
    WHERE r.supplierID = '{supplier_id}'
      AND r.productSKU = '{product_sku}'
      AND o.orderDate >= DATE_SUB(CURRENT_DATE(), INTERVAL {lookback_days} DAY)
    ORDER BY r.buyersRemorseReason, r.productRating ASC
    """

    incidents = [dict(r) for r in bq_client.query(inc_sql).result()]
    returns   = [dict(r) for r in bq_client.query(ret_sql).result()]

    return {"incidents": incidents, "returns": returns}


# ── Claude analysis ───────────────────────────────────────────────────────────

def analyse_sku_comments(
    client:      Anthropic,
    supplier_id: str,
    product_sku: str,
    category:    str,
    sku_data:    dict,
    sku_rates:   dict,
) -> dict:
    """
    Calls Claude to analyse all comments for a SKU exhaustively.
    Returns structured intelligence JSON.
    """
    incidents = sku_data["incidents"]
    returns   = sku_data["returns"]

    # Format incidents for prompt
    inc_text = ""
    if incidents:
        by_type = {}
        for r in incidents:
            t = r["incidentType"]
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(r)
        for itype, rows in by_type.items():
            inc_text += f"\n### Incident type: {itype} ({len(rows)} incidents)\n"
            for r in rows:
                inc_text += (
                    f"- Rating: {r['productRating']}/5 | "
                    f"Cost: £{r['resolutionCost']:.2f} | "
                    f"Days to incident: {r['daysBetweenPurchaseAndIncident']} | "
                    f"Status: {r['resolutionStatus']}\n"
                    f"  Customer comment: {r['incidentCustomerComment']}\n"
                    f"  Customer review: {r['customerReview']}\n"
                )
    else:
        inc_text = "No incidents recorded for this SKU in the analysis period."

    # Format returns for prompt
    ret_text = ""
    if returns:
        by_reason = {}
        for r in returns:
            reason = r["buyersRemorseReason"]
            if reason not in by_reason:
                by_reason[reason] = []
            by_reason[reason].append(r)
        for reason, rows in by_reason.items():
            ret_text += f"\n### Return reason: {reason} ({len(rows)} returns)\n"
            for r in rows:
                ret_text += (
                    f"- Rating: {r['productRating']}/5 | "
                    f"Days to return: {r['daysBetweenPurchaseAndReturn']} | "
                    f"Status: {r['resolutionStatus']}\n"
                    f"  Customer comment: {r['buyersRemorseComment']}\n"
                    f"  Customer review: {r['customerReview']}\n"
                )
    else:
        ret_text = "No returns recorded for this SKU in the analysis period."

    system_prompt = """You are a supplier performance analyst. You analyse customer comments
exhaustively to identify recurring problems, root causes, and specific improvement actions
for suppliers. You are precise, evidence-based, and commercially focused.

You must return a JSON object only — no preamble, no markdown, no explanation.
The JSON must have exactly these fields:
{
  "incident_themes": [
    {"theme": "string", "frequency": int, "severity": "high|medium|low", "evidence": "direct quote or paraphrase from comments"}
  ],
  "return_themes": [
    {"theme": "string", "frequency": int, "severity": "high|medium|low", "evidence": "direct quote or paraphrase from comments"}
  ],
  "root_causes": [
    {"cause": "string", "category": "packaging|product_quality|listing_accuracy|fulfilment|other", "confidence": "high|medium|low", "supporting_evidence": "string"}
  ],
  "improvements": [
    {"action": "string", "priority": 1, "expected_impact": "string", "effort": "low|medium|high"}
  ],
  "confidence": 0.0
}

Rules:
- incident_themes and return_themes: identify ALL recurring patterns across ALL comments
- root_causes: identify the underlying operational or product failures driving the themes
- improvements: specific, actionable steps the supplier can take — not generic advice
- confidence: 0.0-1.0 based on comment volume and consistency of signal
- All fields required. Use empty arrays if no data.
- Be concise: limit each theme/cause/improvement description to 1-2 sentences."""

    user_prompt = f"""Analyse ALL customer comments for this product SKU exhaustively.

Supplier: {supplier_id}
SKU: {product_sku}
Category: {category}
Incident rate: {sku_rates['sku_incident_rate']}% (category average: {sku_rates['cat_incident_rate']}%)
Return rate: {sku_rates['sku_return_rate']}% (category average: {sku_rates['cat_return_rate']}%)
Max deviation above category average: {sku_rates['max_deviation']}pp

── INCIDENT COMMENTS ({len(incidents)} total) ──
{inc_text}

── RETURN COMMENTS ({len(returns)} total) ──
{ret_text}

Analyse every comment. Identify every recurring theme. Determine root causes.
Provide specific improvement actions the supplier can implement.
Return only the JSON object."""

    def _call(messages, max_tok):
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tok,
            system=system_prompt,
            messages=messages,
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.lower().startswith("json"):
                raw = raw[4:]
        return raw.strip()

    # First attempt
    raw = _call([{"role": "user", "content": user_prompt}], 4000)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Second attempt — explicitly ask for conciseness
    concise_prompt = user_prompt + (
        "\n\nIMPORTANT: Be concise. Limit each theme/cause/improvement "
        "to 1 sentence max. Return valid JSON only — no truncation."
    )
    raw = _call([{"role": "user", "content": concise_prompt}], 4000)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Claude returned invalid JSON for {product_sku}: {e}\nRaw: {raw[:200]}"
        )


# ── Store node ────────────────────────────────────────────────────────────────

def store_intelligence(
    bq_client:      bigquery.Client,
    project:        str,
    dataset:        str,
    supplier_id:    str,
    product_sku:    str,
    category:       str,
    sku_rates:      dict,
    analysis:       dict,
    incident_count: int,
    return_count:   int,
) -> None:
    """Writes structured intelligence to sku_comment_intelligence table."""
    row = {
        "intelligenceID":       str(uuid.uuid4()),
        "supplierID":           supplier_id,
        "productSKU":           product_sku,
        "productCategory":      category,
        "analysisMonth":        date.today().replace(day=1).isoformat(),
        "totalOrders":          sku_rates.get("total_orders"),
        "skuIncidentRate":      sku_rates.get("sku_incident_rate"),
        "catIncidentRate":      sku_rates.get("cat_incident_rate"),
        "skuReturnRate":        sku_rates.get("sku_return_rate"),
        "catReturnRate":        sku_rates.get("cat_return_rate"),
        "maxDeviation":         sku_rates.get("max_deviation"),
        "incidentCommentCount": incident_count,
        "returnCommentCount":   return_count,
        "incidentThemes":       json.dumps(analysis.get("incident_themes", [])),
        "returnThemes":         json.dumps(analysis.get("return_themes", [])),
        "rootCauses":           json.dumps(analysis.get("root_causes", [])),
        "improvements":         json.dumps(analysis.get("improvements", [])),
        "confidence":           analysis.get("confidence", 0.0),
        "generatedAt":          datetime.now(timezone.utc).isoformat(),
    }

    errors = bq_client.insert_rows_json(
        f"{project}.{dataset}.sku_comment_intelligence", [row]
    )
    if errors:
        raise RuntimeError(f"Failed to write intelligence for {product_sku}: {errors}")


# ── Main runner ───────────────────────────────────────────────────────────────

def run_comment_intelligence(
    supplier_id:         str,
    project:             str   = None,
    dataset:             str   = None,
    top_n:               int   = 5,
    min_orders:          int   = 20,
    deviation_threshold: float = 1.0,
    lookback_days:       int   = 90,
) -> dict:
    """
    Run the full Comment Intelligence pipeline for one supplier.
    Returns a summary dict with results per SKU.
    """
    config    = _load_config()
    project   = project or config["project"]
    dataset   = dataset  or config["dataset"]
    api_key   = os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")

    bq_client = bigquery.Client(project=project)
    ai_client = Anthropic(api_key=api_key)

    print(f"\n{'='*60}")
    print(f"Comment Intelligence — {supplier_id}")
    print(f"{'='*60}")

    # ── Step 1: Identify flagged SKUs ─────────────────────────────
    print(f"\n[filter] Identifying top {top_n} problem SKUs...")
    flagged = get_flagged_skus(
        bq_client, project, dataset, supplier_id,
        top_n=top_n,
        min_orders=min_orders,
        deviation_threshold=deviation_threshold,
        lookback_days=lookback_days,
    )

    if not flagged:
        print(f"[filter] No SKUs above threshold for {supplier_id} — skipping")
        return {"supplier_id": supplier_id, "flagged_skus": 0, "results": []}

    print(f"[filter] {len(flagged)} SKUs flagged:")
    for s in flagged:
        print(f"  {s['productSKU']} ({s['productCategory']}) — "
              f"incident {s['sku_incident_rate']}% vs {s['cat_incident_rate']}% avg | "
              f"return {s['sku_return_rate']}% vs {s['cat_return_rate']}% avg | "
              f"deviation {s['max_deviation']}pp")

    # ── Step 2 & 3: Analyse each SKU ─────────────────────────────
    results = []
    for sku_data in flagged:
        sku = sku_data["productSKU"]
        cat = sku_data["productCategory"]
        print(f"\n[analyse] {sku} ({cat})...")

        comments  = get_sku_comments(
            bq_client, project, dataset, supplier_id, sku, lookback_days
        )
        inc_count = len(comments["incidents"])
        ret_count = len(comments["returns"])
        print(f"  {inc_count} incident comments, {ret_count} return comments")

        if inc_count + ret_count == 0:
            print(f"  No comments found — skipping")
            continue

        analysis   = analyse_sku_comments(
            ai_client, supplier_id, sku, cat, comments, sku_data
        )
        confidence = analysis.get("confidence", 0.0)
        n_themes   = len(analysis.get("incident_themes", [])) + len(analysis.get("return_themes", []))
        n_causes   = len(analysis.get("root_causes", []))
        n_actions  = len(analysis.get("improvements", []))
        print(f"  ✓ {n_themes} themes · {n_causes} root causes · {n_actions} improvements · confidence {confidence:.2f}")

        store_intelligence(
            bq_client, project, dataset,
            supplier_id, sku, cat,
            sku_data, analysis, inc_count, ret_count,
        )
        print(f"  ✓ Written to sku_comment_intelligence")

        results.append({
            "sku":        sku,
            "category":   cat,
            "deviation":  sku_data["max_deviation"],
            "confidence": confidence,
            "themes":     n_themes,
            "actions":    n_actions,
        })

    print(f"\n[complete] {supplier_id} — {len(results)}/{len(flagged)} SKUs analysed")
    return {
        "supplier_id":  supplier_id,
        "flagged_skus": len(flagged),
        "analysed":     len(results),
        "results":      results,
    }


# ── Batch runner (all suppliers) ──────────────────────────────────────────────

def run_all_suppliers(
    top_n:               int   = 5,
    min_orders:          int   = 20,
    deviation_threshold: float = 1.0,
    lookback_days:       int   = 90,
) -> None:
    """
    Run Comment Intelligence for all active suppliers.
    Called by the monthly scheduler.
    """
    config    = _load_config()
    project   = config["project"]
    dataset   = config["dataset"]
    bq_client = bigquery.Client(project=project)

    suppliers = [
        dict(r)["supplierID"]
        for r in bq_client.query(f"""
            SELECT DISTINCT supplierID
            FROM `{project}.{dataset}.suppliers`
            ORDER BY supplierID
        """).result()
    ]

    print(f"\nComment Intelligence batch — {len(suppliers)} suppliers")
    print(f"Parameters: top_n={top_n} | min_orders={min_orders} | "
          f"deviation_threshold={deviation_threshold}pp | lookback={lookback_days}d\n")

    summary = []
    for supplier_id in suppliers:
        try:
            result = run_comment_intelligence(
                supplier_id,
                top_n=top_n,
                min_orders=min_orders,
                deviation_threshold=deviation_threshold,
                lookback_days=lookback_days,
            )
            summary.append(result)
        except Exception as e:
            print(f"[error] {supplier_id} failed: {e}")
            summary.append({"supplier_id": supplier_id, "error": str(e)})

    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE — {len(suppliers)} suppliers processed")
    print(f"{'='*60}")
    total_analysed = sum(r.get("analysed", 0) for r in summary)
    total_flagged  = sum(r.get("flagged_skus", 0) for r in summary)
    errors         = [r for r in summary if "error" in r]
    print(f"Total SKUs flagged:   {total_flagged}")
    print(f"Total SKUs analysed:  {total_analysed}")
    print(f"Errors:               {len(errors)}")
    if errors:
        for e in errors:
            print(f"  {e['supplier_id']}: {e['error']}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        run_comment_intelligence(sys.argv[1])
    else:
        run_all_suppliers()