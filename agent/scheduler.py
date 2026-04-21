"""
Supplier BI Agent — Parallel Scheduler
=======================================
Runs the full monthly and weekly reporting cycle.

Monthly (1st of each month):
  - Comment Intelligence Agent for all 20 suppliers (sequential — data prep)
  - Monthly supplier account reports for all 20 suppliers (parallel)

Weekly (every Monday):
  - Weekly business overview report (single run)

All supplier reports run in parallel using ThreadPoolExecutor.
Each run is fully isolated — one failure does not affect others.
Results are written to BigQuery agent_runs and pending_reports as normal.

Usage:
  # Run monthly cycle
  python agent/scheduler.py monthly

  # Run weekly cycle
  python agent/scheduler.py weekly

  # Run monthly for specific suppliers only
  python agent/scheduler.py monthly --suppliers SUP001 SUP002 SUP003

  # Dry run — show what would run without executing
  python agent/scheduler.py monthly --dry-run
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import argparse
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import yaml
from google.cloud import bigquery

from agent.graph import run_agent
from agent.comment_intelligence import run_comment_intelligence, run_all_suppliers


# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "config" / "metadata.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _get_all_suppliers(project: str, dataset: str) -> list:
    """Fetch all active supplier IDs from BigQuery."""
    client = bigquery.Client(project=project)
    rows   = list(client.query(f"""
        SELECT supplierID, supplierName
        FROM `{project}.{dataset}.suppliers`
        ORDER BY supplierID
    """).result())
    return [dict(r) for r in rows]


# ── Single supplier runner (called in thread) ─────────────────────────────────

def _run_supplier_report(supplier: dict) -> dict:
    """
    Run a monthly supplier account report for one supplier.
    Returns a result dict — never raises, so one failure doesn't kill the batch.
    """
    supplier_id   = supplier["supplierID"]
    supplier_name = supplier.get("supplierName", supplier_id)
    run_id        = str(uuid.uuid4())
    started_at    = datetime.now(timezone.utc)

    try:
        run_agent(
            report_type = "monthly_supplier_account",
            goal        = (
                f"Monthly account report for {supplier_name}. "
                f"Identify problematic SKUs, incident breakdown by category, "
                f"return reasons, resolution costs, and provide a specific improvement plan."
            ),
            audience    = "supplier",
            supplier_id = supplier_id,
            thread_id   = run_id,
        )
        elapsed = (datetime.now(timezone.utc) - started_at).seconds
        return {
            "supplier_id":   supplier_id,
            "supplier_name": supplier_name,
            "run_id":        run_id,
            "status":        "success",
            "elapsed_s":     elapsed,
        }

    except Exception as e:
        elapsed = (datetime.now(timezone.utc) - started_at).seconds
        print(f"  [scheduler] ERROR — {supplier_id} failed after {elapsed}s: {e}")
        return {
            "supplier_id":   supplier_id,
            "supplier_name": supplier_name,
            "run_id":        run_id,
            "status":        "failed",
            "error":         str(e),
            "elapsed_s":     elapsed,
        }


# ── Monthly cycle ─────────────────────────────────────────────────────────────

def run_monthly_cycle(
    supplier_filter: list = None,
    max_workers:     int  = 5,
    skip_intelligence: bool = False,
    dry_run:         bool = False,
) -> dict:
    """
    Run the full monthly cycle:
      1. Comment Intelligence for all suppliers (sequential — must complete before reports)
      2. Monthly supplier account reports for all suppliers (parallel)

    Args:
        supplier_filter:    If set, only run for these supplier IDs
        max_workers:        Max parallel report runs (default 5 — balances API rate limits)
        skip_intelligence:  Skip comment intelligence step (useful if already ran today)
        dry_run:            Print what would run without executing
    """
    config  = _load_config()
    project = config["project"]
    dataset = config["dataset"]

    all_suppliers = _get_all_suppliers(project, dataset)

    if supplier_filter:
        suppliers = [s for s in all_suppliers if s["supplierID"] in supplier_filter]
        print(f"\n[scheduler] Filtered to {len(suppliers)} suppliers: {supplier_filter}")
    else:
        suppliers = all_suppliers

    print(f"\n{'='*60}")
    print(f"MONTHLY CYCLE — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")
    print(f"Suppliers:    {len(suppliers)}")
    print(f"Max workers:  {max_workers}")
    print(f"Skip intel:   {skip_intelligence}")
    print(f"Dry run:      {dry_run}")

    if dry_run:
        print(f"\n[dry-run] Would run Comment Intelligence for:")
        for s in suppliers:
            print(f"  {s['supplierID']} — {s['supplierName']}")
        print(f"\n[dry-run] Would run monthly reports in parallel for:")
        for s in suppliers:
            print(f"  {s['supplierID']} — {s['supplierName']}")
        return {"dry_run": True, "suppliers": len(suppliers)}

    started_at = datetime.now(timezone.utc)

    # ── Step 1: Comment Intelligence ──────────────────────────────────────────
    if not skip_intelligence:
        print(f"\n[scheduler] Step 1 — Comment Intelligence ({len(suppliers)} suppliers)...")
        for supplier in suppliers:
            try:
                run_comment_intelligence(supplier["supplierID"])
            except Exception as e:
                print(f"  [scheduler] WARNING — Comment Intelligence failed for "
                      f"{supplier['supplierID']}: {e}")
        print(f"[scheduler] Comment Intelligence complete")
    else:
        print(f"\n[scheduler] Step 1 — Comment Intelligence skipped")

    # ── Step 2: Parallel supplier reports ────────────────────────────────────
    print(f"\n[scheduler] Step 2 — Monthly reports ({len(suppliers)} suppliers, "
          f"{max_workers} parallel)...")

    results   = []
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_run_supplier_report, supplier): supplier
            for supplier in suppliers
        }
        for future in as_completed(futures):
            result     = future.result()
            completed += 1
            status_icon = "✓" if result["status"] == "success" else "✗"
            print(f"  [{completed}/{len(suppliers)}] {status_icon} {result['supplier_id']} "
                  f"— {result['status']} ({result['elapsed_s']}s)")
            results.append(result)

    # ── Summary ───────────────────────────────────────────────────────────────
    total_elapsed = (datetime.now(timezone.utc) - started_at).seconds
    succeeded = [r for r in results if r["status"] == "success"]
    failed    = [r for r in results if r["status"] == "failed"]

    print(f"\n{'='*60}")
    print(f"MONTHLY CYCLE COMPLETE — {total_elapsed}s total")
    print(f"{'='*60}")
    print(f"  Succeeded: {len(succeeded)}/{len(suppliers)}")
    print(f"  Failed:    {len(failed)}/{len(suppliers)}")

    if failed:
        print(f"\n  Failed suppliers:")
        for r in failed:
            print(f"    {r['supplier_id']}: {r.get('error', 'unknown error')}")

    return {
        "cycle":         "monthly",
        "total_elapsed": total_elapsed,
        "succeeded":     len(succeeded),
        "failed":        len(failed),
        "results":       results,
    }


# ── Weekly cycle ──────────────────────────────────────────────────────────────

def run_weekly_cycle(dry_run: bool = False) -> dict:
    """
    Run the weekly business overview report.
    Single run — no parallelism needed.
    """
    print(f"\n{'='*60}")
    print(f"WEEKLY CYCLE — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    if dry_run:
        print(f"[dry-run] Would run weekly business overview")
        return {"dry_run": True, "cycle": "weekly"}

    run_id     = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    try:
        run_agent(
            report_type = "weekly_supplier_overview",
            goal        = (
                "Weekly supplier performance overview. Identify top incident suppliers, "
                "problematic categories, return rate trends, and total resolution costs."
            ),
            audience    = "business",
            thread_id   = run_id,
        )
        elapsed = (datetime.now(timezone.utc) - started_at).seconds
        print(f"\n[scheduler] Weekly overview complete — {elapsed}s — run_id: {run_id}")
        return {
            "cycle":   "weekly",
            "run_id":  run_id,
            "status":  "success",
            "elapsed": elapsed,
        }

    except Exception as e:
        elapsed = (datetime.now(timezone.utc) - started_at).seconds
        print(f"\n[scheduler] Weekly overview FAILED after {elapsed}s: {e}")
        return {
            "cycle":   "weekly",
            "run_id":  run_id,
            "status":  "failed",
            "error":   str(e),
            "elapsed": elapsed,
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Supplier BI Agent Scheduler")
    parser.add_argument(
        "cycle",
        choices=["monthly", "weekly"],
        help="Which cycle to run"
    )
    parser.add_argument(
        "--suppliers",
        nargs="+",
        default=None,
        help="Supplier IDs to include (monthly only). Default: all suppliers."
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="Max parallel report runs for monthly cycle (default: 5)"
    )
    parser.add_argument(
        "--skip-intelligence",
        action="store_true",
        help="Skip Comment Intelligence step (monthly only)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run without executing"
    )

    args = parser.parse_args()

    if args.cycle == "monthly":
        run_monthly_cycle(
            supplier_filter    = args.suppliers,
            max_workers        = args.max_workers,
            skip_intelligence  = args.skip_intelligence,
            dry_run            = args.dry_run,
        )
    elif args.cycle == "weekly":
        run_weekly_cycle(dry_run=args.dry_run)
