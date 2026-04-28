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
                Layer 4: Customer Voice — exhaustive comment intelligence for
                         problem SKUs from sku_comment_intelligence table

Output:
  report_narrative — human-readable markdown report
  report_json      — structured JSON for BigQuery storage
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


# ── Comment intelligence loader ───────────────────────────────────────────────

def _load_comment_intelligence(supplier_id: str) -> list:
    """
    Loads this month's comment intelligence for the supplier from BigQuery.
    Returns empty list if no data available — report still generates without it.
    """
    try:
        from google.cloud import bigquery
        config  = _load_config()
        project = config["project"]
        dataset = config["dataset"]
        client  = bigquery.Client(project=project)

        analysis_month = date.today().replace(day=1).isoformat()

        rows = list(client.query(f"""
            SELECT
                productSKU,
                productCategory,
                skuIncidentRate,
                catIncidentRate,
                skuReturnRate,
                catReturnRate,
                maxDeviation,
                incidentCommentCount,
                returnCommentCount,
                incidentThemes,
                returnThemes,
                rootCauses,
                improvements,
                confidence
            FROM `{project}.{dataset}.sku_comment_intelligence`
            WHERE supplierID = '{supplier_id}'
              AND analysisMonth = '{analysis_month}'
            ORDER BY maxDeviation DESC
        """).result())

        intelligence = []
        for row in rows:
            d = dict(row)
            for field in ["incidentThemes", "returnThemes", "rootCauses", "improvements"]:
                if d.get(field) and isinstance(d[field], str):
                    try:
                        d[field] = json.loads(d[field])
                    except Exception:
                        d[field] = []
            intelligence.append(d)

        print(f"  [generate] Loaded comment intelligence for {len(intelligence)} SKUs")
        return intelligence

    except Exception as e:
        print(f"  [generate] WARNING — could not load comment intelligence: {e}")
        return []


# ── Business overview report generator ───────────────────────────────────────

def generate_business_report(
    client:      Anthropic,
    analysis:    dict,
    report_type: str,
    goal:        str = "",
) -> str:
    """
    LLM call 4a — generate business overview narrative.
    For internal leadership. Portfolio-level, all suppliers.
    """
    goal_line = f"The user specifically asked for: {goal}\n" if goal else ""

    system_prompt = f"""You are generating a supplier performance report for internal leadership.
Write in clear, professional business language.

The report type is: {report_type}
{goal_line}
STRUCTURE RULES:
- For SCHEDULED reports (weekly/monthly overview): use fixed sections:
  ## Executive Summary, ## Portfolio Performance, ## Supplier Rankings,
  ## Category Analysis, ## Fulfilment Channel Signals, ## Priority Actions, ## Anomalies & Watch Items
- For AD-HOC reports: structure the report around what was actually asked.
  Only include sections relevant to the goal. Do not pad with empty sections.
  If asked for sales figures and a period comparison, structure around that comparison.
  If asked about a single supplier, focus on that supplier.
  Always include ## Key Findings and ## Recommendations at minimum.

CONTENT RULES:
- Every claim must be backed by a number from the analysis
- Name suppliers, categories, and SKUs explicitly
- Be direct — this is a business report, not an academic paper
- No hedging language unless confidence is genuinely low
- Do NOT include sections about topics that were not asked about and have no data
- If a section would be empty or irrelevant to the goal, omit it entirely

DATA INTEGRITY RULES — CRITICAL:
- If comparing two time periods and one period has significantly fewer orders (less than 50%
  of the other period's order count), explicitly flag this as a data limitation
- If the earliest data point is close to the start of a comparison period, flag that the
  comparison period may be incomplete — state what dates ARE covered
- If any metric is zero when it logically should not be (e.g. resolution cost for a supplier
  with known incidents), flag it as a data gap, not a finding
- Never present partial-period data as a full-period figure without stating it is partial
- If a period comparison was requested but data only covers part of one period, state clearly
  what IS available and what IS NOT before drawing any conclusions"""

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


# ── Supplier account report generator ────────────────────────────────────────

def generate_supplier_report(
    client:      Anthropic,
    analysis:    dict,
    supplier_id: str,
    report_type: str,
) -> str:
    """
    LLM call 4b — generate supplier account narrative.
    For both the account manager and the supplier directly.
    Includes Customer Voice section from comment intelligence if available.
    """
    # Extract supplier name
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

    # Load comment intelligence for this supplier
    comment_intelligence = _load_comment_intelligence(supplier_id)

    # Format comment intelligence for the prompt
    ci_section = ""
    if comment_intelligence:
        ci_section = "\n\n## COMMENT INTELLIGENCE DATA\n"
        ci_section += "The following is exhaustive analysis of all customer comments for problem SKUs.\n"
        ci_section += "Use this data to write the Customer Voice section of the report.\n\n"
        for sku_intel in comment_intelligence:
            ci_section += f"### SKU {sku_intel['productSKU']} ({sku_intel['productCategory']})\n"
            ci_section += f"Incident rate: {sku_intel['skuIncidentRate']}% vs {sku_intel['catIncidentRate']}% category avg\n"
            ci_section += f"Return rate: {sku_intel['skuReturnRate']}% vs {sku_intel['catReturnRate']}% category avg\n"
            ci_section += f"Max deviation: {sku_intel['maxDeviation']}pp above category average\n"
            ci_section += f"Comments analysed: {sku_intel['incidentCommentCount']} incident, {sku_intel['returnCommentCount']} return\n"
            ci_section += f"Analysis confidence: {sku_intel['confidence']:.2f}\n\n"

            if sku_intel.get("incidentThemes"):
                ci_section += "**Incident themes:**\n"
                for t in sku_intel["incidentThemes"]:
                    ci_section += f"- {t['theme']} (frequency: {t['frequency']}, severity: {t['severity']})\n"
                    ci_section += f"  Evidence: {t['evidence']}\n"

            if sku_intel.get("returnThemes"):
                ci_section += "\n**Return themes:**\n"
                for t in sku_intel["returnThemes"]:
                    ci_section += f"- {t['theme']} (frequency: {t['frequency']}, severity: {t['severity']})\n"
                    ci_section += f"  Evidence: {t['evidence']}\n"

            if sku_intel.get("rootCauses"):
                ci_section += "\n**Root causes:**\n"
                for c in sku_intel["rootCauses"]:
                    ci_section += f"- [{c['category'].upper()} — {c['confidence']} confidence] {c['cause']}\n"

            if sku_intel.get("improvements"):
                ci_section += "\n**Recommended improvements:**\n"
                for i in sku_intel["improvements"]:
                    ci_section += f"- Priority {i['priority']} [{i['effort']} effort]: {i['action']}\n"
                    ci_section += f"  Expected impact: {i['expected_impact']}\n"

            ci_section += "\n---\n"

    # Build the customer voice section instruction
    cv_instruction = ""
    if comment_intelligence:
        cv_instruction = """
## Customer Voice — Problem SKU Analysis
This section uses exhaustive analysis of all customer comments for problem SKUs.
For EACH SKU in the comment intelligence data:

### [SKU code] — [Category] — [deviation]pp above category average
**What customers are reporting:**
Synthesise the incident and return themes into clear customer experience statements.
Group by severity. Quote or paraphrase specific evidence where it strengthens the point.

**Root causes identified:**
List root causes with confidence level and category (packaging/product_quality/listing_accuracy/fulfilment).

**Improvement actions:**
Reproduce the prioritised improvement actions from the comment intelligence.
Add any additional context from the quantitative analysis.

**Impact if resolved:**
Calculate the approximate incident/return rate reduction and cost saving
if this SKU returned to category average performance.

"""
    else:
        cv_instruction = """
## Customer Voice
No comment intelligence data available for this reporting period.
Note that comment analysis runs monthly and will be available from next period.

"""

    system_prompt = f"""You are generating a supplier performance account report for {supplier_name}.
This report is read by both the account manager and the supplier directly.
Write in clear, professional language that is direct but constructive.

Structure the report exactly as follows — use these markdown headers:

## Account Summary — {supplier_name}
Report period and 2-3 sentence summary of overall performance vs benchmark.

## Performance vs Benchmark
Simple table: Metric | This Supplier | Category Average | Portfolio Average
Include: incident rate, return rate, resolution cost.

## Category Performance
For EACH category this supplier operates in:
### [Category Name]
- Incident rate vs portfolio average for this category
- Primary incident types (with percentages)
- Return rate and primary return reasons
- Above or below benchmark verdict

## Problematic SKUs — Deep Dive
For each category above benchmark, identify specific SKUs driving it.
For EACH problematic SKU:
### SKU [code] — [category]
- Incident rate, most common incident types, resolution cost impact
- Specific call to action
{cv_instruction}
## Improvement Plan
Numbered priority actions. Most impactful first.
For EACH action:
**Action [N]: [title]**
- What: specific action required
- Why: data that justifies this (include numbers)
- Target: which SKU/category/process
- Expected impact: what improvement is expected

## Positive Signals
Areas where this supplier is performing well. Always include at least one.

RULES:
- Every claim must be backed by a number from the analysis or comment intelligence
- Name SKUs explicitly — never say "some SKUs" if you have the data
- The improvement plan must be actionable — specific enough for the supplier to act on
- Tone: direct, professional, constructive — not punitive
- If comment intelligence is provided, use it extensively in the Customer Voice section

DATA INTEGRITY RULES — CRITICAL:
- If comparing two time periods and one period has significantly fewer orders (less than 50%
  of the other period's order count), explicitly flag this as a data limitation
- If the earliest data point is close to the start of a comparison period, flag that the
  comparison period may be incomplete — state what dates ARE covered
- If any metric is zero when it logically should not be (e.g. resolution cost for a supplier
  with known incidents), flag it as a data gap, not a finding
- Never present partial-period data as a full-period figure without stating it is partial
- If a period comparison was requested but data only covers part of one period, state clearly
  what IS available and what IS NOT before drawing any conclusions"""

    user_prompt = f"""Generate a supplier account performance report for {supplier_name} ({supplier_id}).
Report type: {report_type}
Report date: {date.today().isoformat()}

Analysis data:
{json.dumps(analysis, indent=2, default=str)}
{ci_section}
Write the full report following the structure above.
Be specific — name SKUs, categories, and use numbers throughout.
If comment intelligence data is provided, use it fully in the Customer Voice section."""

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
    return {
        "reportID":           run_id,
        "reportType":         report_type,
        "audience":           audience,
        "supplierID":         supplier_id,
        "reportDate":         date.today().isoformat(),
        "confidence":         analysis.get("confidence"),
        "flags":              analysis.get("flags", []),
        "overallMetrics":     analysis.get("overall_metrics", {}),
        "topIssues":          analysis.get("top_issues", []),
        "byCategory":         analysis.get("by_category", []),
        "bySupplier":         analysis.get("by_supplier", []),
        "improvementActions": analysis.get("improvement_actions", []),
        "anomalies":          analysis.get("anomalies", []),
        "narrative":          narrative,
    }


# ── Output validator ──────────────────────────────────────────────────────────

def validate_report(narrative: str, report_json: dict) -> list:
    issues = []

    if len(narrative) < 500:
        issues.append("Report narrative is suspiciously short — may be incomplete")

    required_sections_business = [
        "Executive Summary", "Portfolio Performance",
        "Supplier Rankings", "Priority Actions"
    ]
    required_sections_supplier = [
        "Account Summary", "Performance vs Benchmark",
        "Category Performance", "Improvement"
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
    goal        = state.get("goal", "")
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
            goal        = goal,
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
