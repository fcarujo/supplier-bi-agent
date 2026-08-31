# Supplier BI Agent — Fix Pack

Fixes the inter-agent context handoff (so the Customer Voice section stops
silently coming back empty), adds a hallucination grounding guard between
`generate` and `validate`, and hardens LLM response parsing across the agents.
Works for both scheduled and ad-hoc reports.

## What's in this pack

```
agent/common/__init__.py      # new shared package
agent/common/handoff.py       # RunContext + month helper + robust LLM parsing
apply_fixes.py                # idempotent in-place patcher (run from repo root)
test_fixes.py                 # offline verification (no BigQuery / no API key)
MANUAL_EDITS.md               # 2 small edits to apply by hand
```

## Which bug each piece fixes

| # | Bug | Fixed by |
|---|---|---|
| 1 | CI write/read use independent `date.today()` → empty Customer Voice | `handoff.current_analysis_month` threaded scheduler → CI → generate |
| 2 | `validate` ignores Customer Voice content | `validate_grounding` guard (fix 6 + MANUAL_EDITS B) |
| 3 | Low CI confidence can still auto-publish | `min_ci_confidence` in state (MANUAL_EDITS B, optional clamp) |
| 7 | `content[0].text` crashes on non-text/empty blocks | `extract_text` / `parse_json_response` (fix 5) |
| 8 | One bad SKU kills whole supplier CI run | per-SKU try/except (fix 1d) |
| 10 | `.seconds` truncates >24h runtimes | `.total_seconds()` (fix 2c) |

(Bugs 4, 5, 9, 11, 12 from the review are noted in the review but are larger
design changes — RLS, parameterised SQL, paging CI input — and are intentionally
left out of this mechanical pack so it stays low-risk. Say the word and I'll
write those next.)

---

## How to apply (VS Code, local machine)

1. **Copy the new files into your repo.** From the repo root
   (`supplier-bi-agent/`), copy this pack so that `agent/common/` and the three
   loose files land at the top level:

   ```
   supplier-bi-agent/
   ├── agent/
   │   └── common/
   │       ├── __init__.py
   │       └── handoff.py
   ├── apply_fixes.py
   ├── test_fixes.py
   └── MANUAL_EDITS.md
   ```

2. **Commit a clean checkpoint first** (so the .bak files + git both protect you):

   ```bash
   git add -A && git commit -m "checkpoint before fix pack"
   ```

3. **Dry-run the patcher** to see what it will change:

   ```bash
   python apply_fixes.py --dry-run
   ```

   Read the output. Lines starting with `✓` will be applied, `·` are already
   applied (skipped), and `!` mean an anchor wasn't found and needs a manual look.

4. **Apply it:**

   ```bash
   python apply_fixes.py
   ```

   Each modified file gets a `.bak` sibling the first time it's touched.

5. **Apply the two hand edits** in `MANUAL_EDITS.md` (graph initial state +
   generate return dict). They take about two minutes.

6. **Verify offline** (no API key, no BigQuery needed):

   ```bash
   python test_fixes.py
   ```

   All five checks should pass (the grounding-guard test only runs after
   `apply_fixes.py` has added `validate_grounding`).

7. **Run the existing suite** (needs your `.env` / key for tests 3–5):

   ```bash
   source .venv/bin/activate
   export ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY .env | cut -d '=' -f2)
   python test_agent.py 1   # template pull, no LLM
   python test_agent.py 5   # full supplier pipeline — exercises CI handoff + grounding
   ```

8. **Smoke-test the monthly handoff end to end** for one supplier:

   ```bash
   python agent/scheduler.py monthly --suppliers SUP001 --max-workers 1
   ```

   The Customer Voice section should now be populated, and a report citing a
   SKU not in the CI data will show an `ungrounded_sku` hallucination flag in
   `validation_results`.

### Rolling back

```bash
# per file
mv agent/comment_intelligence.py.bak agent/comment_intelligence.py
# or everything via git
git checkout -- .
```

---

## Doc correction (no code risk)

`README.md` says SQL auto-corrects "up to 2 attempts" but `pull.py` uses
`range(3)`. Pick one and make them agree. If 3 attempts is intended, change the
README line; if 2 is intended, change `range(3)` to `range(2)` in `pull.py`.
