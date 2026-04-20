# Supplier Performance BI Agent

An autonomous AI agent that generates, validates, and publishes supplier performance reports — with a human-in-the-loop review layer and a React control plane for account managers.

---

## Architecture

```
Orders / Incidents / Returns / Suppliers
              ↓
    BigQuery (supplier_bi dataset)
              ↓
    LangGraph Agent Pipeline
    discover → pull → analyse → generate → validate → review → publish
              ↓
    Control Plane (FastAPI + React)
    Queue · Dashboards · New Report · Ask · Observability
              ↓
    Cloud Run (europe-west2)
```

---

## Pipeline Nodes

| Node | What it does |
|---|---|
| **discover** | Selects which BigQuery tables are needed. Template mode for scheduled reports, LLM mode for ad-hoc. Always forces `orders` table for incident rate calculations. |
| **pull** | Generates and executes BigQuery SQL. Template SQL for scheduled reports, LLM-generated SQL for ad-hoc. Handles BOOL column constraints. |
| **analyse** | Pre-processes query results into structured analysis. Ad-hoc aware — handles free-form SQL with period comparisons. Sets confidence score and flags. |
| **generate** | Calls Claude Sonnet to write the full report narrative. Retry logic on 529 overload. |
| **validate** | Compares reported metrics against BigQuery ground truth. Scope-aware — extracts date range and supplier from actual SQL queries run, not hardcoded defaults. |
| **review** | Policy engine — auto-approves, routes to queue, or escalates based on confidence, hallucination flags, and pipeline errors. |
| **publish** | Writes approved reports to GCS and `approved_reports` BigQuery table. |

---

## Report Types

| Type | Audience | Cadence | Tables |
|---|---|---|---|
| `weekly_supplier_overview` | Business | Weekly | orders, incidents, returns, suppliers |
| `monthly_supplier_account` | Supplier | Monthly | orders, incidents, returns, suppliers |
| `adhoc_business` | Business | On demand | LLM-selected |
| `adhoc_supplier` | Supplier | On demand | LLM-selected |

---

## Control Plane

### Tabs

**Queue** — Pending reports awaiting human decision. Shows confidence, validation checks, hallucination flags. Click to open audit view with Report / Validation / Policy / Data tabs. Decisions: Approve / Edit & Approve / Reject. Option to share approved supplier reports with the supplier.

**Dashboards**
- *Business Overview* — 7 scorecards, incident & return rate trend, resolution cost % trend, top 10 suppliers bar, category incident rate bar, resolution mix pie. Cross-filtering: click any chart to filter all others.
- *Supplier Account* — Per-supplier drill-down with 2 scorecard rows (metrics + portfolio benchmarks), category charts, SKU incident & return tables, return reasons, incident type pie, resolution mix. Cross-filtering on category and incident type.

**New Report** — Plain English goal → agent pipeline runs in background → page updates automatically when done → Internal Only or Share with Supplier buttons.

**Ask** — Natural language → BigQuery SQL → answer table + SQL shown transparently. Auto-retry up to 3 times on SQL errors.

**Observability** — Full run history with confidence, decisions, reviewer, and run IDs.

### Supplier Portal

Route `/supplier/:id` shows a clean supplier-facing view with their dashboard data and any approved reports shared with them. No internal metrics or governance UI.

---

## Key Fixes — Phase 5

| Fix | What was wrong | What was fixed |
|---|---|---|
| Run ID mismatch | `graph.py` generated a new `run_id` separate from `thread_id`. Browser polled `thread_id` but BigQuery had `run_id`. | `run_id = thread_id` throughout. |
| No initial BigQuery status | Nothing written to `agent_runs` until pipeline finished. | Write `status = "running"` immediately at start. Write `status = "failed"` on exception. |
| Status endpoint streaming delay | `agent_runs` UPDATE not visible immediately after streaming insert. | Status endpoint checks `pending_reports` first. |
| Ad-hoc pre-processor reading 0 orders | Pre-processor assumed fixed column names. | Detect ad-hoc reports and pass raw data directly to LLM. |
| Validate node wrong scope | Ground truth used all-supplier 30-day data regardless of report scope. | Extract date range and supplier from actual SQL queries run. |
| supplierID case sensitivity | `sup002` got 0 rows. | Normalise to uppercase in `trigger_run`. |
| SUM on BOOL columns | LLM generated `SUM(hasIncident)` which BigQuery rejects. | Added rule to pull node prompt. |
| React polling stale run ID | Old `setInterval` kept firing from previous submissions. | Clear existing poll before starting new one. Use explicit terminal status list. |
| `orders` table omitted for ad-hoc | LLM sometimes didn't select `orders`. | Enforce `orders` in code after LLM selection. |
| Confidence showing 0% in UI | `agent_runs` confidence null due to BigQuery streaming buffer delay. | Fall back to `reportJSON.confidence` in `pending_reports`. |
| Cloud Run `ModuleNotFoundError: agent` | Import ran inside a thread at request time. | Move import to module level with `sys.path.insert(0, "/app")`. |
| Cloud Run requirements conflict | Pinned versions had conflicting sub-dependencies. | Remove version pins, let pip resolve compatible versions. |

---

## GCP Infrastructure

| Resource | Name |
|---|---|
| Project | `supplier-bi-agent-2025` |
| Region | `europe-west2` |
| BigQuery dataset | `supplier_bi` |
| Cloud Run service | `supplier-bi-control-plane` |
| GCS bucket (approved) | `supplier-bi-agent-2025-approved-reports` |
| GCS bucket (raw) | `supplier-bi-agent-2025-raw-reports` |
| Service account | `bi-agent@supplier-bi-agent-2025.iam.gserviceaccount.com` |

---

## BigQuery Tables

| Table | Purpose |
|---|---|
| `orders` | Order-level data — revenue, incident flags, return flags |
| `incidents` | Incident detail — type, resolution, cost, rating |
| `returns` | Return detail — reason, resolution, rating |
| `suppliers` | Supplier master — name, tier, region, category |
| `agent_runs` | Pipeline run log — status, confidence, queries, flags |
| `pending_reports` | Human review queue |
| `approved_reports` | Published reports — internal and supplier-facing |
| `validation_results` | Per-metric validation checks per run |
| `human_decisions` | Reviewer decisions with reason and timestamp |

---

## Local Development

```bash
# Start FastAPI backend
cd ~/projects/supplier-bi-agent
uvicorn control_plane.main:app --reload --port 8000

# Start React frontend
cd control_plane/frontend
npm run dev
# → http://localhost:5173
```

## Deploy to Cloud Run

```bash
cd ~/projects/supplier-bi-agent

gcloud run deploy supplier-bi-control-plane \
  --source . \
  --region europe-west2 \
  --project supplier-bi-agent-2025 \
  --platform managed \
  --no-allow-unauthenticated \
  --set-env-vars GCP_PROJECT=supplier-bi-agent-2025,BQ_DATASET=supplier_bi,ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY .env | cut -d '=' -f2) \
  --memory 1Gi \
  --timeout 300
```

## Access deployed service

```bash
gcloud run services proxy supplier-bi-control-plane \
  --region europe-west2 \
  --project supplier-bi-agent-2025 \
  --port 8081
# → http://localhost:8081
```

---

## Cost Estimate (monthly at PoC scale)

| Component | Cost |
|---|---|
| GCP infrastructure | ~$2–5 |
| Anthropic API (scheduled reports) | ~$3–5 |
| Anthropic API (ad-hoc + NL queries) | ~$3–5 |
| **Total** | **~$8–15/month** |

---

## Project Structure

```
supplier-bi-agent/
├── agent/
│   ├── graph.py              # LangGraph pipeline — run_id fix, initial BQ write
│   ├── nodes/
│   │   ├── discover.py       # Table selection — forces orders table
│   │   ├── pull.py           # SQL generation + execution
│   │   ├── analyse.py        # Ad-hoc aware pre-processor
│   │   ├── generate.py       # Report narrative generation
│   │   ├── validate.py       # Scope-aware ground truth validation
│   │   ├── review.py         # Policy engine
│   │   └── publish.py        # GCS + BigQuery publish
│   └── config/
│       ├── metadata.yaml     # Table schemas, SQL templates, allowed columns
│       └── policies.yaml     # Auto-approve rules per report type
├── control_plane/
│   ├── main.py               # FastAPI — all endpoints + static file serving
│   ├── requirements.txt      # Python dependencies (unpinned for compatibility)
│   ├── Dockerfile            # Multi-stage: Node build + Python serve
│   ├── .dockerignore
│   └── frontend/
│       └── src/
│           └── App.jsx       # Full React control plane UI
├── Dockerfile                # Symlink → control_plane/Dockerfile
├── .dockerignore             # Project-root ignore for Cloud Run builds
├── .gitignore
└── test_agent.py             # Integration tests (tests 4 and 5)
```
