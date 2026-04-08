"""
Supplier BI Agent — Node 4: Generate
======================================
Takes the validated structured JSON from the Analyse node and
generates the final narrative report.

Two modes driven by audience:
  business  — portfolio overview for internal leadership
              executive summary, supplier rankings, category analysis,
              portfolio-level improvement actions

  supplier  — account management report for supplier and account manager
              three-layer structure:
                Layer 1: portfolio context — how supplier compares to benchmarks
                Layer 2: category drill-down — which categories are above benchmark
                Layer 3: SKU-level specifics — which SKUs are driving rates,
                         what customers are saying, concrete call to action per SKU

Output:
  report_narrative — human-readable markdown report
  report_json      — structured JSON for Looker Studio and BigQuery storage
"""

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Optional

import yaml
from anthropic import Anthropic


# ── Load metadata config ──────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / "config" / "metadata.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Business overview report generator ───────────────────────────────────────

def generate_business_report(
    client:      Anthropic,
    analysis:    dict,
    report_type: str,
) -> str:
    """
    LLM call 4a — generate business overview narrative.
    For internal leadership. Portfolio-level, all suppliers.
    """
    system_prompt = """You are generating a supplier performance business overview report
for internal leadership. Write in clear, professional business language.

Structure the report exactly as follows — use these markdown headers:

## Executive Summary
2-3 sentences. Overall portfolio health, most important finding, top priority action.

## Portfolio Performance
Key metrics table and narrative. Incident rate, return rate, resolution cost.
Compare to any available benchmarks. Note trends.

## Supplier Rankings
Rank suppliers by incident rate. Highlight outliers — both high performers and problems.
Include specific numbers. Name the suppliers.

## Category Analysis
Which categories have structural issues. Incident rates per category vs portfolio average.
Note which categories are supplier-driven vs category-driven.

## Fulfilment Channel Signals
Any patterns by fulfilment channel worth noting.

## Priority Actions
Numbered list of recommended actions. Most impactful first.
Each action must include: what to do, which supplier/category, expected impact.
Be specific — include supplier names, category names, and numbers.

## Anomalies & Watch Items
Any unusual patterns that need monitoring.

RULES:
- Every claim must be backed by a number from the analysis
- Name suppliers, categories, and SKUs explicitly
- Be direct — this is a business report, not an academic paper
- No hedging language unless confidence is genuinely low"""

    user_prompt = f"""Generate a supplier performance business overview report.
Report type: {report_type}
Report date: {date.today().isoformat()}

Analysis data:
{json.dumps(analysis, indent=2, default=str)}

Write the full report following the structure above."""

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
                    print(f"  [generate] API overloaded — retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
            else:
                raise

    return response.content[0].text.strip()

def generate_supplier_report(
    client:      Anthropic,
    analysis:    dict,
    supplier_id: str,
    report_type: str,
) -> str:
    """
    LLM call 4b — generate supplier account narrative.
    For both the account manager and the supplier directly.
    Three-layer structure: context → category drill-down → SKU specifics.
    """

    # Extract supplier name from analysis for personalisation
    supplier_name = supplier_id
    by_supplier = analysis.get("by_supplier", [])
    if isinstance(by_supplier, list):
        for s in by_supplier:
            if s.get("supplier_id") == supplier_id:
                supplier_name = s.get("supplier_name", supplier_id)
                break
    elif isinstance(by_supplier, dict):
        sup_data = by_supplier.get(supplier_id, {})
        supplier_name = sup_data.get("supplierName", supplier_id)

    system_prompt = f"""You are generating a supplier performance account report for {supplier_name}.
This report is read by both the account manager and the supplier directly.
Write in clear, professional language that is direct but constructive.

Structure the report exactly as follows — use these markdown headers:

## Account Summary — {supplier_name}
Report period and 2-3 sentence summary of this supplier's overall performance.
Lead with where they stand vs benchmark.

## Performance vs Benchmark
How does this supplier compare to:
- Category average incident rate
- Tier average incident rate
- Overall portfolio average
Use a simple table: Metric | This Supplier | Category Average | Tier Average

## Category Performance
For EACH category this supplier operates in:
### [Category Name]
- Incident rate for this category vs portfolio average for this category
- Primary incident types driving the rate (with percentages)
- Return rate and primary return reasons
- Whether this is above or below benchmark

## Problematic SKUs — Deep Dive
For each category that is above benchmark, identify the specific SKUs driving it.
For EACH problematic SKU:
### SKU [code] — [category]
- Incident rate for this SKU
- Most common incident types reported by customers
- What customers are saying (synthesise from incident types and return reasons)
- Resolution cost impact
- Specific call to action

## Improvement Plan
Numbered priority actions. Most impactful first.
For EACH action:
**Action [N]: [title]**
- What: specific action required
- Why: data that justifies this action (include numbers)
- Target: which SKU/category/process
- Expected impact: what improvement is expected

## Positive Signals
Any areas where this supplier is performing well or improving.
Always include at least one — constructive tone is important.

RULES:
- Every claim must be backed by a number from the analysis
- Name SKUs explicitly — never say "some SKUs" if you have the data
- The improvement plan must be actionable — specific enough for the supplier to act on
- Tone: direct, professional, constructive — not punitive"""

    user_prompt = f"""Generate a supplier account performance report for {supplier_name} ({supplier_id}).
Report type: {report_type}
Report date: {date.today().isoformat()}

Analysis data:
{json.dumps(analysis, indent=2, default=str)}

Write the full report following the structure above.
Be specific — name SKUs, categories, and use numbers throughout."""

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
                    print(f"  [generate] API overloaded — retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
            else:
                raise

    return response.content[0].text.strip()


# ── Report JSON builder ───────────────────────────────────────────────────────

def build_report_json(
    analysis:    dict,
    narrative:   str,
    report_type: str,
    audience:    str,
    supplier_id: Optional[str],
    run_id:      Optional[str],
) -> dict:
    """
    Build the structured JSON version of the report for BigQuery storage
    and Looker Studio consumption.
    """
    return {
        "reportID":        run_id,
        "reportType":      report_type,
        "audience":        audience,
        "supplierID":      supplier_id,
        "reportDate":      date.today().isoformat(),
        "confidence":      analysis.get("confidence"),
        "flags":           analysis.get("flags", []),
        "overallMetrics":  analysis.get("overall_metrics", {}),
        "topIssues":       analysis.get("top_issues", []),
        "byCategory":      analysis.get("by_category", []),
        "bySupplier":      analysis.get("by_supplier", []),
        "improvementActions": analysis.get("improvement_actions", []),
        "anomalies":       analysis.get("anomalies", []),
        "narrative":       narrative,
    }


# ── Output validator ──────────────────────────────────────────────────────────

def validate_report(narrative: str, report_json: dict) -> list:
    """
    Basic validation of the generated report.
    Returns list of issues — empty means report is valid.
    """
    issues = []

    if len(narrative) < 500:
        issues.append("Report narrative is suspiciously short — may be incomplete")

    required_sections_business = [
        "Executive Summary", "Portfolio Performance",
        "Supplier Rankings", "Priority Actions"
    ]
    required_sections_supplier = [
        "Account Summary", "Performance vs Benchmark",
        "Category Performance", "Improvement Plan"
    ]

    audience = report_json.get("audience", "business")
    required = required_sections_supplier if audience == "supplier" else required_sections_business

    for section in required:
        if section.lower() not in narrative.lower():
            issues.append(f"Required section missing from report: '{section}'")

    if not report_json.get("overallMetrics"):
        issues.append("Report JSON missing overallMetrics")

    if not report_json.get("improvementActions"):
        issues.append("Report JSON missing improvementActions")

    return issues


# ── Generate node ─────────────────────────────────────────────────────────────

def generate_node(state: dict) -> dict:
    """
    Node 4 — Generate.

    Reads:  state.analysis, state.confidence, state.audience,
            state.supplier_id, state.report_type, state.run_id

    Writes: state.report_narrative, state.report_json, state.errors
    """
    print("  [generate] Starting...")

    analysis    = state.get("analysis", {})
    audience    = state["audience"]
    supplier_id = state.get("supplier_id")
    report_type = state["report_type"]
    run_id      = state.get("run_id")
    errors      = list(state.get("errors") or [])

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set.")

    client = Anthropic(api_key=api_key)

    # ── Generate narrative ────────────────────────────────────────────────────
    if audience == "supplier":
        print(f"  [generate] Generating supplier account report for {supplier_id}...")
        narrative = generate_supplier_report(
            client      = client,
            analysis    = analysis,
            supplier_id = supplier_id,
            report_type = report_type,
        )
    else:
        print(f"  [generate] Generating business overview report...")
        narrative = generate_business_report(
            client      = client,
            analysis    = analysis,
            report_type = report_type,
        )

    print(f"  [generate] Narrative generated — {len(narrative):,} characters")

    # ── Build structured JSON ─────────────────────────────────────────────────
    report_json = build_report_json(
        analysis    = analysis,
        narrative   = narrative,
        report_type = report_type,
        audience    = audience,
        supplier_id = supplier_id,
        run_id      = run_id,
    )

    # ── Validate output ───────────────────────────────────────────────────────
    issues = validate_report(narrative, report_json)
    if issues:
        print(f"  [generate] Validation warnings:")
        for issue in issues:
            print(f"    - {issue}")
        errors.extend(issues)

    print(f"  [generate] Complete")

    return {
        "report_narrative": narrative,
        "report_json":      report_json,
        "current_node":     "generate",
        "errors":           errors,
    }