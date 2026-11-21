"""Conversational analytics: intake, template SQL generation, result checks.

SQL generation is deterministic and template-based offline (no LLM needed):
metric + filters + group-by map to a parametrised SELECT from the semantic
layer. With an Azure key set, the LLM generates the SQL instead — through
the same validation. Either way a failed generation/validation returns a
structured error or escalates, never an invented answer.
"""

from __future__ import annotations

import calendar
import re
import sqlite3
import statistics
from datetime import date, timedelta

from . import config

_GREETINGS = re.compile(
    r"^(hi|hello|hey|please|kindly|can you|could you|would you|please tell me)\b[ ,]*",
    re.I)
_FILLER = re.compile(r"\b(please|kindly|thanks|thank you)\b", re.I)

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})

_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\blast month\b", re.I), "last_month"),
    (re.compile(r"\bthis month\b", re.I), "this_month"),
    (re.compile(r"\blast week\b", re.I), "last_week"),
    (re.compile(r"\blast 30 days\b", re.I), "last_30_days"),
    (re.compile(r"\byesterday\b", re.I), "yesterday"),
    (re.compile(r"\btoday\b", re.I), "today"),
]


def parse_dates(text: str, today: date | None = None) -> tuple[str, str] | None:
    """Normalise a relative date phrase to an (ISO start, ISO end) range."""
    today = today or date.today()
    lowered = text.lower()
    for pattern, kind in _DATE_PATTERNS:
        if not pattern.search(lowered):
            continue
        if kind == "last_month":
            first_this = today.replace(day=1)
            end = first_this - timedelta(days=1)
            return end.replace(day=1).isoformat(), end.isoformat()
        if kind == "this_month":
            return today.replace(day=1).isoformat(), today.isoformat()
        if kind == "last_week":
            return (today - timedelta(days=7)).isoformat(), today.isoformat()
        if kind == "last_30_days":
            return (today - timedelta(days=30)).isoformat(), today.isoformat()
        if kind == "yesterday":
            day = today - timedelta(days=1)
            return day.isoformat(), day.isoformat()
        if kind == "today":
            return today.isoformat(), today.isoformat()
    month_match = re.search(
        r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b(?:\s+(\d{4}))?",
        lowered)
    if month_match:
        month = _MONTHS[month_match.group(1)]
        year = int(month_match.group(2)) if month_match.group(2) else today.year
        if month > today.month and year == today.year:
            year -= 1  # "October" in November means this year's October, not next
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()
    return None


def _known_cities(db_path: str) -> list[str]:
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT DISTINCT city FROM customers").fetchall()
        conn.close()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []


def _known_customers(db_path: str) -> list[tuple[int, str]]:
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT id, name FROM customers").fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows]
    except sqlite3.Error:
        return []


def extract_entities(text: str, db_path: str | None = None) -> dict:
    """Extract city, customer, group-by from the question."""
    db_path = db_path or config.DB_PATH
    cleaned = _FILLER.sub("", _GREETINGS.sub("", text)).strip()
    lowered = cleaned.lower()
    city = None
    for known in _known_cities(db_path):
        if known.lower() in lowered:
            city = known
            break
    if city is None:
        # A capitalised word after "in"/"for" is a city candidate even when
        # it is not in the seed data — it then correctly yields no rows.
        match = re.search(r"\b(?:in|for|from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", text)
        if match and match.group(1).lower() not in ("last", "this"):
            city = match.group(1)
    customer_id = None
    customer_name = None
    for cid, name in _known_customers(db_path):
        if name.lower() in lowered:
            customer_id, customer_name = cid, name
            break
    group_by = "city" if re.search(r"\bby city\b", lowered) else None
    status = None
    for candidate in ("paid", "pending", "overdue"):
        if re.search(rf"\b{candidate}\b", lowered):
            status = candidate
            break
    return {"cleaned": cleaned, "city": city, "customer_id": customer_id,
            "customer_name": customer_name, "group_by": group_by, "status": status}


def _esc(value: str) -> str:
    return value.replace("'", "''")


def _where(date_col: str, entities: dict, date_range: tuple[str, str] | None) -> str:
    conds: list[str] = []
    if entities.get("city"):
        conds.append(f"c.city = '{_esc(entities['city'])}'")
    if entities.get("customer_id"):
        conds.append(f"i.customer_id = {entities['customer_id']}")
    if date_range:
        conds.append(f"{date_col} >= '{date_range[0]}' AND {date_col} <= '{date_range[1]}'")
    if entities.get("status"):
        conds.append(f"i.status = '{entities['status']}'")
    return " AND ".join(conds) if conds else "1=1"


def _single_table(metric: str, sql_expr: str, from_sql: str, date_col: str,
                  entities: dict, date_range: tuple[str, str] | None) -> str:
    where = _where(date_col, entities, date_range)
    if entities.get("group_by") == "city":
        return (f"SELECT c.city AS city, {sql_expr} AS {metric} FROM {from_sql} "
                f"WHERE {where} GROUP BY c.city ORDER BY {metric} DESC")
    return f"SELECT {sql_expr} AS {metric} FROM {from_sql} WHERE {where}"


def _two_part(metric: str, pay_expr: str, ref_expr: str, entities: dict,
              date_range: tuple[str, str] | None, combine_grouped: str,
              combine_total: str) -> str:
    """Metrics that are payments minus/divided by refunds, per city or total.

    ``combine_grouped`` runs against the LEFT JOINed CTEs (pay.total /
    ref.total in scope); ``combine_total`` must use scalar subqueries
    because the outer SELECT has no FROM.
    """
    pay_where = _where("p.paid_on", entities, date_range)
    ref_where = _where("r.refunded_on", entities, date_range)
    pay_from = ("payments p JOIN invoices i ON p.invoice_id = i.id "
                "JOIN customers c ON i.customer_id = c.id")
    ref_from = ("refunds r JOIN invoices i ON r.invoice_id = i.id "
                "JOIN customers c ON i.customer_id = c.id")
    if entities.get("group_by") == "city":
        city_filter = (f" WHERE city = '{_esc(entities['city'])}'"
                       if entities.get("city") else "")
        return (
            f"WITH pay AS (SELECT c.city AS city, {pay_expr} AS total FROM {pay_from} "
            f"WHERE {pay_where} GROUP BY c.city), "
            f"ref AS (SELECT c.city AS city, {ref_expr} AS total FROM {ref_from} "
            f"WHERE {ref_where} GROUP BY c.city), "
            f"cities AS (SELECT DISTINCT city FROM customers{city_filter}) "
            f"SELECT cities.city AS city, {combine_grouped} AS {metric} FROM cities "
            f"LEFT JOIN pay ON pay.city = cities.city "
            f"LEFT JOIN ref ON ref.city = cities.city ORDER BY {metric} DESC")
    return (
        f"WITH pay AS (SELECT {pay_expr} AS total FROM {pay_from} WHERE {pay_where}), "
        f"ref AS (SELECT {ref_expr} AS total FROM {ref_from} WHERE {ref_where}) "
        f"SELECT {combine_total} AS {metric}")


def build_sql(metric: str, entities: dict,
              date_range: tuple[str, str] | None) -> str | None:
    """Deterministic template SQL for a metric, or None when unsupported."""
    if metric == "refunds":
        return _single_table(
            "refunds", "COALESCE(SUM(r.amount_paise), 0)",
            "refunds r JOIN invoices i ON r.invoice_id = i.id "
            "JOIN customers c ON i.customer_id = c.id",
            "r.refunded_on", entities, date_range)
    if metric == "avg_invoice_value":
        return _single_table(
            "avg_invoice_value", "AVG(i.amount_paise)",
            "invoices i JOIN customers c ON i.customer_id = c.id",
            "i.issued_on", entities, date_range)
    if metric == "revenue":
        return _two_part(
            "revenue", "COALESCE(SUM(p.amount_paise), 0)",
            "COALESCE(SUM(r.amount_paise), 0)", entities, date_range,
            "COALESCE(pay.total, 0) - COALESCE(ref.total, 0)",
            "(SELECT total FROM pay) - (SELECT total FROM ref)")
    if metric == "refund_rate":
        return _two_part(
            "refund_rate", "COALESCE(SUM(p.amount_paise), 0)",
            "COALESCE(SUM(r.amount_paise), 0)", entities, date_range,
            "CASE WHEN COALESCE(pay.total, 0) = 0 THEN 0.0 "
            "ELSE CAST(COALESCE(ref.total, 0) AS REAL) / pay.total END",
            "CASE WHEN (SELECT total FROM pay) = 0 THEN 0.0 "
            "ELSE CAST((SELECT total FROM ref) AS REAL) / (SELECT total FROM pay) END")
    if metric == "dispute_rate":
        where_d = _where("d.opened_on", entities, date_range)
        where_i = _where("i.issued_on", entities, date_range)
        if entities.get("group_by") == "city":
            city_filter = (f" WHERE city = '{_esc(entities['city'])}'"
                           if entities.get("city") else "")
            return (
                "WITH dsp AS (SELECT c.city AS city, COUNT(*) AS total FROM disputes d "
                "JOIN customers c ON d.customer_id = c.id "
                f"WHERE {where_d} GROUP BY c.city), "
                "inv AS (SELECT c.city AS city, COUNT(*) AS total FROM invoices i "
                "JOIN customers c ON i.customer_id = c.id "
                f"WHERE {where_i} GROUP BY c.city), "
                f"cities AS (SELECT DISTINCT city FROM customers{city_filter}) "
                "SELECT cities.city AS city, CASE WHEN COALESCE(inv.total, 0) = 0 THEN 0.0 "
                "ELSE CAST(COALESCE(dsp.total, 0) AS REAL) / inv.total END AS dispute_rate "
                "FROM cities LEFT JOIN dsp ON dsp.city = cities.city "
                "LEFT JOIN inv ON inv.city = cities.city ORDER BY dispute_rate DESC")
        return (
            "WITH dsp AS (SELECT COUNT(*) AS total FROM disputes d "
            "JOIN invoices i ON d.invoice_id = i.id "
            "JOIN customers c ON i.customer_id = c.id "
            f"WHERE {where_d}), "
            "inv AS (SELECT COUNT(*) AS total FROM invoices i "
            "JOIN customers c ON i.customer_id = c.id "
            f"WHERE {where_i}) "
            "SELECT CASE WHEN (SELECT total FROM inv) = 0 THEN 0.0 "
            "ELSE CAST((SELECT total FROM dsp) AS REAL) / (SELECT total FROM inv) END "
            "AS dispute_rate")
    if metric == "invoice_count":
        where = _where("i.issued_on", entities, date_range)
        if entities.get("group_by") == "city":
            return (f"SELECT c.city AS city, COUNT(*) AS invoice_count FROM invoices i "
                    f"JOIN customers c ON i.customer_id = c.id WHERE {where} "
                    f"GROUP BY c.city ORDER BY invoice_count DESC")
        return (f"SELECT COUNT(*) AS invoice_count FROM invoices i "
                f"JOIN customers c ON i.customer_id = c.id WHERE {where}")
    return None


def detect_count_query(text: str) -> str | None:
    """'How many invoices...' has no semantic-layer metric; it is a count."""
    lowered = text.lower()
    if re.search(r"\bhow many\b", lowered) and "invoice" in lowered:
        return "invoice_count"
    return None


def validate_result(columns: list[str], rows: list[tuple]) -> dict:
    """Empty-result, outlier and schema-conformance checks on query output."""
    checks: dict = {"empty_result": len(rows) == 0, "outliers": [],
                    "schema_conformant": bool(columns)}
    if len(rows) >= 4 and len(columns) >= 2:
        values = [r[1] for r in rows if isinstance(r[1], (int, float))]
        if len(values) >= 4:
            median = statistics.median(values)
            if median > 0:
                checks["outliers"] = [
                    {"label": str(r[0]), "value": r[1]}
                    for r in rows
                    if isinstance(r[1], (int, float)) and r[1] > 3 * median]
    return checks


def format_inr(paise: int | float) -> str:
    """Paise -> ₹ with Indian digit grouping (12,34,567)."""
    rupees = int(round(paise / 100))
    text = str(abs(rupees))
    if len(text) > 3:
        last3, rest = text[-3:], text[:-3]
        groups = []
        while rest:
            groups.append(rest[-2:])
            rest = rest[:-2]
        text = ",".join(reversed(groups)) + "," + last3
    return f"{'−' if rupees < 0 else ''}₹{text}"


def format_answer(metric: str, columns: list[str], rows: list[tuple],
                  entities: dict, date_range: tuple[str, str] | None) -> str:
    scope = ""
    if entities.get("city"):
        scope += f" in {entities['city']}"
    if date_range:
        scope += f" from {date_range[0]} to {date_range[1]}"
    if not rows:
        return f"No rows found for {metric.replace('_', ' ')}{scope}."
    if len(columns) == 1:
        value = rows[0][0]
        if metric in ("refund_rate", "dispute_rate"):
            return f"{metric.replace('_', ' ').title()}{scope}: {value:.2%}."
        if metric == "invoice_count":
            return f"Invoice count{scope}: {int(value)}."
        return f"{metric.replace('_', ' ').title()}{scope}: {format_inr(value)}."
    lines = [f"{metric.replace('_', ' ').title()} by city{scope}:"]
    for row in rows:
        value = row[1]
        if metric in ("refund_rate", "dispute_rate"):
            rendered = f"{value:.2%}"
        elif metric == "invoice_count":
            rendered = str(int(value))
        else:
            rendered = format_inr(value)
        lines.append(f"  {row[0]}: {rendered}")
    return "\n".join(lines)
