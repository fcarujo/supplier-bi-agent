"""
Supplier BI Agent — Node 3: Analyse
=====================================
Takes aggregated query results from the Pull node, pre-processes
them into a coherent data summary, then calls Claude to produce
structured JSON analysis.

Two stages:
  1. Python pre-processing — joins multi-table results, computes
     derived metrics (incident rates, return rates, resolution costs,
     category averages, peer benchmarks). No LLM. Deterministic.
     Slims results to top-N before sending to LLM.

  2. LLM call — passes the pre-processed summary to Claude and
     requests structured JSON: metrics, trends, anomalies, confidence,
     flags. No free text at this stage.

The Generate node (next) turns this JSON into the narrative report.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

import yaml
from anthropic import Anthropic


# ── Load metadata config ──────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / "config" / "metadata.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Pre-processor ─────────────────────────────────────────────────────────────

def preprocess_results(
    query_results: dict,
    audience:      str,
    supplier_id:   Optional[str],
    project:       str,
    dataset:       str,
) -> dict:
    """
    Join and aggregate multi-table query results into a coherent
    data summary ready for the LLM. Pure Python — no LLM calls.
    Slims to top-N items before returning to keep LLM context manageable.
    """
    summary = {
        "audience":         audience,
        "supplier_id":      supplier_id,
        "tables_available": list(query_results.keys()),
    }

    orders    = query_results.get("orders",    [])
    incidents = query_results.get("incidents", [])
    returns   = query_results.get("returns",   [])
    suppliers = query_results.get("suppliers", [])

    # ── Supplier reference map ────────────────────────────────────────────────
    supplier_map = {s["supplierID"]: s for s in suppliers}
    summary["suppliers"] = supplier_map

    # ── Order-level metrics ───────────────────────────────────────────────────
    if orders:
        total_orders              = sum(r.get("total_orders", 0) or 0 for r in orders)
        total_revenue             = sum(r.get("total_gross_revenue", 0) or 0 for r in orders)
        total_incidents_from_orders = sum(r.get("total_incidents", 0) or 0 for r in orders)
        total_returns_from_orders   = sum(r.get("total_returns", 0) or 0 for r in orders)

        overall_incident_rate = round(
            total_incidents_from_orders / total_orders * 100, 2
        ) if total_orders else 0

        overall_return_rate = round(
            total_returns_from_orders / total_orders * 100, 2
        ) if total_orders else 0

        summary["overall"] = {
            "total_orders":              total_orders,
            "total_gross_revenue":       round(total_revenue, 2),
            "total_incidents":           total_incidents_from_orders,
            "total_returns":             total_returns_from_orders,
            "overall_incident_rate_pct": overall_incident_rate,
            "overall_return_rate_pct":   overall_return_rate,
        }

        # ── Per-supplier aggregation ──────────────────────────────────────────
        supplier_agg = {}
        for row in orders:
            sid = row.get("supplierID")
            if not sid:
                continue
            if sid not in supplier_agg:
                supplier_agg[sid] = {
                    "supplierID":    sid,
                    "supplierName":  supplier_map.get(sid, {}).get("supplierName", sid),
                    "supplierTier":  row.get("supplierTier"),
                    "supplierRegion":row.get("supplierRegion"),
                    "total_orders":  0,
                    "total_revenue": 0,
                    "total_incidents": 0,
                    "total_returns":   0,
                    "categories":    set(),
                }
            agg = supplier_agg[sid]
            agg["total_orders"]    += row.get("total_orders", 0) or 0
            agg["total_revenue"]   += row.get("total_gross_revenue", 0) or 0
            agg["total_incidents"] += row.get("total_incidents", 0) or 0
            agg["total_returns"]   += row.get("total_returns", 0) or 0
            if row.get("productCategory"):
                agg["categories"].add(row["productCategory"])

        for sid, agg in supplier_agg.items():
            n = agg["total_orders"]
            agg["incident_rate_pct"] = round(agg["total_incidents"] / n * 100, 2) if n else 0
            agg["return_rate_pct"]   = round(agg["total_returns"]   / n * 100, 2) if n else 0
            agg["categories"]        = list(agg["categories"])
            agg["total_revenue"]     = round(agg["total_revenue"], 2)

        summary["by_supplier"] = supplier_agg

        # ── Per-category aggregation ──────────────────────────────────────────
        category_agg = {}
        for row in orders:
            cat = row.get("productCategory")
            if not cat:
                continue
            if cat not in category_agg:
                category_agg[cat] = {
                    "total_orders": 0, "total_incidents": 0, "total_returns": 0
                }
            agg = category_agg[cat]
            agg["total_orders"]    += row.get("total_orders", 0) or 0
            agg["total_incidents"] += row.get("total_incidents", 0) or 0
            agg["total_returns"]   += row.get("total_returns", 0) or 0

        for cat, agg in category_agg.items():
            n = agg["total_orders"]
            agg["incident_rate_pct"] = round(agg["total_incidents"] / n * 100, 2) if n else 0
            agg["return_rate_pct"]   = round(agg["total_returns"]   / n * 100, 2) if n else 0

        summary["by_category"] = category_agg

        # ── Per-fulfilment-channel aggregation ────────────────────────────────
        channel_agg = {}
        for row in orders:
            ch = row.get("fulfilmentChannel")
            if not ch:
                continue
            if ch not in channel_agg:
                channel_agg[ch] = {"total_orders": 0, "total_incidents": 0}
            channel_agg[ch]["total_orders"]    += row.get("total_orders", 0) or 0
            channel_agg[ch]["total_incidents"] += row.get("total_incidents", 0) or 0

        for ch, agg in channel_agg.items():
            n = agg["total_orders"]
            agg["incident_rate_pct"] = round(agg["total_incidents"] / n * 100, 2) if n else 0

        summary["by_fulfilment_channel"] = channel_agg

    # ── Incident metrics ──────────────────────────────────────────────────────
    if incidents:
        total_resolution_cost = sum(r.get("total_resolution_cost", 0) or 0 for r in incidents)
        total_inc_count       = sum(r.get("total_incidents", 0) or 0 for r in incidents)

        # Incident type breakdown — aggregate across all rows
        type_agg = {}
        for row in incidents:
            t = row.get("incidentType")
            if not t:
                continue
            if t not in type_agg:
                type_agg[t] = {"count": 0, "total_cost": 0}
            type_agg[t]["count"]      += row.get("total_incidents", 0) or 0
            type_agg[t]["total_cost"] += row.get("total_resolution_cost", 0) or 0

        # Use overall incident count from orders for rate calculation
        total_for_pct = summary.get("overall", {}).get("total_incidents", total_inc_count)
        for t, agg in type_agg.items():
            agg["pct_of_total"] = round(
                agg["count"] / total_for_pct * 100, 1
            ) if total_for_pct else 0
            agg["total_cost"] = round(agg["total_cost"], 2)

        # Resolution type breakdown
        resolution_agg = {}
        for row in incidents:
            r = row.get("incidentResolution")
            if not r:
                continue
            if r not in resolution_agg:
                resolution_agg[r] = {"count": 0, "total_cost": 0}
            resolution_agg[r]["count"]      += row.get("total_incidents", 0) or 0
            resolution_agg[r]["total_cost"] += row.get("total_resolution_cost", 0) or 0

        for r, agg in resolution_agg.items():
            agg["pct_of_total"] = round(
                agg["count"] / total_for_pct * 100, 1
            ) if total_for_pct else 0
            agg["total_cost"] = round(agg["total_cost"], 2)

        # Per-SKU incident breakdown (supplier account mode)
        sku_incident_agg = {}
        for row in incidents:
            sku = row.get("productSKU")
            if not sku:
                continue
            if sku not in sku_incident_agg:
                sku_incident_agg[sku] = {
                    "productSKU":      sku,
                    "productCategory": row.get("productCategory"),
                    "total_incidents": 0,
                    "total_cost":      0,
                    "incident_types":  {},
                }
            agg = sku_incident_agg[sku]
            agg["total_incidents"] += row.get("total_incidents", 0) or 0
            agg["total_cost"]      += row.get("total_resolution_cost", 0) or 0
            t = row.get("incidentType")
            if t:
                agg["incident_types"][t] = (
                    agg["incident_types"].get(t, 0) + (row.get("total_incidents", 0) or 0)
                )

        for sku, agg in sku_incident_agg.items():
            agg["total_cost"] = round(agg["total_cost"], 2)

        summary["incidents"] = {
            "total_resolution_cost": round(total_resolution_cost, 2),
            "by_type":               type_agg,
            "by_resolution":         resolution_agg,
            "by_sku":                sku_incident_agg,
        }

    # ── Return metrics ────────────────────────────────────────────────────────
    if returns:
        total_ret_count = sum(r.get("total_returns", 0) or 0 for r in returns)

        reason_agg = {}
        for row in returns:
            reason = row.get("buyersRemorseReason")
            if not reason:
                continue
            if reason not in reason_agg:
                reason_agg[reason] = {"count": 0, "avg_rating": []}
            reason_agg[reason]["count"] += row.get("total_returns", 0) or 0
            if row.get("avg_product_rating"):
                reason_agg[reason]["avg_rating"].append(row["avg_product_rating"])

        total_ret_for_pct = summary.get("overall", {}).get("total_returns", total_ret_count)
        for reason, agg in reason_agg.items():
            agg["pct_of_total"] = round(
                agg["count"] / total_ret_for_pct * 100, 1
            ) if total_ret_for_pct else 0
            ratings = agg.pop("avg_rating")
            agg["avg_product_rating"] = round(
                sum(ratings) / len(ratings), 2
            ) if ratings else None

        # Per-SKU return breakdown
        sku_return_agg = {}
        for row in returns:
            sku = row.get("productSKU")
            if not sku:
                continue
            if sku not in sku_return_agg:
                sku_return_agg[sku] = {
                    "productSKU":      sku,
                    "productCategory": row.get("productCategory"),
                    "total_returns":   0,
                    "return_reasons":  {},
                }
            agg = sku_return_agg[sku]
            agg["total_returns"] += row.get("total_returns", 0) or 0
            reason = row.get("buyersRemorseReason")
            if reason:
                agg["return_reasons"][reason] = (
                    agg["return_reasons"].get(reason, 0) + (row.get("total_returns", 0) or 0)
                )

        summary["returns"] = {
            "by_reason": reason_agg,
            "by_sku":    sku_return_agg,
        }

    # ── Slim summary for LLM context ─────────────────────────────────────────
    # Send top-N only — LLM doesn't need every combination

    if "by_supplier" in summary:
        suppliers_list = sorted(
            summary["by_supplier"].values(),
            key=lambda x: x.get("incident_rate_pct", 0),
            reverse=True
        )[:15]
        summary["by_supplier"] = suppliers_list

    if "incidents" in summary and "by_sku" in summary["incidents"]:
        skus = sorted(
            summary["incidents"]["by_sku"].values(),
            key=lambda x: x.get("total_incidents", 0),
            reverse=True
        )[:20]
        summary["incidents"]["by_sku"] = skus

    if "returns" in summary and "by_sku" in summary["returns"]:
        skus = sorted(
            summary["returns"]["by_sku"].values(),
            key=lambda x: x.get("total_returns", 0),
            reverse=True
        )[:20]
        summary["returns"]["by_sku"] = skus

    return summary


# ── Guardrail check ───────────────────────────────────────────────────────────

def guardrail_check(query_results: dict, config: dict) -> list:
    """
    Validate query results before passing to LLM.
    Checks for blocked columns. Returns list of violations.
    """
    violations      = []
    blocked_columns = config["security"].get("blocked_columns", [])

    for table_name, rows in query_results.items():
        if not rows:
            continue
        columns = list(rows[0].keys())
        for col in blocked_columns:
            if col in columns:
                violations.append(
                    f"Blocked column '{col}' found in {table_name} — "
                    f"pipeline stopped. Review Pull node column allowlist."
                )

    return violations


# ── LLM analysis call ─────────────────────────────────────────────────────────

def analyse_with_llm(
    client:      Anthropic,
    summary:     dict,
    audience:    str,
    supplier_id: Optional[str],
    report_type: str,
) -> dict:
    """
    LLM call 3 — structured JSON analysis.
    Receives pre-processed data summary, returns structured analysis JSON.
    No free text — Generate node handles narrative.
    """
    is_supplier = audience == "supplier"

    system_prompt = """You are the Analyse node of a supplier performance BI agent.
You receive pre-processed supplier data and return a structured JSON analysis.

CRITICAL RULES:
- Return ONLY valid JSON — no prose, no markdown, no explanation outside the JSON
- Every number must come from the data provided — never invent figures
- If data is insufficient for a conclusion, set confidence lower and flag it
- Do not include raw row data — only derived insights
- Keep string values concise — no paragraph-length strings inside JSON values

Your JSON must follow this exact structure:
{
  "confidence": 0.0-1.0,
  "flags": ["short warning strings only"],
  "overall_metrics": {
    "total_orders": number,
    "total_gross_revenue": number,
    "overall_incident_rate_pct": number,
    "overall_return_rate_pct": number,
    "total_resolution_cost": number,
    "vs_benchmark": "brief note"
  },
  "top_issues": [
    {
      "rank": 1,
      "type": "incident_rate/return_rate/resolution_cost/trend",
      "description": "specific finding with numbers — one sentence",
      "severity": "high/medium/low",
      "supplier_id": "SUPXXX or null",
      "category": "category name or null",
      "sku": "SKU code or null"
    }
  ],
  "by_category": [
    {
      "category": "name",
      "incident_rate_pct": number,
      "return_rate_pct": number,
      "vs_portfolio_average": "above/below X%",
      "primary_incident_type": "type name",
      "top_issue_skus": ["SKU1", "SKU2"]
    }
  ],
  "by_supplier": [
    {
      "supplier_id": "SUPXXX",
      "supplier_name": "name",
      "incident_rate_pct": number,
      "return_rate_pct": number,
      "total_resolution_cost": number,
      "vs_tier_average": "above/below X%",
      "primary_issue": "one sentence"
    }
  ],
  "improvement_actions": [
    {
      "priority": 1,
      "scope": "portfolio/category/supplier/sku",
      "target": "what this applies to",
      "action": "specific recommended action — one sentence",
      "rationale": "data-driven reason with numbers — one sentence",
      "expected_impact": "expected improvement — one sentence"
    }
  ],
  "anomalies": [
    {
      "description": "specific anomaly with numbers — one sentence",
      "affected": "identifier",
      "signal": "data pattern — one sentence"
    }
  ]
}"""

    if is_supplier:
        focus = f"""This is a SUPPLIER ACCOUNT report for {supplier_id}.
Focus on:
1. How this supplier's rates compare to category and tier averages
2. Which categories are above benchmark and why
3. Which specific SKUs are driving the rates — name them
4. What customers are experiencing (incident types and return reasons)
5. Concrete improvement actions per problematic SKU

Limit top_issues to 5. Limit improvement_actions to 6. Limit by_category to all relevant categories."""
    else:
        focus = """This is a BUSINESS OVERVIEW report for internal leadership.
Focus on:
1. Portfolio-level health — overall rates vs internal averages
2. Which suppliers are outliers — high and low performers
3. Which categories have structural issues
4. Fulfilment channel signals
5. Top priority portfolio-level actions

Limit top_issues to 5. Limit by_supplier to top 10 by incident rate. Limit improvement_actions to 5."""

    user_prompt = f"""Analyse this supplier performance data and return structured JSON.

Report type: {report_type}
{focus}

Data:
{json.dumps(summary, indent=2, default=str)}

Return only the JSON object. Keep all string values concise — one sentence maximum per field."""

    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            break
        except Exception as e:
            if "529" in str(e) or "overloaded" in str(e).lower():
                if attempt < 2:
                    wait = 30 * (attempt + 1)
                    print(f"  [analyse] API overloaded — retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
            else:
                raise

    raw = response.content[0].text.strip()

    # Strip markdown if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.lower().startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    # Parse with fallback salvage on truncation
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Try to salvage truncated JSON by finding last complete object
        last_brace = raw.rfind("}")
        if last_brace > 0:
            try:
                salvaged = json.loads(raw[:last_brace + 1])
                print(f"  [analyse] WARNING — JSON was truncated, salvaged partial response")
                return salvaged
            except json.JSONDecodeError:
                pass
        raise ValueError(
            f"[analyse] LLM returned invalid JSON: {e}\nRaw: {raw[:500]}"
        )


# ── Analyse node ──────────────────────────────────────────────────────────────

def analyse_node(state: dict) -> dict:
    """
    Node 3 — Analyse.

    Reads:  state.query_results, state.audience, state.supplier_id,
            state.report_type

    Writes: state.analysis, state.confidence, state.flags, state.errors
    """
    print("  [analyse] Starting...")

    config        = _load_config()
    query_results = state.get("query_results", {})
    audience      = state["audience"]
    supplier_id   = state.get("supplier_id")
    report_type   = state["report_type"]
    errors        = list(state.get("errors") or [])

    project = config["project"]
    dataset = config["dataset"]

    # ── Guardrail check ───────────────────────────────────────────────────────
    print("  [analyse] Running guardrail check...")
    violations = guardrail_check(query_results, config)
    if violations:
        print(f"  [analyse] GUARDRAIL VIOLATION — stopping pipeline:")
        for v in violations:
            print(f"    - {v}")
        raise ValueError(
            f"[analyse] Guardrail violations detected — pipeline stopped: {violations}"
        )
    print("  [analyse] Guardrail check passed")

    # ── Pre-process data ──────────────────────────────────────────────────────
    print("  [analyse] Pre-processing query results...")
    summary = preprocess_results(
        query_results = query_results,
        audience      = audience,
        supplier_id   = supplier_id,
        project       = project,
        dataset       = dataset,
    )

    total_orders = summary.get("overall", {}).get("total_orders", 0)
    print(f"  [analyse] Pre-processing complete — {total_orders:,} orders in scope")

    # ── LLM analysis call ─────────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set.")

    client = Anthropic(api_key=api_key)

    print("  [analyse] Calling LLM for structured analysis...")
    analysis = analyse_with_llm(
        client      = client,
        summary     = summary,
        audience    = audience,
        supplier_id = supplier_id,
        report_type = report_type,
    )

    # ── Validate analysis structure ───────────────────────────────────────────
    required_keys = [
        "confidence", "flags", "overall_metrics",
        "top_issues", "improvement_actions"
    ]
    missing = [k for k in required_keys if k not in analysis]
    if missing:
        errors.append(f"Analysis missing required fields: {missing}")
        print(f"  [analyse] WARNING — missing fields: {missing}")

    confidence = float(analysis.get("confidence", 0.5))
    flags      = analysis.get("flags", [])

    print(f"  [analyse] Complete — confidence: {confidence:.2f}")
    if flags:
        for f in flags:
            print(f"  [analyse] Flag: {f}")

    return {
        "analysis":    analysis,
        "confidence":  confidence,
        "flags":       flags,
        "current_node":"analyse",
        "errors":      errors,
    }