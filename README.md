# Supplier Performance BI Agent

An autonomous multi-agent BI system that generates, validates, and publishes supplier performance reports — with a human-in-the-loop review layer, a React control plane for account managers, and a Comment Intelligence layer that analyses customer feedback to surface actionable product and operations improvements.

Live at **[agentic-intel.de](https://agentic-intel.de)**

---

## What it does

The system serves two distinct audiences from the same data pipeline:

**Business overview** — portfolio-level analysis across all suppliers. Incident rates, return rates, resolution costs, category and supplier rankings, trend detection. Runs weekly on a schedule. Includes a weekly insights banner at the top of the dashboard with anomaly detection, severity-rated alerts, and a Claude-generated digest paragraph to drive the weekly business review agenda.

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
    ┌─────────────────────────────────────┐
    │  Parallel Scheduler Agent           │
    │  comment intelligence →             │
    │  parallel supplier reports          │
    │  (runs monthly + weekly)            │
    └─────────────────────────────────────┘
              ↓
    ┌─────────────────────────────────────┐
    │  Insight Agent                      │
    │  detect → digest → store → cleanup  │
    │  (runs weekly)                      │
    └─────────────────────────────────────┘
              ↓
    Control Plane (FastAPI + React)
    Queue · Dashboards · New Report · Ask · Control Plane
              ↓
    Cloud Run (europe-west2) · agentic-intel.de
```

---

## Agent Pipeline

### Core nodes

| Node | What it does |
|---|---|
| **discover** | Selects which BigQuery tables are needed. Template mode for scheduled reports, LLM mode for ad-hoc. Always enforces `orders` table in code for incident rate calculations. |
| **pull** | Generates and executes BigQuery SQL. Template SQL for scheduled, LLM-generated for ad-hoc. Auto-corrects SQL errors up to 2 attempts by feeding the exact BigQuery error back to the LLM. |
| **analyse** | Pre-processes query results into structured analysis. Sets confidence score and flags. |
| **generate** | Writes the full report narrative. For supplier reports, loads comment intelligence and injects a Customer Voice section. |
| **validate** | Re-queries BigQuery independently to verify reported metrics against ground truth. Scope-aware date and supplier filtering. Flags deviations above 10% as potential hallucinations. |
| **review** | Deterministic policy engine — auto-approves, routes to queue, or escalates based on confidence, hallucination flags, and validation results. No LLM involved. |
| **publish** | Writes approved reports to GCS and the `approved_reports` BigQuery table. |

### Rejection Feedback Loop

When a reviewer rejects a report with a written reason, the agent pipeline re-runs automatically with that reason injected into the SQL generation prompt. The correction is applied without manual intervention. Max 2 re-run attempts enforced. The queue shows a re-run button with the original rejection reason pre-filled.

### Comment Intelligence Agent

A standalone monthly agent that analyses customer feedback for problem SKUs.

**Filter** — For each supplier, identifies the top 5 SKUs where incident or return rate exceeds the category average by more than 1 percentage point. Requires a minimum of 20 orders in the 90-day analysis window.

**Analyse** — For each flagged SKU, pulls every incident comment and return comment from the last 90 days. Calls Claude for exhaustive structured analysis: incident themes, return themes, root causes (packaging / product_quality / listing_accuracy / fulfilment), and prioritised improvement actions.

**Store** — Writes structured intelligence to the `sku_comment_intelligence` BigQuery table.

**Integration** — The generate node loads this month's intelligence for the supplier and injects it into the report. Supplier account reports include a **Customer Voice** section grounded entirely in what customers actually said.

### Parallel Scheduler Agent

Orchestrates the full monthly and weekly reporting cycle.

**Monthly cycle:**
1. Comment Intelligence for all suppliers (sequential — data prep must complete first)
2. Monthly supplier account reports for all 20 suppliers in parallel (ThreadPoolExecutor, default 5 workers)

**Weekly cycle:** Single weekly business overview run.

Each supplier runs in full isolation — one failure never affects others. Reduces monthly cycle from ~70 minutes sequential to ~3-4 minutes parallel.

```bash
python agent/scheduler.py monthly [--suppliers SUP001 SUP002] [--max-workers 5] [--skip-intelligence] [--dry-run]
python agent/scheduler.py weekly [--dry-run]
```

### Conversational Query Agent

Upgrades the Ask tab from a stateless one-shot NL→SQL tool to a full multi-turn conversation. Each session maintains message history and previous query results as context, allowing follow-up questions like "now break that down by category" without repeating the supplier or time period.

Sessions are in-memory, ephemeral by design. Max 100 sessions, last 10 exchanges per session, clears on instance restart.

### Insight Agent

Runs weekly. Detects anomalies and trends across the portfolio without being asked.

**Signals monitored:**
- Supplier incident rate spike vs 4-week baseline (>20% increase, min 25 orders)
- Supplier return rate spike vs 4-week baseline (>20% increase)
- Supplier resolution cost spike vs 4-week baseline (>25% increase)
- New problem SKU — above category average this week, not last month
- Portfolio incident type trending up across 3+ suppliers
- Category incident rate above portfolio average and worsening week-on-week

**Output:**
- Structured alert rows written to `insights` BigQuery table with severity (critical/warning/watch)
- Claude-generated 3-4 sentence weekly digest for the business review agenda
- Alerts older than 4 weeks deleted on every run
- Confidence ≥ 0.75 → auto-published to Business Dashboard banner
- Confidence < 0.75 → routes to human review

---

## SQL Strategy

**Scheduled reports** use pre-defined SQL templates stored in `metadata.yaml`. Zero LLM involvement in query generation — consistent, version-controlled, deterministic results on every run.

**Ad-hoc reports** use LLM-generated SQL at runtime since the goal is open-ended. The LLM receives enriched column schemas from `metadata.yaml` — each column has its type, description, and usage notes — to prevent invented column names. If a generated query fails in BigQuery, the exact error is fed back to the LLM for automatic correction (up to 2 attempts). Always requires human review before publishing.

---

## Security Architecture

Three independent layers — an attacker must bypass all three simultaneously.

**Layer 1 — Input sanitiser:** Every goal string is scanned for prompt injection patterns before reaching the LLM. Detections are flagged to the audit trail.

**Layer 2 — Column allowlist and SQL validator:** Every SQL query is validated before execution. No dangerous operations, no SELECT *, only allowed columns. Supplier-scoped reports have `WHERE supplierID = 'SUPXXX'` injected automatically. `netRevenue` and `customerID` are permanently blocked columns regardless of report type.

**Layer 3 — IAM:** The service account has read-only access to the allowed dataset only. Even a malicious query that bypasses layers 1 and 2 is rejected at the infrastructure level.

**API rate limiting:** `POST /api/runs` and `POST /api/runs/rerun` are rate-limited to 10 requests per minute per user. Returns 429 on breach.

---

## Control Plane

**Control Plane tab** — Contains two sub-tabs:
- *Queue* — Pending reports awaiting human decision. Shows confidence meter, validation pass/fail counts, hallucination flags. Audit view with Report / Validation / Policy / Data tabs. Decisions: Approve / Edit & Approve / Reject. Rejection with reason triggers automatic agent re-run with correction applied.
- *Observability* — Full run history with confidence scores, decisions, and reviewer names.

**Dashboards**
- *Business Overview* — Weekly insights banner (digest + severity-rated alerts + 4-week history), 7 scorecards with traffic light legend, incident & return rate trend, resolution cost % trend, top 10 suppliers, category breakdown with portfolio average reference line, resolution mix. Cross-filtering on all charts.
- *Supplier Account* — Metrics + portfolio benchmark scorecards, category charts, SKU incident & return tables, return reasons, incident type breakdown, resolution mix. Cross-filtering on category and incident type.

**New Report** — Plain English goal → pipeline runs in background → animated 6-step progress indicator → results shown automatically on completion. Low-confidence reports show amber warning with narrative still visible. Recent Reports section shows all past ad-hoc runs with status and narrative. Optional title field stores a human-readable label alongside the goal.

**Ask** — Conversational natural language → BigQuery SQL → answer table. Multi-turn session with context memory. SQL shown transparently per answer. Auto-corrects SQL errors up to 3 times.

**Supplier Portal** — Authenticated supplier-facing view at agentic-intel.de. Scoped to their `supplierID` — they see only their own data, charts, and any reports shared with them by account managers. Includes a **Customer Voice** tab with structured, interactive view of `sku_comment_intelligence` data.

---

## Landing Page

Standalone portfolio showcase at agentic-intel.de (no login required). Built to demonstrate the project to potential employers:
- Hero section with 6-agent pipeline card
- How it works: 6 steps + 3 delivery modes (scheduled, ad-hoc, spike alerts)
- Sample outputs: weekly dashboard, sales comparison, customer voice, spike alert
- Control plane and audit trail section
- Tech stack: LangGraph, Claude Sonnet 4, BigQuery, FastAPI, Firebase, Cloud Run
- "Explore the demo account" auto-logs in with demo credentials

---

## Demo Account

A protected read-only account for recruiters and stakeholders (demo@agentic-intel.de).

**What demo can do:**
- View all dashboards (business overview and supplier account with pre-built SUP001 data)
- New Report: pre-filled question, fake pipeline animation, pre-written report output
- Ask: 3 static example exchanges showing real SQL and results
- Control Plane Queue: 3 pre-built reports always available for review decisions
- Observability: full run history

**What demo cannot do:**
- Trigger the real agent pipeline (`POST /api/runs` returns 403)
- Trigger re-runs
- Access live Ask endpoints

**Security:** Demo rows in BigQuery are tagged `DEMO_` and never marked as decided, so the queue always appears fresh regardless of decisions made. Rate limiter on all write endpoints.

---

## Auth

Firebase Authentication with custom claims. Four roles enforced at both the React layer and the FastAPI JWT middleware.

- `admin` — full control plane including queue decisions and report creation
- `business` — dashboards, ask, new report (no queue/observability)
- `demo` — read-only across all views, no write operations, pre-built data
- `supplier` — portal only, data scoped to their supplierID

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
| `insights` | Weekly anomaly alerts — severity, signal type, current vs baseline values |
| `insight_digests` | Weekly digest narratives — Claude-generated summary for business review |

---

## Project Structure

```
supplier-bi-agent/
├── agent/
│   ├── graph.py                  # LangGraph pipeline — AgentState, graph wiring
│   ├── comment_intelligence.py   # Comment Intelligence Agent — monthly batch
│   ├── scheduler.py              # Parallel Scheduler Agent — monthly + weekly cycles
│   ├── insight_agent.py          # Insight Agent — weekly anomaly detection
│   ├── nodes/
│   │   ├── discover.py           # Table selection — forces orders table
│   │   ├── pull.py               # SQL generation + execution + auto-correction
│   │   ├── analyse.py            # Ad-hoc aware pre-processor, confidence scoring
│   │   ├── generate.py           # Report narrative + Customer Voice section
│   │   ├── validate.py           # Scope-aware ground truth validation
│   │   ├── review.py             # Policy engine — auto-approve / queue / escalate
│   │   └── publish.py            # GCS + BigQuery publish
│   └── config/
│       ├── metadata.yaml         # Table schemas, SQL templates, allowed columns
│       └── policies.yaml         # Auto-approve rules per report type
├── control_plane/
│   ├── main.py                   # FastAPI — all endpoints, JWT middleware, rate limiter, session store
│   ├── requirements.txt          # Python dependencies
│   ├── Dockerfile                # Multi-stage: Node build + Python serve
│   └── frontend/
│       └── src/
│           ├── App.jsx           # React control plane — landing page, all tabs, supplier portal, demo mode
│           └── firebase.js       # Firebase Auth initialisation
├── scripts/
│   └── set_firebase_claims.py    # Set role claims on Firebase accounts
├── Dockerfile                    # Project-root entry for Cloud Run builds
├── firebase.json                 # Firebase Hosting → Cloud Run proxy
├── .firebaserc                   # Firebase project config
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
| 5 — Reporting, ad-hoc & NL BI | ✅ Complete | React dashboards, ad-hoc reports, conversational query, Cloud Run |
| 6 — Multi-agent | ✅ Complete | Comment Intelligence, Parallel Scheduler, Insight Agent |
| 7 — Supplier portal & demo | ✅ Complete | Firebase Auth · agentic-intel.de · supplier portal · multi-role access · landing page · demo account · rejection feedback loop · dashboard cleanup · nav reorganisation · report title field |
| 8 — Security hardening | ⬜ Planned | BigQuery row-level policies · service account per node · column-level PII security · LangSmith tracing · CI security pipeline |
