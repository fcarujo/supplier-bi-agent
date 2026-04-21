# Supplier Performance BI Agent

An autonomous multi-agent BI system that generates, validates, and publishes supplier performance reports — with a human-in-the-loop review layer, a React control plane for account managers, and a Comment Intelligence layer that analyses customer feedback to surface actionable product and operations improvements.

---

## What it does

The system serves two distinct audiences from the same data pipeline:

**Business overview** — portfolio-level analysis across all suppliers. Incident rates, return rates, resolution costs, category and supplier rankings, trend detection. Runs weekly on a schedule.

**Supplier account management** — one report per supplier, including a Customer Voice section that exhaustively analyses customer comments to identify exactly what is failing at the product and operations level, and what the supplier needs to fix. Runs monthly on a schedule, shareable directly with the supplier.

Both report types flow through the same agent pipeline. The audience determines what data is exposed, how it is scoped, and what the generated narrative focuses on.

---

## Architecture

```
Orders / Incidents / Returns / Suppliers
              ↓
    BigQuery (supplier_bi dataset)
              ↓
    ┌─────────────────────────────────────┐
    │  Agent Pipeline (LangGraph)         │
    │  discover → pull → analyse →        │
    │  generate → validate → review →     │
    │  publish                            │
    └─────────────────────────────────────┘
              ↓
    ┌─────────────────────────────────────┐
    │  Comment Intelligence Agent         │
    │  filter → analyse → store           │
    │  (runs monthly, feeds generate)     │
    └─────────────────────────────────────┘
              ↓
    Control Plane (FastAPI + React)
    Queue · Dashboards · New Report · Ask · Observability
              ↓
    Cloud Run (europe-west2)
```

---

## Agent Pipeline

### Core nodes

| Node | What it does |
|---|---|
| **discover** | Selects which BigQuery tables are needed. Template mode for scheduled reports, LLM mode for ad-hoc. Always enforces `orders` table in code for incident rate calculations. |
| **pull** | Generates and executes BigQuery SQL. Template SQL for scheduled, LLM-generated for ad-hoc. |
| **analyse** | Pre-processes query results into structured analysis. Sets confidence score and flags. |
| **generate** | Writes the full report narrative. For supplier reports, loads comment intelligence and injects a Customer Voice section. |
| **validate** | Re-queries BigQuery independently to verify reported metrics against ground truth. Scope-aware date and supplier filtering. Flags deviations above 10% as potential hallucinations. |
| **review** | Deterministic policy engine — auto-approves, routes to queue, or escalates based on confidence, hallucination flags, and validation results. No LLM involved. |
| **publish** | Writes approved reports to GCS and the `approved_reports` BigQuery table. |

### Comment Intelligence Agent

A standalone monthly agent that analyses customer feedback for problem SKUs.

**Filter** — For each supplier, identifies the top 5 SKUs where incident or return rate exceeds the category average by more than 1 percentage point. Requires a minimum of 20 orders in the 90-day analysis window to ensure statistical reliability. SKUs below this threshold are excluded.

**Analyse** — For each flagged SKU, pulls every incident comment and return comment from the last 90 days. Calls Claude for exhaustive structured analysis:
- Incident themes — recurring patterns with frequency, severity, and customer evidence
- Return themes — recurring return reasons with frequency and evidence
- Root causes — underlying failures categorised as packaging / product_quality / listing_accuracy / fulfilment
- Improvements — specific prioritised actions with expected impact and effort rating

**Store** — Writes structured intelligence to the `sku_comment_intelligence` BigQuery table.

**Integration** — The generate node loads this month's intelligence for the supplier and injects it into the report. Supplier account reports include a **Customer Voice** section with per-SKU analysis grounded entirely in what customers actually said.

---

## SQL Strategy

**Scheduled reports** use pre-defined SQL templates stored in `metadata.yaml`. Zero LLM involvement in query generation — consistent, version-controlled, deterministic results on every run.

**Ad-hoc reports** use LLM-generated SQL at runtime since the goal is open-ended. Always requires human review before publishing.

---

## Security Architecture

Three independent layers — an attacker must bypass all three simultaneously.

**Layer 1 — Input sanitiser:** Every goal string is scanned for prompt injection patterns before reaching the LLM. Detections are flagged to the audit trail.

**Layer 2 — Column allowlist and SQL validator:** Every SQL query is validated before execution. No dangerous operations, no SELECT *, only allowed columns. Supplier-scoped reports have `WHERE supplierID = 'SUPXXX'` injected automatically. `netRevenue` and `customerID` are permanently blocked columns regardless of report type.

**Layer 3 — IAM:** The service account has read-only access to the allowed dataset only. Even a malicious query that bypasses layers 1 and 2 is rejected at the infrastructure level.

---

## Control Plane

**Queue** — Pending reports awaiting human decision. Shows confidence meter, validation pass/fail counts, hallucination flags. Audit view with Report / Validation / Policy / Data tabs. Decisions: Approve / Edit & Approve / Reject. Option to share approved supplier reports with the supplier.

**Dashboards**
- *Business Overview* — 7 scorecards, incident & return rate trend, resolution cost % trend, top 10 suppliers, category breakdown, resolution mix. Cross-filtering on all charts.
- *Supplier Account* — Metrics + portfolio benchmark scorecards, category charts, SKU incident & return tables, return reasons, incident type breakdown, resolution mix. Cross-filtering on category and incident type.

**New Report** — Plain English goal → pipeline runs in background → animated 7-step progress indicator → results shown automatically on completion → Internal Only or Share with Supplier.

**Ask** — Natural language → BigQuery SQL → answer table with SQL shown transparently. Auto-corrects SQL errors up to 3 times.

**Observability** — Full run history with confidence scores, decisions, and reviewer names.

**Supplier Portal** — Route `/supplier/:id` shows a clean supplier-facing view with their dashboard data and any reports shared with them. No internal metrics, no governance UI.

---

## Data Layer

Synthetic supplier performance dataset — 500k+ orders across 20 suppliers, 7 product categories, 301 SKUs, 12 months of history. Appended daily.

Intentional signal patterns designed for the agents to detect:
- One supplier has a sharp incident spike starting 3 months ago — early warning signal
- Supplier-direct fulfilment has 4× the lost_item rate versus warehouse fulfilment
- Budget price tier has 2× the return rate of premium
- 5 preferred suppliers account for ~78% of order volume — absolute count vs rate distinction is critical
- Customer comments enriched with product-specific context so comment intelligence has genuine signal to work with

---

## BigQuery Tables

| Table | Purpose |
|---|---|
| `orders` | Order-level data — revenue, incident flags, return flags |
| `incidents` | Incident detail — type, resolution, cost, rating, customer comments |
| `returns` | Return detail — reason, resolution, rating, customer comments |
| `suppliers` | Supplier master — name, tier, region, category |
| `agent_runs` | Pipeline run log — status, confidence, queries, flags |
| `pending_reports` | Human review queue |
| `approved_reports` | Published reports — internal and supplier-facing |
| `validation_results` | Per-metric validation checks per run |
| `human_decisions` | Reviewer decisions with reason and timestamp |
| `sku_comment_intelligence` | Monthly comment analysis — themes, root causes, improvements per flagged SKU |

---

## Project Structure

```
supplier-bi-agent/
├── agent/
│   ├── graph.py                  # LangGraph pipeline — AgentState, graph wiring
│   ├── comment_intelligence.py   # Comment Intelligence Agent — monthly batch
│   ├── nodes/
│   │   ├── discover.py           # Table selection — forces orders table
│   │   ├── pull.py               # SQL generation + execution
│   │   ├── analyse.py            # Ad-hoc aware pre-processor, confidence scoring
│   │   ├── generate.py           # Report narrative + Customer Voice section
│   │   ├── validate.py           # Scope-aware ground truth validation
│   │   ├── review.py             # Policy engine — auto-approve / queue / escalate
│   │   └── publish.py            # GCS + BigQuery publish
│   └── config/
│       ├── metadata.yaml         # Table schemas, SQL templates, allowed columns
│       └── policies.yaml         # Auto-approve rules per report type
├── control_plane/
│   ├── main.py                   # FastAPI — all endpoints, parallel queries
│   ├── requirements.txt          # Python dependencies
│   ├── Dockerfile                # Multi-stage: Node build + Python serve
│   └── frontend/
│       └── src/
│           └── App.jsx           # React control plane — all tabs + pipeline progress
├── Dockerfile                    # Project-root entry for Cloud Run builds
├── .gitignore
└── test_agent.py                 # Integration tests
```

---

## Build Status

| Phase | Status | Description |
|---|---|---|
| 1 — Data layer | ✅ Complete | BigQuery schema, synthetic data, daily append |
| 2 — Agent foundation | ✅ Complete | Discover + Pull nodes, SQL templates, security layer |
| 3 — Intelligence | ✅ Complete | Analyse + Generate nodes, dual-audience reports |
| 4 — Semantic control plane | ✅ Complete | Validation, policy engine, React audit UI |
| 5 — Dashboards & deployment | ✅ Complete | React dashboards, ad-hoc, NL BI, Cloud Run |
| 6 — Multi-agent | 🔄 In progress | Comment Intelligence Agent complete · Parallel Scheduler, Conversational Query Agent, Insight Agent pending |
| 7 — Supplier portal | ⬜ Planned | Firebase Auth, row-level security, supplier self-serve |
