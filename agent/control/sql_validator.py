"""
Supplier BI Agent — SQL Validator
===================================
Standalone SQL validation for user-written queries — not agent-generated SQL.
A user pastes SQL, this checks it against the known schema before they run it
anywhere.

Stage 1 (this file, current scope):
  - Syntax validity (via sqlglot, BigQuery dialect)
  - Every referenced table exists in the schema
  - Every referenced column exists on its table
  - Basic style checks (SELECT *, missing table qualification, missing join
    conditions)
  - Line numbers on every issue

Not yet in scope (later stages):
  - Join / relation validation (needs a `relations:` block in metadata.yaml)
  - Regulation / PII masking checks (needs `policy_rules.yaml`)

Line number accuracy — read this before trusting it blindly:
  sqlglot does NOT attach a line/col to every AST node once parsing succeeds
  — that metadata only survives for the exact point a ParseError happened.
  So SYNTAX_ERROR line numbers are exact, straight from sqlglot. Every other
  issue's line number is found by searching the raw SQL text for the
  relevant identifier (table/column name) and returning the first line it
  appears on. If the same name appears more than once (e.g. the same column
  selected twice, or the same table name as a substring of another word),
  this can point at the wrong occurrence. It's good enough to jump to the
  right area of a query, not a guarantee of the exact line for every case.

Usage:
    from agent.control.sql_validator import validate_user_sql
    result = validate_user_sql(sql_text)
"""

import re
from pathlib import Path
from typing import Optional

import sqlglot
import sqlglot.expressions as exp
import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config" / "metadata.yaml"
DIALECT = "bigquery"


# ── Schema loading ──────────────────────────────────────────────────────────

def _load_schema() -> dict:
    """
    Returns {table_name: set(column_names)}, built from column_schemas
    in metadata.yaml. This is the full table schema, not a per-report
    allowed_columns subset — a user validating arbitrary SQL isn't bound
    to one report's column allowlist.
    """
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    schema = {}
    for table_name, info in cfg.get("column_schemas", {}).items():
        schema[table_name] = set(info.get("columns", {}).keys())
    return schema


def _load_project_dataset() -> tuple:
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    return cfg["project"], cfg["dataset"]


# ── Line-number lookup (best-effort, see module docstring) ───────────────────

def _line_for_identifier(sql_lines: list, name: str) -> Optional[int]:
    """
    First line (1-indexed) containing `name` as a whole word. Returns None
    if not found. This is a text search, not AST-position tracking — see
    the accuracy note in the module docstring.
    """
    pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    for i, line in enumerate(sql_lines, start=1):
        if pattern.search(line):
            return i
    return None


def _line_for_pattern(sql_lines: list, pattern: "re.Pattern") -> Optional[int]:
    for i, line in enumerate(sql_lines, start=1):
        if pattern.search(line):
            return i
    return None


# ── Issue helper ─────────────────────────────────────────────────────────────

def _issue(severity: str, code: str, message: str, line: Optional[int] = None) -> dict:
    return {"severity": severity, "code": code, "message": message, "line": line}


# ── Syntax check ──────────────────────────────────────────────────────────────

def check_syntax(sql: str) -> tuple:
    """
    Parses the SQL with sqlglot. Returns (ast_or_None, list_of_issues).
    On parse failure, ast is None and the caller should stop — no point
    running schema/style checks against SQL that doesn't parse.
    """
    issues = []
    try:
        ast = sqlglot.parse_one(sql, dialect=DIALECT)
        return ast, issues
    except sqlglot.errors.ParseError as e:
        # e.errors is a list of dicts with real line/col from the tokenizer —
        # exact, not text-searched. Use the first error (sqlglot stops
        # meaningfully useful parsing after that point anyway).
        first = e.errors[0]
        issues.append(_issue(
            "error", "SYNTAX_ERROR",
            f"SQL does not parse: {first['description']} "
            f"(near `{first.get('highlight', '')}`)",
            line=first.get("line"),
        ))
        return None, issues


# ── Table / column validation ─────────────────────────────────────────────────

def _bare_table_name(t: exp.Table) -> str:
    """The plain table name, no catalog/dataset qualification, no alias."""
    return t.name


def check_tables_and_columns(ast, schema: dict, project: str, dataset: str, sql_lines: list) -> list:
    issues = []

    # ── Tables ──
    tables_in_query = list(ast.find_all(exp.Table))
    referenced_table_names = set()

    for t in tables_in_query:
        bare_name = _bare_table_name(t)
        referenced_table_names.add(bare_name)
        display_name = t.sql(dialect=DIALECT, identify=False)
        line = _line_for_identifier(sql_lines, bare_name)

        if bare_name not in schema:
            issues.append(_issue(
                "error", "UNKNOWN_TABLE",
                f"Table `{bare_name}` is not in the known schema "
                f"(expected one of: {', '.join(sorted(schema.keys()))})",
                line=line,
            ))
        elif not (t.catalog == project and t.db == dataset):
            issues.append(_issue(
                "warning", "TABLE_NOT_FULLY_QUALIFIED",
                f"`{display_name}` should be fully qualified as "
                f"`{project}.{dataset}.{bare_name}`",
                line=line,
            ))

    # If any table is unknown, column checks against it would be noise —
    # but we can still check columns against tables we DID recognise.
    known_tables = {t for t in referenced_table_names if t in schema}

    # ── Columns ──
    # Build a combined allowed-column set across all known tables referenced.
    # This is intentionally permissive about *which* table a column belongs to
    # when multiple tables are in play (that precision needs join/alias
    # resolution — stage 2). For a single-table query it's exact.
    allowed_columns = set()
    for t in known_tables:
        allowed_columns |= schema[t]

    if known_tables:
        seen_unknown = set()
        for col in ast.find_all(exp.Column):
            col_name = col.name
            if col_name == "*" or col_name in seen_unknown:
                continue
            if col_name not in allowed_columns:
                seen_unknown.add(col_name)
                issues.append(_issue(
                    "error", "UNKNOWN_COLUMN",
                    f"Column `{col_name}` not found on referenced table(s) "
                    f"({', '.join(sorted(known_tables))})",
                    line=_line_for_identifier(sql_lines, col_name),
                ))

    return issues


# ── Style checks ──────────────────────────────────────────────────────────────

def check_style(ast, sql_lines: list) -> list:
    issues = []

    if list(ast.find_all(exp.Star)):
        # The star may be on its own line in a multi-line SELECT list, so
        # don't require SELECT and * on the same line — just find a bare
        # `*` token (not part of a word, not a multiplication like `a*b`).
        star_line = _line_for_pattern(sql_lines, re.compile(r"(?<![\w.])\*(?!\w)"))
        issues.append(_issue(
            "warning", "SELECT_STAR",
            "SELECT * — name columns explicitly",
            line=star_line,
        ))

    # Comma-joins and explicit CROSS JOINs both parse as a Join node with
    # kind=CROSS and no ON condition. Any join with no ON condition is a
    # cartesian-product risk regardless of how it was written.
    for j in ast.find_all(exp.Join):
        if j.args.get("on") is None:
            table_name = j.this.name if isinstance(j.this, exp.Table) else "?"
            issues.append(_issue(
                "warning", "MISSING_JOIN_CONDITION",
                f"Join on `{table_name}` has no ON condition — "
                f"this produces a cross product (comma-join or CROSS JOIN)",
                line=_line_for_identifier(sql_lines, table_name),
            ))

    return issues


# ── Entry point ────────────────────────────────────────────────────────────────

_SEVERITY_ORDER = {"error": 0, "warning": 1}


def validate_user_sql(sql: str) -> dict:
    """
    Validate a user-submitted SQL string. Returns:
        {
            "valid": bool,          # True only if there are zero "error" issues
            "summary": {"errors": int, "warnings": int},
            "issues": [ {severity, code, message, line}, ... ],  # errors first
            "tables_referenced": [...],
        }
    """
    schema = _load_schema()
    project, dataset = _load_project_dataset()
    sql_lines = sql.splitlines()

    ast, issues = check_syntax(sql)

    tables_referenced = []
    if ast is not None:
        issues += check_tables_and_columns(ast, schema, project, dataset, sql_lines)
        issues += check_style(ast, sql_lines)
        tables_referenced = sorted({
            _bare_table_name(t) for t in ast.find_all(exp.Table)
        })

    issues.sort(key=lambda i: _SEVERITY_ORDER.get(i["severity"], 99))

    error_count = sum(1 for i in issues if i["severity"] == "error")
    warning_count = sum(1 for i in issues if i["severity"] == "warning")

    return {
        "valid": error_count == 0,
        "summary": {"errors": error_count, "warnings": warning_count},
        "issues": issues,
        "tables_referenced": tables_referenced,
    }
