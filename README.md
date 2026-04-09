# Supplier Performance BI Agent

An autonomous BI system that independently queries, analyses, and generates supplier performance reports — with a human-in-the-loop control plane and security controls built into the pipeline from the ground up.

Built as a proof of concept for supplier performance intelligence: identifying problematic categories and SKUs, the most common incident and return patterns, and generating contextual improvement plans per supplier.

---

## What it does

The system serves two distinct audiences from the same data pipeline:

**Business overview** — portfolio-level analysis across all 20 suppliers. Incident rates, return rates, resolution costs, category and supplier rankings, trend detection. Runs weekly on a schedule, published automatically to Looker Studio.

**Supplier account management** — one report per supplier, drilled down to SKU and customer level. Incident type breakdown, return reason patterns, resolution cost analysis, peer benchmarking, and a contextual improvement plan with specific actions per flagged SKU. Runs monthly on a schedule, shareable directly with the supplier.

Both report types flow through the same agent pipeline. The audience determines what data is exposed, how it's scoped, and what the generated narrative focuses on.

---

## Architecture

```
Trigger (Cloud Scheduler / on-demand)
    │
    ▼
Node 1 — Discover        reads metadata config → selects tables (no LLM for scheduled)
    │
    ▼
Node 2 — Pull            runs SQL templates → validates → serialises results
    │
    ▼
Node 3 — Analyse         Python pre-processing → LLM structured JSON analysis
    │
    ▼
Node 4 — Generate        JSON → dual-audience narrative report
    │
    ▼
Node 4b — Validate       re-queries BigQuery → compares report vs ground truth
    │
    ▼
Node 5 — Review          policy engine → auto-approve / queue / escalate
    │
    ▼
Node 6 — Publish         GCS + BigQuery approved_reports (→ Looker Studio Phase 5)
```

State flows through every node as a typed `AgentState` dictionary. Each node reads what it needs and writes its output back to state. Nothing is shared between nodes except through state — no global variables, no side effects.

---

## SQL strategy

A deliberate design decision separates scheduled reports from ad-hoc requests:

**Scheduled reports** (`weekly_supplier_overview`, `monthly_supplier_account`, etc.) use pre-defined SQL templates stored in `agent/config/metadata.yaml`. Templates are version-controlled, tested, and locked. Zero LLM involvement in query generation — consistent results on every run with no variance.

**Ad-hoc reports** (`adhoc_business`, `adhoc_supplier`) use LLM-generated SQL at runtime since the goal is open-ended. Constrained to flat `GROUP BY` aggregations — no CTEs, no window functions, no subqueries. Always requires human review before publishing.

---

## Security architecture

Three independent layers — an attacker must bypass all three simultaneously:

**Layer 1 — Input sanitiser**
Every goal string is scanned for prompt injection patterns before reaching the LLM. Patterns include instruction override attempts, SQL injection fragments, and script tags. Detections are flagged to the audit trail and the human review gate.

**Layer 2 — Column allowlist and SQL validator**
Every SQL query (template or LLM-generated) is validated before execution. Checks: no dangerous operations (DROP, DELETE, UPDATE etc.), correct table reference, no SELECT *, only columns defined in the metadata config. Supplier-scoped reports have a `WHERE supplierID = 'SUPXXX'` filter injected automatically — the LLM cannot bypass this.

**Layer 3 — IAM at the infrastructure level**
The service account running BigQuery queries has read-only access to the allowed dataset only. Even a maliciously crafted query that bypasses layers 1 and 2 would be rejected by BigQuery at the infrastructure level.

Additional controls: `netRevenue` (internal margin data) and `customerID` are blocked columns — never exposed regardless of report type. Maximum row limits prevent full table scans reaching the LLM.

---

## Data layer

Synthetic supplier performance dataset — 500k orders across 20 suppliers, 7 product categories, 301 SKUs, 12 months of history. Appended daily via a Cloud Function.

| Table | Seed rows | Daily append | Description |
|---|---|---|---|
| orders | 500k | ~250/day | Core transaction table |
| incidents | ~56k | ~25/day (10% of orders) | 10% incident rate, unevenly distributed |
| returns | ~30k | ~12/day (5% of orders) | 5% buyers remorse return rate |
| suppliers | 20 | static | Reference table |

**Intentional signal patterns** designed for the agent to detect:

- 5 preferred suppliers account for ~78% of order volume and the majority of incidents — agent must distinguish absolute incident counts from incident rate
- SUP003 has a sharp incident spike starting 3 months ago — early warning signal
- Electronics and Toys & Games carry elevated incident rates independent of supplier
- Supplier-direct fulfilment has 4× the lost item rate versus warehouse — root cause signal
- Budget price tier has 2× the return rate of premium — price-quality correlation
- Probationary suppliers have higher incident rates proportionally despite lower volume

Customer comments on incidents and returns were enriched using Claude with product-specific context derived from SKU codes — a chair cannot have a cracked screen, a tablet cannot have a broken leg. Comments are 1-2 sentences, specific and concrete, designed to give the NLP layer genuine signal to work with.

---

## Project structure

```
supplier-bi-agent/
├── agent/
│   ├── config/
│   │   ├── metadata.yaml      # report definitions, SQL templates, security config
│   │   └── policies.yaml      # publish rules per report type — policy engine input
│   ├── control/
│   │   └── policy_engine.py   # deterministic rule evaluator — no LLM
│   ├── nodes/
│   │   ├── discover.py        # Node 1 — table selection
│   │   ├── pull.py            # Node 2 — SQL execution and result serialisation
│   │   ├── analyse.py         # Node 3 — pre-processing + structured JSON analysis
│   │   ├── generate.py        # Node 4 — dual-audience narrative report generation
│   │   ├── validate.py        # Node 4b — semantic validation vs BigQuery ground truth
│   │   ├── review.py          # Node 5 — policy engine + route to queue / auto-approve
│   │   └── publish.py         # Node 6 — GCS + BigQuery approved_reports
│   └── graph.py               # LangGraph AgentState and full 7-node graph
├── data/
│   ├── seed/
│   │   ├── setup_bigquery.py       # creates dataset, tables, partitioning, clustering
│   │   ├── setup_control_store.py  # creates agent_runs, validation_results, human_decisions, pending_reports
│   │   ├── seed_data.py            # generates 500k-row synthetic dataset
│   │   └── enrich_comments.py      # Claude-powered comment enrichment with checkpoint/resume
│   └── daily_append/
│       ├── main.py            # Cloud Function — ~250 rows/day
│       └── requirements.txt
├── control_plane/             # Semantic control plane UI
│   ├── main.py                # FastAPI backend — reads/writes BigQuery
│   ├── requirements.txt
│   ├── Dockerfile
│   └── frontend/              # React app — deployed to Cloud Run
│       ├── src/
│       │   ├── App.jsx        # full control plane UI — queue, audit, decision, observability
│       │   └── main.jsx
│       ├── index.html
│       ├── package.json
│       └── vite.config.js
├── dashboards/                # Looker Studio config — Phase 5
├── docs/
│   └── bi-agent-roadmap.html  # interactive project tracker
├── test_agent.py              # test suite (5 tests — all passing)
└── .env.example               # required environment variables
```

---

## Semantic control plane

Phase 4 implements governance as an active operational layer across four interdependent components:

**Semantic validation (Node 4b)** — after Generate, re-queries BigQuery directly and compares key metrics stated in the report against ground truth. Incidents and returns are joined to orders on `orderID` so resolution costs always align with the same order window as revenue. Deviations above 10% are flagged, above 20% are hallucination candidates. Improvement actions are checked to ensure they reference specific SKUs or categories.

**Policy engine** — `policies.yaml` defines publish rules per report type. A deterministic evaluator (no LLM) produces one of three decisions: `auto_approve`, `route_to_queue`, or `escalate`. Rules cover confidence threshold, validation pass rate, zero hallucination tolerance, metric deviation limits, required report sections, minimum improvement actions, and SKU citation requirements. Soft rule failures route to queue, hard failures escalate.

**Observability store** — four BigQuery tables: `agent_runs` (full pipeline trace per execution), `validation_results` (expected vs actual metrics), `human_decisions` (every approve/reject/edit with reviewer and reason), `pending_reports` (review queue). Human decisions feed back into retry context.

**React control plane** — FastAPI backend + React frontend deployed to Cloud Run. Four views: run queue with confidence and validation status, audit view with report + validation results + policy rules + raw SQL side by side, decision interface (approve / edit and approve / reject with required reason), and observability dashboard with run history and confidence trends. Accessed securely via `gcloud run services proxy`.

| Layer | Technology | Purpose |
|---|---|---|
| Language | Python 3.11 | All agent and pipeline code |
| Agent orchestration | LangGraph | Stateful directed graph, typed state, human-in-the-loop interrupts |
| LLM | Claude API | Discover (ad-hoc), Pull (ad-hoc SQL), Analyse, Generate |
| Data warehouse | BigQuery | All data storage, partitioned and clustered per table |
| Tracing | LangSmith | Node-by-node trace, behavioural eval suite — Phase 3 |
| Reporting frontend | Looker Studio | Native BigQuery connector, shareable dashboards — Phase 5 |
| Control plane | React + Cloud Run | Audit UI, approve/reject, run history — Phase 4 |
| Daily data append | Cloud Functions Gen 2 | Serverless, triggered by Pub/Sub |
| Scheduling | Cloud Scheduler | Cron → Pub/Sub → Cloud Functions / Cloud Run |
| Report storage | GCS | Raw, approved, audit log buckets |
| Secrets | Secret Manager | All credentials, zero secrets in code |
| Infra monitoring | GCP Security Command Center | Continuous posture assessment |

---

## Build status

| Phase | Status | Description |
|---|---|---|
| 1 — Data layer | ✅ Complete | BigQuery schema, 500k seed, daily append, Cloud Scheduler |
| 2 — Agent foundation | ✅ Complete | Discover + Pull nodes, LangGraph state, SQL templates, security layer |
| 3 — Intelligence | ✅ Complete | Analyse + Generate nodes, dual-audience reports, SKU improvement plans |
| 4 — Semantic control plane | ✅ Complete | Validation node, policy engine, observability store, React UI on Cloud Run |
| 5 — Looker Studio | 🔄 Next | Dashboards, hardening, end-to-end test |
| 6 — Multi-agent | ⬜ Future | Router agent, parallel execution, Vertex AI NLP |
| 7 — Supplier portal | ⬜ Future | Firebase Auth, supplier-facing React views |


