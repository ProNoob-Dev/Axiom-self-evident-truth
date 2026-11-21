"""SQL safety harness (sqlglot): parse, whitelist, PII rules, row limits.

Only a single SELECT against the billing schema may run. Anything else is
rejected before execution; execute_readonly runs against SQLite only.
"""

from __future__ import annotations

import sqlite3

import sqlglot
from sqlglot import exp

from . import config

ALLOWED_TABLES = {"customers", "invoices", "payments", "refunds", "disputes"}
# PII columns: selectable only through a masking expression (never bare).
PII_COLUMNS = {"email", "phone", "account_number"}


def _bare_pii_in_select_list(select: exp.Select) -> str | None:
    """Name of a PII column selected bare in this SELECT, or None.

    A PII column wrapped in a function call (e.g. substr(email, 1, 3) or
    count(email)) is masked/aggregated and allowed; a bare column in the
    select list would leak it. WHERE/GROUP BY references are not output.
    """
    for expr in select.expressions:
        masked = {id(c) for f in expr.find_all(exp.Func)
                  for c in f.find_all(exp.Column)}
        for col in expr.find_all(exp.Column):
            if col.name.lower() in PII_COLUMNS and id(col) not in masked:
                return col.name.lower()
    return None


def validate(sql: str) -> tuple[bool, list[str]]:
    """Return (ok, errors). ``ok`` is False when ``errors`` is non-empty."""
    errors: list[str] = []
    stripped = sql.strip().rstrip(";")
    if not stripped:
        return False, ["empty SQL"]
    if ";" in stripped:
        return False, ["multiple statements are not allowed"]
    try:
        parsed = sqlglot.parse_one(stripped, read="sqlite")
    except Exception as exc:  # noqa: BLE001 - sqlglot parse errors are user input
        return False, [f"unparseable SQL: {exc}"]
    if parsed is None:
        return False, ["unparseable SQL"]
    if not isinstance(parsed, exp.Select):
        return False, ["only a single SELECT statement is allowed"]
    # CTE names are defined in the query itself, not real tables.
    cte_names = {c.alias_or_name.lower() for c in parsed.find_all(exp.CTE)}
    tables = {t.name.lower() for t in parsed.find_all(exp.Table)} - cte_names
    unknown = tables - ALLOWED_TABLES
    if unknown:
        errors.append(f"unknown table(s): {', '.join(sorted(unknown))}")
    for select in parsed.find_all(exp.Select):
        bare = _bare_pii_in_select_list(select)
        if bare:
            errors.append(f"PII column selected unmasked: {bare}")
        if select.is_star:
            errors.append("SELECT * is not allowed: list columns explicitly")
    limit = parsed.args.get("limit")
    if limit is not None:
        try:
            n = int(limit.expression.sql(dialect="sqlite"))
        except (ValueError, AttributeError):
            n = None
        if n is None or n > config.ROW_LIMIT:
            errors.append(f"LIMIT must be <= {config.ROW_LIMIT}")
    return (not errors), errors


def enforce_limit(sql: str) -> str:
    """Append LIMIT 100 when the statement has none."""
    parsed = sqlglot.parse_one(sql.strip().rstrip(";"), read="sqlite")
    if parsed.args.get("limit") is None:
        parsed.set("limit", exp.Limit(expression=exp.Literal.number(config.ROW_LIMIT)))
    return parsed.sql(dialect="sqlite")


def execute_readonly(sql: str, db_path: str | None = None) -> tuple[list[str], list[tuple]]:
    """Validate, then run the SELECT against the SQLite billing DB.

    Returns (column_names, rows). Raises ValueError on validation failure.
    """
    ok, errors = validate(sql)
    if not ok:
        raise ValueError("; ".join(errors))
    sql = enforce_limit(sql)
    conn = sqlite3.connect(db_path or config.DB_PATH)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description]
        return columns, cur.fetchall()
    finally:
        conn.close()
