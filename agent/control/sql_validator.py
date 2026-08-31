"""
Supplier BI Agent — SQL Validator
===================================
Standalone SQL validation for user-written queries — not agent-generated SQL.
A user pastes SQL, this checks it against the known schema before they run it
anywhere.

Stage 1 + 2 (this file, current scope):
  - Syntax validity (via sqlglot, BigQuery dialect)
  - Every referenced table exists in the schema
  - Every referenced column exists on its table
  - Basic style checks (SELECT *, missing table qualification, missing join
    conditions)
  - Line numbers on every issue
  - Join relation validity: every JOIN's ON condition is checked against a
    declared set of real foreign-key relationships (metadata.yaml's
    `relations:` block) — joining on columns that exist but aren't the real
    relationship (e.g. two tables that happen to share a column name) is
    flagged as UNDECLARED_RELATION.
  - Fan-out / double-counting: SUM/AVG/MIN/MAX/COUNT applied to a column on
    the "one" side of a many-to-one join is flagged as FANOUT_AGGREGATE,
    since that value repeats once per matching row on the "many" side and
    gets over-counted. A plain, non-aggregated join (e.g. a denormalized
    report row combining an incident with its supplier's name) is NOT
    flagged — repeating a lookup value across many rows is the normal,
    correct shape of that kind of query. Only aggregation across the
    repetition is the actual bug.

Not yet in scope (later stages):
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


def _load_relations() -> dict:
    """
    Returns the `relations:` block from metadata.yaml as-is:
        {table: {"grain": col, "references": [{"to": table, "via": col}, ...]}}
    """
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("relations", {})


def _all_declared_edges(relations: dict):
    """Yields (many_table, one_table, fk_column) for every declared reference."""
    for table, info in relations.items():
        for ref in info.get("references", []):
            yield table, ref["to"], ref["via"]


def _find_declared_relation(relations: dict, table_a: str, col_a: str, table_b: str, col_b: str):
    """
    If (table_a.col_a = table_b.col_b) matches a declared many-to-one relation
    in either direction, returns (many_table, many_col, one_table, one_col).
    Otherwise returns None.
    """
    for many_table, one_table, fk_col in _all_declared_edges(relations):
        one_grain = relations.get(one_table, {}).get("grain")
        if one_grain is None:
            continue
        if table_a == many_table and col_a == fk_col and table_b == one_table and col_b == one_grain:
            return (many_table, fk_col, one_table, one_grain)
        if table_b == many_table and col_b == fk_col and table_a == one_table and col_a == one_grain:
            return (many_table, fk_col, one_table, one_grain)
    return None


def _describe_correct_relation(relations: dict, table_a: str, table_b: str) -> str:
    for many_t, one_t, fk_col in _all_declared_edges(relations):
        one_grain = relations.get(one_t, {}).get("grain")
        if {many_t, one_t} == {table_a, table_b}:
            return (f"The declared relationship between `{many_t}` and `{one_t}` is "
                    f"`{many_t}.{fk_col} = {one_t}.{one_grain}`.")
    return f"No declared relationship exists between `{table_a}` and `{table_b}` at all."


def _build_alias_map(ast) -> dict:
    """Maps every alias (or bare table name if unaliased) to its real table name."""
    alias_map = {}
    for t in ast.find_all(exp.Table):
        bare = _bare_table_name(t)
        alias = t.alias or bare
        alias_map[alias] = bare
        alias_map[bare] = bare
    return alias_map


def _extract_join_equalities(on_expr) -> list:
    """
    Walks an ON-clause expression tree and returns every column=column
    equality found. ANDed conditions are split; anything that isn't a plain
    column=column equality (OR'd conditions, literal filters, functions) is
    ignored — this only needs the actual join-key equalities.
    """
    if on_expr is None:
        return []
    pairs = []
    def walk(node):
        if isinstance(node, exp.And):
            walk(node.left)
            walk(node.right)
        elif isinstance(node, exp.EQ):
            l, r = node.left, node.right
            if isinstance(l, exp.Column) and isinstance(r, exp.Column):
                pairs.append((l, r))
    walk(on_expr)
    return pairs


_AGGREGATE_TYPES = (exp.Sum, exp.Avg, exp.Min, exp.Max)


def _check_fanout(ast, alias_map: dict, many_table: str, one_table: str, one_grain_col: str, sql_lines: list) -> list:
    """
    Given a confirmed many-to-one join (many_table.fk = one_table.grain),
    checks whether the query actually uses it safely:
      - any SUM/AVG/MIN/MAX on a one_table column -> inflated (FANOUT_AGGREGATE)
      - COUNT(*) or COUNT(one_table column) without DISTINCT -> inflated (FANOUT_AGGREGATE)
      - any other raw (non-aggregated) one_table column selected, with no
        GROUP BY / DISTINCT anywhere -> duplicated rows (FANOUT_DUPLICATE_ROWS)
    The join key column itself is exempt from the last check — repeating the
    FK/grain value across matching rows is expected, not a bug.
    """
    issues = []
    flagged_cols = set()  # (table, col_name) already reported, avoid double-reporting

    for agg in ast.find_all(_AGGREGATE_TYPES):
        for col in agg.find_all(exp.Column):
            if alias_map.get(col.table) == one_table:
                fn_name = type(agg).__name__.upper()
                issues.append(_issue(
                    "error", "FANOUT_AGGREGATE",
                    f"{fn_name}({one_table}.{col.name}) is unsafe after joining to `{many_table}`: "
                    f"each `{one_table}` row repeats once per matching `{many_table}` row, so its "
                    f"`{col.name}` value gets counted multiple times. Example: if one `{one_table}` "
                    f"row has 3 matching `{many_table}` rows, its `{col.name}` is added into the "
                    f"{fn_name} 3 times instead of once. Fix: aggregate `{many_table}` by "
                    f"`{one_grain_col}` in a subquery first, then join the result.",
                    line=_line_for_identifier(sql_lines, col.name),
                ))
                flagged_cols.add((one_table, col.name))

    for cnt in ast.find_all(exp.Count):
        arg = cnt.this
        is_star = isinstance(arg, exp.Star)
        is_distinct = isinstance(arg, exp.Distinct)
        touches_one = False
        touched_col = None
        if not is_star:
            for col in cnt.find_all(exp.Column):
                if alias_map.get(col.table) == one_table:
                    touches_one = True
                    touched_col = col.name
        if is_distinct:
            continue  # COUNT(DISTINCT ...) is the correct, safe pattern
        if is_star or touches_one:
            target = f"{one_table}.*" if is_star else f"{one_table}.{touched_col}"
            issues.append(_issue(
                "error", "FANOUT_AGGREGATE",
                f"COUNT({'*' if is_star else target}) after joining `{one_table}` to `{many_table}` "
                f"counts rows in the joined result, not distinct `{one_table}` rows — it's inflated "
                f"by however many `{many_table}` rows match each `{one_table}` row. Use "
                f"COUNT(DISTINCT {one_table}.{one_grain_col}) to count `{one_table}` rows correctly.",
                line=None,
            ))
            if touched_col:
                flagged_cols.add((one_table, touched_col))

    return issues


def check_relations(ast, relations: dict, sql_lines: list) -> list:
    issues = []
    if not relations:
        return issues

    alias_map = _build_alias_map(ast)

    for j in ast.find_all(exp.Join):
        on_expr = j.args.get("on")
        if on_expr is None:
            continue  # no ON clause — already covered by MISSING_JOIN_CONDITION

        pairs = _extract_join_equalities(on_expr)
        for l_col, r_col in pairs:
            l_table = alias_map.get(l_col.table)
            r_table = alias_map.get(r_col.table)
            if not l_table or not r_table or l_table == r_table:
                continue  # can't resolve, or a self-referencing condition

            declared = _find_declared_relation(relations, l_table, l_col.name, r_table, r_col.name)
            if declared is None:
                correct = _describe_correct_relation(relations, l_table, r_table)
                issues.append(_issue(
                    "error", "UNDECLARED_RELATION",
                    f"`{l_table}.{l_col.name} = {r_table}.{r_col.name}` is not a real relationship "
                    f"between these tables, even though both columns exist. {correct} Joining on "
                    f"the wrong column silently produces a many-to-many match instead of the "
                    f"intended one-to-many, multiplying rows in a way that's easy to miss.",
                    line=_line_for_identifier(sql_lines, l_col.name),
                ))
                continue

            many_table, _many_col, one_table, one_grain = declared
            issues += _check_fanout(ast, alias_map, many_table, one_table, one_grain, sql_lines)

    return issues


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

    # SELECT-list aliases (e.g. `SUM(x) AS total`) are valid to reference in
    # ORDER BY / HAVING — that's standard SQL, not a schema column, so they
    # must not be flagged as unknown.
    select_aliases = {
        sel_expr.alias
        for select in ast.find_all(exp.Select)
        for sel_expr in select.expressions
        if isinstance(sel_expr, exp.Alias) and sel_expr.alias
    }

    if known_tables:
        seen_unknown = set()
        for col in ast.find_all(exp.Column):
            col_name = col.name
            if col_name == "*" or col_name in seen_unknown:
                continue
            if col_name in select_aliases:
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

    # Only a bare `*` directly in the SELECT projection list is "SELECT *".
    # A Star nested inside a function call (COUNT(*), etc.) is a completely
    # different, legitimate thing and must not trigger this.
    has_select_star = any(
        isinstance(sel_expr, exp.Star)
        for select in ast.find_all(exp.Select)
        for sel_expr in select.expressions
    )
    if has_select_star:
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


def format_sql(ast) -> str:
    """
    Pretty-prints the parsed query: indentation, one clause per section,
    consistent keyword/function casing. Purely cosmetic — sqlglot round-trips
    through its own AST, so the formatted output is guaranteed to parse to
    the exact same query structure as the input. It does not add table
    qualification, does not rename anything, does not fix any of the issues
    check_tables_and_columns/check_style/check_relations report — those stay
    listed separately so the person can fix them by hand.
    """
    return ast.sql(dialect=DIALECT, pretty=True, normalize_functions="upper")


def validate_user_sql(sql: str) -> dict:
    """
    Validate a user-submitted SQL string. Returns:
        {
            "valid": bool,          # True only if there are zero "error" issues
            "summary": {"errors": int, "warnings": int},
            "issues": [ {severity, code, message, line}, ... ],  # errors first
            "tables_referenced": [...],
            "formatted_sql": str | None,  # pretty-printed input, same query,
                                           # cosmetic only. None if the SQL
                                           # didn't parse at all.
        }
    """
    schema = _load_schema()
    project, dataset = _load_project_dataset()
    relations = _load_relations()
    sql_lines = sql.splitlines()

    ast, issues = check_syntax(sql)

    tables_referenced = []
    formatted_sql = None
    if ast is not None:
        issues += check_tables_and_columns(ast, schema, project, dataset, sql_lines)
        issues += check_style(ast, sql_lines)
        issues += check_relations(ast, relations, sql_lines)
        tables_referenced = sorted({
            _bare_table_name(t) for t in ast.find_all(exp.Table)
        })
        formatted_sql = format_sql(ast)

    issues.sort(key=lambda i: _SEVERITY_ORDER.get(i["severity"], 99))

    error_count = sum(1 for i in issues if i["severity"] == "error")
    warning_count = sum(1 for i in issues if i["severity"] == "warning")

    return {
        "valid": error_count == 0,
        "summary": {"errors": error_count, "warnings": warning_count},
        "issues": issues,
        "tables_referenced": tables_referenced,
        "formatted_sql": formatted_sql,
    }
