"""
Supplier BI Agent — Node 1: Discover
======================================
Given a report goal and type, selects which BigQuery tables are needed
and returns their allowed column schemas.

Security:
  - Only tables defined in metadata.yaml are ever returned
  - Input is sanitised for prompt injection before reaching the LLM
  - LLM output is validated against the allowed table list

LLM call: one focused call — "given this goal and these available
tables, which tables do you need and why?"
"""

import json
import os
import re
from pathlib import Path

import yaml
from anthropic import Anthropic


# ── Load metadata config ──────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / "config" / "metadata.yaml"

def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Input sanitiser ───────────────────────────────────────────────────────────

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"forget\s+(all\s+)?(previous|prior|above)\s+instructions",
    r"you\s+are\s+now\s+a",
    r"disregard\s+(all\s+)?instructions",
    r"(export|dump|drop|delete|truncate|update|insert)\s+(the\s+)?(full\s+)?(table|database|all)",
    r"system\s*prompt",
    r"<\s*script",
    r"SELECT\s+\*\s+FROM",
    r";\s*(DROP|DELETE|UPDATE|INSERT|CREATE|ALTER)",
]

def sanitise_input(text: str) -> tuple:
    """
    Scan input for prompt injection patterns.
    Returns (sanitised_text, list_of_warnings).
    Flags but does not block — pipeline decides what to do.
    """
    warnings = []
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            warnings.append(f"Potential injection pattern detected: {pattern}")
    sanitised = re.sub(r"<[^>]+>", "", text)
    return sanitised, warnings


# ── Table summary builder ─────────────────────────────────────────────────────

def _build_table_summary(report_config: dict) -> str:
    """
    Build a concise summary of available tables and their allowed columns
    to pass to the LLM. Never exposes blocked columns.
    """
    lines = []
    for table_name, table_config in report_config["tables"].items():
        cols = table_config["allowed_columns"]
        lines.append(f"\nTable: {table_name}")
        lines.append(f"  Columns: {', '.join(cols)}")
        if table_config.get("filters"):
            lines.append(f"  Default filters: {'; '.join(table_config['filters'])}")
    return "\n".join(lines)


# ── Discover node ─────────────────────────────────────────────────────────────

def discover_node(state: dict) -> dict:
    """
    Node 1 — Discover.

    Reads:  state.report_type, state.goal, state.audience, state.supplier_id
    Writes: state.selected_tables, state.table_schemas,
            state.discover_reasoning, state.errors
    """
    print("  [discover] Starting...")

    config      = _load_config()
    report_type = state["report_type"]
    goal        = state["goal"]
    audience    = state["audience"]
    supplier_id = state.get("supplier_id")
    errors      = list(state.get("errors") or [])

    # ── Validate report type ──────────────────────────────────────────────────
    if report_type not in config["reports"]:
        raise ValueError(
            f"Unknown report_type '{report_type}'. "
            f"Valid types: {list(config['reports'].keys())}"
        )

    report_config = config["reports"][report_type]

    # ── Validate supplier_id for scoped reports ───────────────────────────────
    if report_config.get("supplier_scoped") and not supplier_id:
        raise ValueError(
            f"report_type '{report_type}' requires a supplier_id but none was provided."
        )

    # ── Sanitise input ────────────────────────────────────────────────────────
    sanitised_goal, injection_warnings = sanitise_input(goal)
    if injection_warnings:
        print(f"  [discover] WARNING — injection patterns detected in goal:")
        for w in injection_warnings:
            print(f"    {w}")
        errors.extend(injection_warnings)

    # ── For template reports: tables are pre-defined, skip LLM call ───────────
    sql_mode = report_config.get("sql_mode", "template")

    if sql_mode == "template":
        selected_tables    = list(report_config["tables"].keys())
        discover_reasoning = (
            f"Template report — tables pre-defined in metadata config: "
            f"{selected_tables}. No LLM call needed."
        )
        print(f"  [discover] Template mode — using pre-defined tables: {selected_tables}")

    else:
        # ── Ad-hoc: LLM selects which tables are needed ───────────────────────
        table_summary    = _build_table_summary(report_config)
        available_tables = list(report_config["tables"].keys())

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set.")

        client = Anthropic(api_key=api_key)

        system_prompt = f"""You are the Discover node of a supplier performance BI agent.
Your job is to select which database tables are needed to answer a report goal.

You must ONLY select tables from the provided list. Never suggest tables not in the list.
Return your answer as a JSON object with exactly these fields:
{{
  "selected_tables": ["table1", "table2"],
  "reasoning": "brief explanation of why each table is needed"
}}

Available tables for report type '{report_type}' (audience: {audience}):
{table_summary}

Rules:
- Select the minimum tables needed to answer the goal
- Always include the suppliers table if supplier names or regions are needed
- For incident analysis include both orders and incidents tables
- For return analysis include both orders and returns tables
- For combined analysis include orders, incidents, returns, and suppliers
- Never select a table not in the available tables list above"""

        user_prompt = f"""Report goal: {sanitised_goal}

Report type: {report_type}
Audience: {audience}
{"Supplier ID: " + supplier_id if supplier_id else ""}

Which tables are needed? Return only the JSON object."""

        print(f"  [discover] Ad-hoc mode — calling LLM to select tables...")

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )

        raw = response.content[0].text.strip()

        try:
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw    = raw.strip()
            result = json.loads(raw)

            selected_tables    = result.get("selected_tables", [])
            discover_reasoning = result.get("reasoning", "")

        except (json.JSONDecodeError, KeyError) as e:
            raise ValueError(f"[discover] LLM returned invalid JSON: {e}\nRaw: {raw}")

        # Security: validate selected tables against allowlist
        invalid_tables = [t for t in selected_tables if t not in available_tables]
        if invalid_tables:
            print(f"  [discover] WARNING — LLM selected invalid tables: {invalid_tables}")
            selected_tables = [t for t in selected_tables if t in available_tables]
            errors.append(f"LLM attempted to select invalid tables: {invalid_tables}")

        if not selected_tables:
            raise ValueError("[discover] No valid tables selected after security validation.")

    # ── Build table schemas for downstream nodes ──────────────────────────────
    table_schemas = {}
    for table_name in selected_tables:
        table_cfg = report_config["tables"][table_name]
        table_schemas[table_name] = {
            "allowed_columns": table_cfg["allowed_columns"],
            "filters":         table_cfg.get("filters", []),
            "date_column":     table_cfg.get("date_column"),
            "supplier_scoped": report_config.get("supplier_scoped", False),
        }

    print(f"  [discover] Selected tables: {selected_tables}")
    print(f"  [discover] Reasoning: {discover_reasoning}")

    return {
        "selected_tables":    selected_tables,
        "table_schemas":      table_schemas,
        "discover_reasoning": discover_reasoning,
        "current_node":       "discover",
        "errors":             errors,
    }