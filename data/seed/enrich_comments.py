"""
Supplier BI Agent — Comment Enrichment Script (with checkpoints)
================================================================
Enriches incident and return rows with Claude-generated comments.

KEY FEATURES:
- Checkpoint file: saves progress after every batch — safe to stop anytime
- Resume: restart and it picks up exactly where it left off, even after reboot
- Real cost tracking: measures actual token usage per session
- Budget limit: stops automatically when spend limit is reached
- Test mode: runs 5 batches to measure real cost before committing

Usage:
    export ANTHROPIC_API_KEY=your-key

    # ALWAYS run test mode first to get real cost projection:
    python enrich_comments.py --project supplier-bi-agent-2025 --dataset supplier_bi --test

    # Run with a budget cap (stops when limit hit, resumes next session):
    python enrich_comments.py --project supplier-bi-agent-2025 --dataset supplier_bi --budget 5.00

    # Resume (same command — auto-detects checkpoint, no confirmation needed):
    python enrich_comments.py --project supplier-bi-agent-2025 --dataset supplier_bi --budget 5.00

    # Incidents only:
    python enrich_comments.py --project supplier-bi-agent-2025 --dataset supplier_bi --budget 5.00 --incidents-only

    # Returns only:
    python enrich_comments.py --project supplier-bi-agent-2025 --dataset supplier_bi --budget 5.00 --returns-only

    # Reset and start over:
    python enrich_comments.py --project supplier-bi-agent-2025 --dataset supplier_bi --reset

CHECKPOINT FILE:
    Saved to: ./enrich_checkpoint.json (in whichever directory you run from)
    Safe to delete if you want to restart from scratch.
    Survives laptop shutdown — just re-export your API key and rerun.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from google.cloud import bigquery
except ImportError:
    print("ERROR: google-cloud-bigquery not installed.")
    print("  Run: pip install google-cloud-bigquery")
    sys.exit(1)

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic not installed.")
    print("  Run: pip install anthropic")
    sys.exit(1)


# ── Configuration ─────────────────────────────────────────────────────────────

BATCH_SIZE       = 50
SLEEP_BETWEEN    = 1.5    # seconds between API calls
MAX_RETRIES      = 3
MODEL            = "claude-haiku-4-5-20251001"
CHECKPOINT_FILE  = Path("./enrich_checkpoint.json")
TEST_BATCHES     = 5      # batches to run in test mode

PRICE_INPUT_PER_M  = 0.80   # $ per million input tokens
PRICE_OUTPUT_PER_M = 4.00   # $ per million output tokens
WRITE_EVERY_N      = 10     # write to BigQuery every N batches


# ── SKU → product hint ────────────────────────────────────────────────────────

SKU_PRODUCT_HINTS = {
    "ELC": {
        range(1,  10): "smartphone or tablet",
        range(10, 20): "laptop or computer",
        range(20, 30): "headphones or earbuds",
        range(30, 38): "smart home device or speaker",
        range(38, 44): "camera or accessory",
    },
    "HMG": {
        range(1,  10): "chair or sofa",
        range(10, 20): "table or desk",
        range(20, 30): "garden tool or planter",
        range(30, 38): "lamp or lighting fixture",
        range(38, 44): "storage unit or shelf",
    },
    "CLT": {
        range(1,  10): "jacket or coat",
        range(10, 20): "trousers or jeans",
        range(20, 30): "shirt or top",
        range(30, 38): "shoes or boots",
        range(38, 44): "bag or accessories",
    },
    "SPT": {
        range(1,  10): "exercise equipment or weights",
        range(10, 20): "bicycle or cycling accessory",
        range(20, 30): "camping gear or tent",
        range(30, 38): "sports clothing or footwear",
        range(38, 44): "outdoor tool or equipment",
    },
    "TOY": {
        range(1,  10): "board game or puzzle",
        range(10, 20): "action figure or doll",
        range(20, 30): "construction set or building blocks",
        range(30, 38): "remote control vehicle",
        range(38, 44): "educational toy or game",
    },
    "BTY": {
        range(1,  10): "skincare product",
        range(10, 20): "haircare product or tool",
        range(20, 30): "makeup or cosmetics",
        range(30, 38): "health supplement or vitamin",
        range(38, 44): "personal care device",
    },
    "KIT": {
        range(1,  10): "cookware or pan",
        range(10, 20): "kitchen appliance",
        range(20, 30): "cutlery or utensils",
        range(30, 38): "storage container or organiser",
        range(38, 44): "dining accessory or tableware",
    },
}

def _sku_hint(sku, category):
    try:
        prefix = sku.split("-")[0]
        number = int(sku.split("-")[1])
        for r, hint in SKU_PRODUCT_HINTS.get(prefix, {}).items():
            if number in r:
                return hint
    except (IndexError, ValueError):
        pass
    return category


# ── Prompts ───────────────────────────────────────────────────────────────────

INCIDENT_SYSTEM = """You are generating realistic 1-2 sentence customer complaint comments for an e-commerce platform.
Each comment must be specific and concrete — mention the exact part damaged, what is missing, or what arrived wrong.
The comment must make physical sense for the specific product type mentioned.
Sound like a real frustrated customer. Never be vague or generic.

Good examples:
- 'The bottom left corner of the screen had a deep crack running through it, making the display unusable.'
- 'The rear left leg of the chair arrived snapped clean off at the joint.'
- 'Package was marked as delivered but nothing was at my door or with any neighbour.'
- 'The listing said the table was 120cm wide but the one I received measured 74cm.'
- 'I received a blue jacket instead of the black one shown in my order confirmation.'
- 'The charging cable was missing from the box despite being listed as included.'

Respond ONLY with a JSON array of strings. No other text."""

INCIDENT_USER = """Generate {n} realistic 1-2 sentence customer complaint comments.
Each comment must reference specific physical details that match the product type.

Incidents:
{items}

Rules per incident type:
- damage_defect: name the exact part and damage (cracked screen, broken hinge, torn seam, warped lid, bent frame)
- missing_parts: name the specific missing item (power cable, left armrest, mounting bracket, battery cover, assembly screws)
- lost_item: describe the delivery situation (marked delivered but absent, empty box, tracking frozen for X days, photo shows wrong address)
- misinformation: state the specific discrepancy (listed as 120cm but measured 74cm, described as leather but synthetic, incompatible despite listing)
- mis_shipped: describe exactly what was wrong (received red instead of blue, size M instead of XL, completely different product)

Return a JSON array with exactly {n} strings in the same order."""

RETURN_SYSTEM = """You are generating realistic 1-2 sentence customer return comments for an e-commerce platform.
Each comment must be specific — mention the exact quality issue, what did not meet expectations, or the concrete reason they no longer need it.
The comment must make physical sense for the specific product type mentioned.
Sound like a real customer. Never be vague.

Good examples:
- 'The fabric feels much thinner than the photos suggested and already has a small pull after one wash.'
- 'Ordered this as a birthday gift but my partner already bought the same model last week.'
- 'The colour is much darker in person than shown online and does not match my other furniture.'
- 'The non-stick coating on the pan started flaking after the second use.'

Respond ONLY with a JSON array of strings. No other text."""

RETURN_USER = """Generate {n} realistic 1-2 sentence customer return comments.
Each comment must reference specific details that match the product type.

Returns:
{items}

Rules per return reason:
- didnt_like: mention a specific aesthetic or sensory issue (colour in person, texture, weight, style, how it fits the space)
- doesnt_need: give a concrete situational reason (duplicate gift, already owned one, circumstances changed, project cancelled)
- not_up_to_expectations: name the specific failure (stitching quality, coating peeling, poor battery life, material too thin, uneven finish)

Return a JSON array with exactly {n} strings in the same order."""

REVIEW_SYSTEM = """You are generating realistic 1-2 sentence product reviews for an e-commerce platform.
Match the tone precisely to the star rating:
1-2 stars: angry or deeply disappointed, mention the specific problem
3 stars: mixed feelings, something worked but something did not
4-5 stars: satisfied, mention what was specifically good
Sound like a real person. Never generic.
Respond ONLY with a JSON array of strings. No other text."""

REVIEW_USER = """Generate {n} realistic 1-2 sentence product reviews.

Reviews:
{items}

Return a JSON array with exactly {n} strings in the same order."""


# ── Cost tracker ──────────────────────────────────────────────────────────────

class CostTracker:
    def __init__(self, budget=None, initial_spent=0.0):
        self.input_tokens  = 0
        self.output_tokens = 0
        self.total_cost    = initial_spent
        self.budget        = budget
        self.calls         = 0

    def add(self, usage):
        self.input_tokens  += usage.input_tokens
        self.output_tokens += usage.output_tokens
        cost = (
            (usage.input_tokens  / 1_000_000) * PRICE_INPUT_PER_M +
            (usage.output_tokens / 1_000_000) * PRICE_OUTPUT_PER_M
        )
        self.total_cost += cost
        self.calls      += 1
        return cost

    def over_budget(self):
        return self.budget is not None and self.total_cost >= self.budget

    def remaining_str(self):
        if self.budget is None:
            return ""
        return f" | remaining: ${max(0, self.budget - self.total_cost):.4f}"

    def summary(self):
        return (
            f"${self.total_cost:.4f} total "
            f"({self.input_tokens:,} input + {self.output_tokens:,} output tokens, "
            f"{self.calls} API calls)"
        )


# ── Claude caller ─────────────────────────────────────────────────────────────

def call_claude(client, tracker, system, user, n, retries=MAX_RETRIES):
    for attempt in range(retries):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}]
            )
            tracker.add(msg.usage)
            raw = msg.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw.strip())
            if not isinstance(result, list):
                raise ValueError("Not a list")
            if len(result) < n:
                result.extend([result[-1]] * (n - len(result)))
            return result[:n]
        except Exception as e:
            print(f"    Error (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return ["Customer reported an issue with this product."] * n


# ── Description builders ──────────────────────────────────────────────────────

def incident_descs(batch):
    return "\n".join(
        f"- Type: {r['incidentType'].replace('_',' ')} | "
        f"Product: {_sku_hint(r['productSKU'], r['productCategory'])} | "
        f"Category: {r['productCategory']} | "
        f"Days after purchase: {r['daysBetweenPurchaseAndIncident']} | "
        f"Resolution: {r['incidentResolution'].replace('_',' ')}"
        for r in batch
    )

def incident_review_descs(batch):
    return "\n".join(
        f"- Rating: {r['productRating']}/5 | "
        f"Product: {_sku_hint(r['productSKU'], r['productCategory'])} | "
        f"Incident: {r['incidentType'].replace('_',' ')}"
        for r in batch
    )

def return_descs(batch):
    return "\n".join(
        f"- Reason: {r['buyersRemorseReason'].replace('_',' ')} | "
        f"Product: {_sku_hint(r['productSKU'], r['productCategory'])} | "
        f"Category: {r['productCategory']} | "
        f"Days after purchase: {r['daysBetweenPurchaseAndReturn']}"
        for r in batch
    )

def return_review_descs(batch):
    return "\n".join(
        f"- Rating: {r['productRating']}/5 | "
        f"Product: {_sku_hint(r['productSKU'], r['productCategory'])} | "
        f"Reason: {r['buyersRemorseReason'].replace('_',' ')}"
        for r in batch
    )


# ── BigQuery writers ──────────────────────────────────────────────────────────

def write_incidents(bq, project, dataset, rows):
    if not rows:
        return
    tmp = f"{project}.{dataset}.tmp_inc_comments"
    schema = [
        bigquery.SchemaField("incidentID", "STRING"),
        bigquery.SchemaField("comment",    "STRING"),
        bigquery.SchemaField("review",     "STRING"),
    ]
    bq.load_table_from_json(
        rows, tmp,
        job_config=bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    ).result()
    bq.query(f"""
        MERGE `{project}.{dataset}.incidents` T
        USING `{tmp}` S ON T.incidentID = S.incidentID
        WHEN MATCHED THEN UPDATE SET
            T.incidentCustomerComment = S.comment,
            T.customerReview = S.review
    """).result()
    bq.delete_table(tmp, not_found_ok=True)

def write_returns(bq, project, dataset, rows):
    if not rows:
        return
    tmp = f"{project}.{dataset}.tmp_ret_comments"
    schema = [
        bigquery.SchemaField("returnID", "STRING"),
        bigquery.SchemaField("comment",  "STRING"),
        bigquery.SchemaField("review",   "STRING"),
    ]
    bq.load_table_from_json(
        rows, tmp,
        job_config=bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    ).result()
    bq.query(f"""
        MERGE `{project}.{dataset}.returns` T
        USING `{tmp}` S ON T.returnID = S.returnID
        WHEN MATCHED THEN UPDATE SET
            T.buyersRemorseComment = S.comment,
            T.customerReview = S.review
    """).result()
    bq.delete_table(tmp, not_found_ok=True)


# ── Checkpoint ────────────────────────────────────────────────────────────────

def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        try:
            cp = json.loads(CHECKPOINT_FILE.read_text())
            print(f"  Checkpoint found:")
            print(f"    Incidents done: {cp.get('incidents_done',0):,}  complete: {cp.get('incidents_complete',False)}")
            print(f"    Returns done:   {cp.get('returns_done',0):,}  complete: {cp.get('returns_complete',False)}")
            print(f"    Total spent so far: ${cp.get('total_spent',0):.4f}")
            return cp
        except Exception as e:
            print(f"  Warning: could not read checkpoint ({e}) — starting fresh")
    return {
        "incidents_done": 0, "returns_done": 0,
        "total_spent": 0.0,
        "incidents_complete": False, "returns_complete": False,
    }

def save_checkpoint(cp):
    cp["last_updated"] = datetime.now().isoformat()
    CHECKPOINT_FILE.write_text(json.dumps(cp, indent=2))

def reset_checkpoint():
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        print("Checkpoint deleted — will start from scratch next run.")
    else:
        print("No checkpoint found — already at start.")


# ── Test mode ─────────────────────────────────────────────────────────────────

def run_test(bq, claude, project, dataset):
    print(f"\nTEST MODE — running {TEST_BATCHES} batches to measure real cost")
    print("─" * 60)

    rows = list(bq.query(f"""
        SELECT i.incidentID, i.incidentType, i.productCategory, i.productSKU,
               i.productRating, i.daysBetweenPurchaseAndIncident, i.incidentResolution,
               s.supplierRegion
        FROM `{project}.{dataset}.incidents` i
        LEFT JOIN `{project}.{dataset}.suppliers` s ON i.supplierID = s.supplierID
        ORDER BY i.incidentID
        LIMIT {TEST_BATCHES * BATCH_SIZE}
    """).result())

    total_inc = list(bq.query(
        f"SELECT COUNT(*) n FROM `{project}.{dataset}.incidents`"
    ).result())[0]["n"]
    total_ret = list(bq.query(
        f"SELECT COUNT(*) n FROM `{project}.{dataset}.returns`"
    ).result())[0]["n"]

    tracker = CostTracker()
    t0 = time.time()

    for i in range(TEST_BATCHES):
        batch = rows[i * BATCH_SIZE:(i + 1) * BATCH_SIZE]
        if not batch:
            break
        n = len(batch)
        print(f"  Batch {i+1}/{TEST_BATCHES} ({n} rows)...")
        call_claude(claude, tracker, INCIDENT_SYSTEM,
                    INCIDENT_USER.format(n=n, items=incident_descs(batch)), n)
        time.sleep(SLEEP_BETWEEN)
        call_claude(claude, tracker, REVIEW_SYSTEM,
                    REVIEW_USER.format(n=n, items=incident_review_descs(batch)), n)
        time.sleep(SLEEP_BETWEEN)

    elapsed      = time.time() - t0
    rows_tested  = TEST_BATCHES * BATCH_SIZE
    cost_per_row = tracker.total_cost / rows_tested
    secs_per_row = elapsed / rows_tested
    total_rows   = total_inc + total_ret
    proj_cost    = cost_per_row * total_rows
    proj_hours   = (secs_per_row * total_rows) / 3600

    print(f"\n{'─' * 60}")
    print("REAL MEASUREMENTS")
    print(f"{'─' * 60}")
    print(f"  Rows tested:           {rows_tested}")
    print(f"  Actual cost:           ${tracker.total_cost:.4f}")
    print(f"  Cost per row:          ${cost_per_row:.5f}")
    print(f"  Seconds per row:       {secs_per_row:.2f}s")
    print(f"  Avg input tokens/call: {tracker.input_tokens // max(tracker.calls,1):,}")
    print(f"  Avg output tokens/call:{tracker.output_tokens // max(tracker.calls,1):,}")
    print(f"\n{'─' * 60}")
    print("FULL RUN PROJECTION")
    print(f"{'─' * 60}")
    print(f"  Total rows:            {total_rows:,}  ({total_inc:,} incidents + {total_ret:,} returns)")
    print(f"  Projected total cost:  ${proj_cost:.2f}")
    print(f"  Projected total time:  {proj_hours:.1f} hours")
    print(f"  Sessions at $5 each:   ~{proj_cost/5:.1f} sessions")
    print(f"{'─' * 60}")
    print("\nNext step:")
    print("  python enrich_comments.py --project ... --dataset ... --budget 5.00")
    print("  Run again with the same command to resume after each session.")


# ── Main enrichment ───────────────────────────────────────────────────────────

def run_enrichment(bq, claude, project, dataset, budget, do_inc, do_ret, cp):
    tracker = CostTracker(budget=budget, initial_spent=cp["total_spent"])

    # ── Incidents ─────────────────────────────────────────────────────────────
    if do_inc and not cp["incidents_complete"]:
        print("\nFetching incidents...")
        all_rows = list(bq.query(f"""
            SELECT i.incidentID, i.incidentType, i.productCategory, i.productSKU,
                   i.productRating, i.daysBetweenPurchaseAndIncident, i.incidentResolution,
                   s.supplierRegion
            FROM `{project}.{dataset}.incidents` i
            LEFT JOIN `{project}.{dataset}.suppliers` s ON i.supplierID = s.supplierID
            ORDER BY i.incidentID
        """).result())
        total    = len(all_rows)
        start_at = cp["incidents_done"]
        print(f"  {total:,} incidents | resuming from row {start_at:,}")

        pending = []
        batch_count = 0

        for batch_start in range(start_at, total, BATCH_SIZE):
            if tracker.over_budget():
                print(f"\n  Budget ${budget:.2f} reached — stopping.")
                break

            batch = all_rows[batch_start:batch_start + BATCH_SIZE]
            n     = len(batch)
            pct   = batch_start / total * 100

            print(f"  [{pct:5.1f}%] incidents {batch_start+1:,}–{batch_start+n:,}/{total:,}"
                  f" | spent: ${tracker.total_cost:.4f}{tracker.remaining_str()}")

            comments = call_claude(claude, tracker, INCIDENT_SYSTEM,
                                   INCIDENT_USER.format(n=n, items=incident_descs(batch)), n)
            time.sleep(SLEEP_BETWEEN)

            reviews = call_claude(claude, tracker, REVIEW_SYSTEM,
                                  REVIEW_USER.format(n=n, items=incident_review_descs(batch)), n)
            time.sleep(SLEEP_BETWEEN)

            for i, row in enumerate(batch):
                pending.append({"incidentID": row["incidentID"],
                                "comment": comments[i], "review": reviews[i]})

            batch_count += 1
            cp["incidents_done"] = batch_start + n
            cp["total_spent"]    = tracker.total_cost
            save_checkpoint(cp)

            # Flush to BigQuery every WRITE_EVERY_N batches
            if batch_count % WRITE_EVERY_N == 0 or tracker.over_budget():
                print(f"    → Writing {len(pending):,} rows to BigQuery...")
                write_incidents(bq, project, dataset, pending)
                pending = []

        if pending:
            print(f"    → Writing final {len(pending):,} rows to BigQuery...")
            write_incidents(bq, project, dataset, pending)

        if not tracker.over_budget():
            cp["incidents_complete"] = True
            save_checkpoint(cp)
            print("  ✓ All incidents complete")

    # ── Returns ───────────────────────────────────────────────────────────────
    if do_ret and not cp["returns_complete"] and not tracker.over_budget():
        print("\nFetching returns...")
        all_rows = list(bq.query(f"""
            SELECT r.returnID, r.buyersRemorseReason, r.productCategory, r.productSKU,
                   r.productRating, r.daysBetweenPurchaseAndReturn, s.supplierRegion
            FROM `{project}.{dataset}.returns` r
            LEFT JOIN `{project}.{dataset}.suppliers` s ON r.supplierID = s.supplierID
            ORDER BY r.returnID
        """).result())
        total    = len(all_rows)
        start_at = cp["returns_done"]
        print(f"  {total:,} returns | resuming from row {start_at:,}")

        pending = []
        batch_count = 0

        for batch_start in range(start_at, total, BATCH_SIZE):
            if tracker.over_budget():
                print(f"\n  Budget ${budget:.2f} reached — stopping.")
                break

            batch = all_rows[batch_start:batch_start + BATCH_SIZE]
            n     = len(batch)
            pct   = batch_start / total * 100

            print(f"  [{pct:5.1f}%] returns {batch_start+1:,}–{batch_start+n:,}/{total:,}"
                  f" | spent: ${tracker.total_cost:.4f}{tracker.remaining_str()}")

            comments = call_claude(claude, tracker, RETURN_SYSTEM,
                                   RETURN_USER.format(n=n, items=return_descs(batch)), n)
            time.sleep(SLEEP_BETWEEN)

            reviews = call_claude(claude, tracker, REVIEW_SYSTEM,
                                  REVIEW_USER.format(n=n, items=return_review_descs(batch)), n)
            time.sleep(SLEEP_BETWEEN)

            for i, row in enumerate(batch):
                pending.append({"returnID": row["returnID"],
                                "comment": comments[i], "review": reviews[i]})

            batch_count += 1
            cp["returns_done"]  = batch_start + n
            cp["total_spent"]   = tracker.total_cost
            save_checkpoint(cp)

            if batch_count % WRITE_EVERY_N == 0 or tracker.over_budget():
                print(f"    → Writing {len(pending):,} rows to BigQuery...")
                write_returns(bq, project, dataset, pending)
                pending = []

        if pending:
            print(f"    → Writing final {len(pending):,} rows to BigQuery...")
            write_returns(bq, project, dataset, pending)

        if not tracker.over_budget():
            cp["returns_complete"] = True
            save_checkpoint(cp)
            print("  ✓ All returns complete")

    # ── Session summary ───────────────────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print("SESSION SUMMARY")
    print(f"{'═' * 60}")
    print(f"  {tracker.summary()}")
    print(f"  Incidents: {cp['incidents_done']:,} rows done  {'✓ complete' if cp['incidents_complete'] else '(in progress)'}")
    print(f"  Returns:   {cp['returns_done']:,} rows done  {'✓ complete' if cp['returns_complete'] else '(in progress)'}")

    if cp["incidents_complete"] and cp["returns_complete"]:
        print("\n  All enrichment complete! Checkpoint deleted.")
        CHECKPOINT_FILE.unlink(missing_ok=True)
    else:
        print(f"\n  Checkpoint saved to: {CHECKPOINT_FILE.resolve()}")
        print("  Run the same command again to continue — works after reboot too.")
        print("  (Just re-export ANTHROPIC_API_KEY before running)")
    print(f"{'═' * 60}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Supplier BI Agent — Comment Enrichment with checkpoints"
    )
    parser.add_argument("--project",        required=True)
    parser.add_argument("--dataset",        default="supplier_bi")
    parser.add_argument("--anthropic-key",  default=None)
    parser.add_argument("--budget",         type=float, default=None,
                        help="Max spend per session in USD e.g. --budget 5.00")
    parser.add_argument("--test",           action="store_true",
                        help="Measure real cost with 5 test batches then exit")
    parser.add_argument("--incidents-only", action="store_true")
    parser.add_argument("--returns-only",   action="store_true")
    parser.add_argument("--reset",          action="store_true",
                        help="Delete checkpoint and start over")
    args = parser.parse_args()

    api_key = args.anthropic_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: Anthropic API key required.")
        print("  export ANTHROPIC_API_KEY=your-key")
        sys.exit(1)

    print("=" * 60)
    print("Supplier BI Agent — Comment Enrichment")
    print("=" * 60)
    print(f"  Project: {args.project}  |  Dataset: {args.dataset}")
    print(f"  Model:   {MODEL}  |  Batch size: {BATCH_SIZE}")
    if args.budget:
        print(f"  Budget:  ${args.budget:.2f} per session")

    bq     = bigquery.Client(project=args.project)
    claude = anthropic.Anthropic(api_key=api_key)

    if args.reset:
        reset_checkpoint()
        return

    if args.test:
        run_test(bq, claude, args.project, args.dataset)
        return

    print("\nChecking checkpoint...")
    cp = load_checkpoint()

    do_inc = not args.returns_only
    do_ret = not args.incidents_only

    if do_inc and cp["incidents_complete"]:
        print("  Incidents already complete — skipping")
        do_inc = False
    if do_ret and cp["returns_complete"]:
        print("  Returns already complete — skipping")
        do_ret = False

    if not do_inc and not do_ret:
        print("\nAll enrichment already complete!")
        return

    # Only ask for confirmation on a fresh start
    if cp["incidents_done"] == 0 and cp["returns_done"] == 0:
        budget_note = f"Will stop at ${args.budget:.2f} and save checkpoint." if args.budget else "No budget limit set."
        print(f"\nStarting fresh enrichment run. {budget_note}")
        if not args.budget:
            print("TIP: Use --budget 5.00 to run in sessions and stop automatically.")
        confirm = input("Continue? (yes/no): ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            return
    else:
        print("  Resuming from checkpoint...")

    run_enrichment(bq, claude, args.project, args.dataset,
                   budget=args.budget,
                   do_inc=do_inc, do_ret=do_ret, cp=cp)


if __name__ == "__main__":
    main()