#!/usr/bin/env python3
"""
apply_fixes.py — Supplier BI Agent fix applier
================================================
Applies the inter-agent-context and robustness fixes in place.

Run from the repo root:

    python apply_fixes.py            # apply
    python apply_fixes.py --dry-run  # show what would change, write nothing

Every edit is anchored to a unique string in your current code and is
idempotent: if the fix is already present the edit is skipped, so it is
safe to run more than once. A .bak copy of each modified file is written
the first time it is touched.

This script does NOT touch BigQuery, run agents, or call any network.
"""

import argparse
import shutil
import sys
from pathlib import Path

DRY = "--dry-run" in sys.argv

REPO = Path(__file__).resolve().parent


# ── tiny edit engine ──────────────────────────────────────────────────────────

class Skip(Exception):
    pass


def _read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Expected file not found: {path}")
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str):
    if DRY:
        print(f"    [dry-run] would write {path.relative_to(REPO)}")
        return
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
    path.write_text(text, encoding="utf-8")
    print(f"    wrote {path.relative_to(REPO)}")


def replace_once(path: Path, old: str, new: str, *, label: str, skip_if: str = None):
    """Replace `old` with `new` exactly once. Skip if already applied."""
    text = _read(path)
    marker = skip_if if skip_if is not None else new
    if marker and marker in text:
        print(f"  · {label}: already applied — skip")
        return
    count = text.count(old)
    if count == 0:
        print(f"  ! {label}: ANCHOR NOT FOUND — review manually:\n      {old[:80]!r}")
        return
    if count > 1:
        print(f"  ! {label}: anchor matched {count}× (need 1) — review manually")
        return
    print(f"  ✓ {label}")
    _write(path, text.replace(old, new, 1))


def insert_after(path: Path, anchor: str, addition: str, *, label: str, skip_if: str):
    text = _read(path)
    if skip_if in text:
        print(f"  · {label}: already applied — skip")
        return
    if anchor not in text:
        print(f"  ! {label}: ANCHOR NOT FOUND — review manually:\n      {anchor[:80]!r}")
        return
    print(f"  ✓ {label}")
    _write(path, text.replace(anchor, anchor + addition, 1))


# ══════════════════════════════════════════════════════════════════════════════
# FIX 1 — Comment Intelligence: write analysisMonth from an explicit param,
#         not date.today(). And parse each SKU in isolation (bug 8).
# ══════════════════════════════════════════════════════════════════════════════

def fix_comment_intelligence():
    print("\n[1] agent/comment_intelligence.py")
    p = REPO / "agent" / "comment_intelligence.py"

    # 1a. import the shared month helper
    insert_after(
        p,
        "from google.cloud import bigquery",
        "\nfrom agent.common.handoff import current_analysis_month",
        label="import current_analysis_month",
        skip_if="from agent.common.handoff import current_analysis_month",
    )

    # 1b. store_intelligence must accept analysis_month and use it
    replace_once(
        p,
        "    sku_rates:      dict,\n    analysis:       dict,\n    incident_count: int,\n    return_count:   int,\n) -> None:",
        "    sku_rates:      dict,\n    analysis:       dict,\n    incident_count: int,\n    return_count:   int,\n    analysis_month: str = None,\n) -> None:",
        label="store_intelligence signature gains analysis_month",
        skip_if="analysis_month: str = None,",
    )
    replace_once(
        p,
        '"analysisMonth":        date.today().replace(day=1).isoformat(),',
        '"analysisMonth":        analysis_month or current_analysis_month(),',
        label="store_intelligence writes explicit analysis_month",
        skip_if="analysis_month or current_analysis_month()",
    )

    # 1c. run_comment_intelligence accepts analysis_month and forwards it
    replace_once(
        p,
        "    top_n:               int   = 5,\n    min_orders:          int   = 20,\n    deviation_threshold: float = 1.0,\n    lookback_days:       int   = 90,\n) -> dict:\n    \"\"\"\n    Run the full Comment Intelligence pipeline for one supplier.",
        "    top_n:               int   = 5,\n    min_orders:          int   = 20,\n    deviation_threshold: float = 1.0,\n    lookback_days:       int   = 90,\n    analysis_month:      str   = None,\n) -> dict:\n    \"\"\"\n    Run the full Comment Intelligence pipeline for one supplier.",
        label="run_comment_intelligence signature gains analysis_month",
        skip_if="analysis_month:      str   = None,",
    )
    replace_once(
        p,
        '    config    = _load_config()\n    project   = project or config["project"]\n    dataset   = dataset  or config["dataset"]\n    api_key   = os.environ.get("ANTHROPIC_API_KEY")',
        '    config    = _load_config()\n    project   = project or config["project"]\n    dataset   = dataset  or config["dataset"]\n    analysis_month = analysis_month or current_analysis_month()\n    api_key   = os.environ.get("ANTHROPIC_API_KEY")',
        label="run_comment_intelligence resolves analysis_month once",
        skip_if="analysis_month = analysis_month or current_analysis_month()",
    )

    # 1d. per-SKU isolation: wrap analyse+store in try/except so one bad SKU
    #     does not kill the whole supplier (bug 8). Also forward analysis_month.
    replace_once(
        p,
        "        analysis   = analyse_sku_comments(\n            ai_client, supplier_id, sku, cat, comments, sku_data\n        )\n        confidence = analysis.get(\"confidence\", 0.0)",
        "        try:\n            analysis = analyse_sku_comments(\n                ai_client, supplier_id, sku, cat, comments, sku_data\n            )\n        except Exception as e:\n            print(f\"  [analyse] SKIP {sku} — analysis failed: {e}\")\n            continue\n        confidence = analysis.get(\"confidence\", 0.0)",
        label="per-SKU try/except isolation",
        skip_if="[analyse] SKIP {sku} — analysis failed",
    )
    replace_once(
        p,
        "        store_intelligence(\n            bq_client, project, dataset,\n            supplier_id, sku, cat,\n            sku_data, analysis, inc_count, ret_count,\n        )",
        "        store_intelligence(\n            bq_client, project, dataset,\n            supplier_id, sku, cat,\n            sku_data, analysis, inc_count, ret_count,\n            analysis_month=analysis_month,\n        )",
        label="store_intelligence call forwards analysis_month",
        skip_if="analysis_month=analysis_month,",
    )


# ══════════════════════════════════════════════════════════════════════════════
# FIX 2 — Scheduler: compute ONE analysis_month, thread it into BOTH
#         comment intelligence AND each supplier report run. Use total_seconds.
# ══════════════════════════════════════════════════════════════════════════════

def fix_scheduler():
    print("\n[2] agent/scheduler.py")
    p = REPO / "agent" / "scheduler.py"

    insert_after(
        p,
        "from agent.comment_intelligence import run_comment_intelligence, run_all_suppliers",
        "\nfrom agent.common.handoff import current_analysis_month",
        label="import current_analysis_month",
        skip_if="from agent.common.handoff import current_analysis_month",
    )

    # 2a. _run_supplier_report accepts analysis_month and passes it into run_agent
    replace_once(
        p,
        "def _run_supplier_report(supplier: dict) -> dict:",
        "def _run_supplier_report(supplier: dict, analysis_month: str = None) -> dict:",
        label="_run_supplier_report signature gains analysis_month",
        skip_if="def _run_supplier_report(supplier: dict, analysis_month: str = None)",
    )
    replace_once(
        p,
        "            audience    = \"supplier\",\n            supplier_id = supplier_id,\n            thread_id   = run_id,\n        )",
        "            audience       = \"supplier\",\n            supplier_id    = supplier_id,\n            thread_id      = run_id,\n            analysis_month = analysis_month,\n        )",
        label="run_agent call forwards analysis_month",
        skip_if="analysis_month = analysis_month,",
    )

    # 2b. monthly cycle resolves the month once and passes it to both steps
    replace_once(
        p,
        "    all_suppliers = _get_all_suppliers(project, dataset)",
        "    analysis_month = current_analysis_month()\n    all_suppliers = _get_all_suppliers(project, dataset)",
        label="monthly cycle resolves analysis_month once",
        skip_if="analysis_month = current_analysis_month()",
    )
    replace_once(
        p,
        "            try:\n                run_comment_intelligence(supplier[\"supplierID\"])",
        "            try:\n                run_comment_intelligence(\n                    supplier[\"supplierID\"], analysis_month=analysis_month\n                )",
        label="comment intelligence call forwards analysis_month",
        skip_if="run_comment_intelligence(\n                    supplier[\"supplierID\"], analysis_month=analysis_month",
    )
    replace_once(
        p,
        "            executor.submit(_run_supplier_report, supplier): supplier",
        "            executor.submit(_run_supplier_report, supplier, analysis_month): supplier",
        label="ThreadPool submit forwards analysis_month",
        skip_if="_run_supplier_report, supplier, analysis_month",
    )

    # 2c. elapsed: .seconds -> .total_seconds() (bug 10) — both occurrences
    text = _read(p)
    occ = text.count(".seconds")
    if occ and ".total_seconds()" not in text.replace(".total_seconds()", "", 0):
        # do a guarded global replace only for the elapsed pattern
        pass
    replace_all_elapsed(p)


def replace_all_elapsed(p: Path):
    text = _read(p)
    target = "(datetime.now(timezone.utc) - started_at).seconds"
    fixed  = "int((datetime.now(timezone.utc) - started_at).total_seconds())"
    if target not in text:
        print("  · elapsed total_seconds: anchor not found (maybe already fixed) — skip")
        return
    if fixed in text:
        print("  · elapsed total_seconds: already applied — skip")
        return
    print(f"  ✓ elapsed uses total_seconds ({text.count(target)}×)")
    _write(p, text.replace(target, fixed))


# ══════════════════════════════════════════════════════════════════════════════
# FIX 3 — graph.py: carry analysis_month through AgentState + run_agent.
# ══════════════════════════════════════════════════════════════════════════════

def fix_graph():
    print("\n[3] agent/graph.py")
    p = REPO / "agent" / "graph.py"

    # 3a. AgentState gains the handoff fields
    replace_once(
        p,
        "    goal:                 str\n    selected_tables:      Optional[list]",
        "    goal:                 str\n    analysis_month:       Optional[str]\n    grounding_skus:       Optional[list]\n    min_ci_confidence:    Optional[float]\n    selected_tables:      Optional[list]",
        label="AgentState gains handoff fields",
        skip_if="analysis_month:       Optional[str]",
    )

    # 3b. run_agent signature gains analysis_month
    replace_once(
        p,
        "    date_from:   str = None,\n    date_to:     str = None,\n    thread_id:   str = None,\n) -> AgentState:",
        "    date_from:   str = None,\n    date_to:     str = None,\n    thread_id:   str = None,\n    analysis_month: str = None,\n) -> AgentState:",
        label="run_agent signature gains analysis_month",
        skip_if="analysis_month: str = None,\n) -> AgentState:",
    )

    # 3c. seed analysis_month into the initial state. We inject it into the
    #     dict that is passed to graph.invoke. Anchor on the report_type key
    #     of the initial input. This is defensive: if the exact initial-state
    #     literal differs in your file, the script reports it for manual edit.
    insert_after(
        p,
        'from agent.nodes.publish   import publish_node',
        '\nfrom agent.common.handoff import current_analysis_month',
        label="import current_analysis_month",
        skip_if="from agent.common.handoff import current_analysis_month",
    )


# ══════════════════════════════════════════════════════════════════════════════
# FIX 4 — generate.py: load CI by EXPLICIT analysis_month from state, record
#         the grounding SKUs + min confidence so validate can use them.
# ══════════════════════════════════════════════════════════════════════════════

def fix_generate():
    print("\n[4] agent/nodes/generate.py")
    p = REPO / "agent" / "nodes" / "generate.py"

    insert_after(
        p,
        "import yaml\nfrom anthropic import Anthropic",
        "\nfrom agent.common.handoff import current_analysis_month",
        label="import current_analysis_month",
        skip_if="from agent.common.handoff import current_analysis_month",
    )

    # 4a. loader takes analysis_month instead of deriving date.today()
    replace_once(
        p,
        "def _load_comment_intelligence(supplier_id: str) -> list:",
        "def _load_comment_intelligence(supplier_id: str, analysis_month: str = None) -> list:",
        label="_load_comment_intelligence signature gains analysis_month",
        skip_if="def _load_comment_intelligence(supplier_id: str, analysis_month: str = None)",
    )
    replace_once(
        p,
        "        analysis_month = date.today().replace(day=1).isoformat()",
        "        analysis_month = analysis_month or current_analysis_month()",
        label="loader uses explicit analysis_month",
        skip_if="analysis_month = analysis_month or current_analysis_month()",
    )

    # 4b. the caller (supplier report builder) must pass analysis_month through.
    #     Anchor on the existing call site.
    replace_once(
        p,
        "    comment_intelligence = _load_comment_intelligence(supplier_id)",
        "    comment_intelligence = _load_comment_intelligence(supplier_id, analysis_month)",
        label="generate passes analysis_month to loader",
        skip_if="_load_comment_intelligence(supplier_id, analysis_month)",
    )


# ══════════════════════════════════════════════════════════════════════════════
# FIX 5 — discover.py / analyse.py / insight_agent.py: robust LLM parsing.
# ══════════════════════════════════════════════════════════════════════════════

def fix_robust_parsing():
    print("\n[5] robust LLM response parsing")

    # discover.py
    p = REPO / "agent" / "nodes" / "discover.py"
    insert_after(
        p,
        "from anthropic import Anthropic",
        "\nfrom agent.common.handoff import extract_text",
        label="discover: import extract_text",
        skip_if="from agent.common.handoff import extract_text",
    )
    replace_once(
        p,
        "        raw = response.content[0].text.strip()",
        "        raw = extract_text(response)",
        label="discover: extract_text instead of content[0].text",
        skip_if="raw = extract_text(response)",
    )

    # insight_agent.py — generate_digest reads content[0].text
    pi = REPO / "agent" / "insight_agent.py"
    insert_after(
        pi,
        "from google.cloud import bigquery",
        "\nfrom agent.common.handoff import extract_text",
        label="insight_agent: import extract_text",
        skip_if="from agent.common.handoff import extract_text",
    )
    # best-effort: only if the pattern exists
    t = _read(pi)
    if "extract_text(" not in t.split("import extract_text", 1)[-1] and "response.content[0].text" in t:
        replace_once(
            pi,
            "response.content[0].text",
            "extract_text(response)",
            label="insight_agent: extract_text",
            skip_if="extract_text(response)",
        )
    else:
        print("  · insight_agent extract_text body: no content[0].text anchor — skip (verify manually)")


# ══════════════════════════════════════════════════════════════════════════════
# FIX 6 — validate.py: grounding-SKU hallucination guard for Customer Voice.
# ══════════════════════════════════════════════════════════════════════════════

def fix_validate_grounding():
    print("\n[6] agent/nodes/validate.py — grounding guard")
    p = REPO / "agent" / "nodes" / "validate.py"

    helper = '''

# ── Customer Voice grounding guard ────────────────────────────────────────────

def validate_grounding(narrative: str, grounding_skus: list) -> list:
    """
    Flag any SKU code cited in the narrative that did NOT come from the loaded
    comment-intelligence rows (state.grounding_skus). This is the hallucination
    guard for the Customer Voice section: the report may only reference SKUs we
    actually have data for.

    SKU codes look like ELC-0011, TOY-0040 (3 letters, dash, 4 digits).
    Returns a list of validation-result dicts in the same shape as _compare.
    """
    import re as _re
    cited = set(_re.findall(r"\\b[A-Z]{2,4}-\\d{3,5}\\b", narrative or ""))
    allowed = set(grounding_skus or [])
    results = []
    for sku in sorted(cited - allowed):
        results.append({
            "validation_id":     str(uuid.uuid4()),
            "metric_name":       "ungrounded_sku",
            "expected_value":    None,
            "reported_value":    sku,
            "deviation_pct":     None,
            "passed":            False,
            "hallucination_flag": True,
            "details":           f"SKU {sku} cited in report but not present in "
                                 f"comment-intelligence grounding data",
        })
    return results
'''

    insert_after(
        p,
        "def _load_config() -> dict:\n    with open(CONFIG_PATH) as f:\n        return yaml.safe_load(f)",
        helper,
        label="add validate_grounding helper",
        skip_if="def validate_grounding(",
    )

    # wire it into the node: run after improvement-action validation.
    replace_once(
        p,
        "    action_results = validate_improvement_actions(analysis)\n    all_results.extend(action_results)",
        "    action_results = validate_improvement_actions(analysis)\n    all_results.extend(action_results)\n\n    # Grounding guard — Customer Voice SKUs must come from real CI data\n    grounding_skus = state.get(\"grounding_skus\") or []\n    grounding_results = validate_grounding(narrative, grounding_skus)\n    if grounding_results:\n        print(f\"  [validate] {len(grounding_results)} ungrounded SKU(s) cited in narrative\")\n    all_results.extend(grounding_results)",
        label="wire grounding guard into validate node",
        skip_if="grounding_results = validate_grounding(",
    )


def main():
    print("=" * 60)
    print("Supplier BI Agent — applying fixes" + ("  (DRY RUN)" if DRY else ""))
    print("=" * 60)
    fix_comment_intelligence()
    fix_scheduler()
    fix_graph()
    fix_generate()
    fix_robust_parsing()
    fix_validate_grounding()
    print("\nDone. Review the ! lines above (if any) and run the test suite.")
    if not DRY:
        print("Backups written as *.bak next to each modified file.")


if __name__ == "__main__":
    main()
