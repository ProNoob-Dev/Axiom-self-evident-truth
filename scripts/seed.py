"""Build data/billing.sqlite from scratch (idempotent).

Dates are relative to today so "last month" / "this month" questions always
have data. A few Mumbai refunds are pinned into last month so the demo
question "total refunds in Mumbai last month" always returns rows.
"""

from __future__ import annotations

import calendar
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "billing.sqlite"

SCHEMA = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL,
    email TEXT NOT NULL, phone TEXT NOT NULL, account_number TEXT NOT NULL);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_paise INTEGER NOT NULL, issued_on TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE payments (
    id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    amount_paise INTEGER NOT NULL, paid_on TEXT NOT NULL, method TEXT NOT NULL);
CREATE TABLE refunds (
    id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    amount_paise INTEGER NOT NULL, reason TEXT NOT NULL, refunded_on TEXT NOT NULL);
CREATE TABLE disputes (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
    invoice_id INTEGER NOT NULL REFERENCES invoices(id), reason TEXT NOT NULL,
    status TEXT NOT NULL, opened_on TEXT NOT NULL,
    resolved_on TEXT, resolution TEXT);
"""

CUSTOMERS = [
    ("Aarav Mehta", "Mumbai"), ("Priya Sharma", "Mumbai"),
    ("Rohan Iyer", "Mumbai"), ("Sneha Kulkarni", "Pune"),
    ("Vikram Nair", "Pune"), ("Ananya Reddy", "Bengaluru"),
    ("Karan Malhotra", "Bengaluru"), ("Divya Menon", "Delhi"),
    ("Arjun Patel", "Delhi"), ("Meera Joshi", "Chennai"),
    ("Sanjay Gupta", "Hyderabad"), ("Nisha Agarwal", "Kolkata"),
    ("Rahul Verma", "Ahmedabad"), ("Kavya Singh", "Mumbai"),
    ("Aditya Rao", "Bengaluru"),
]

REFUND_REASONS = ["duplicate charge", "service not delivered",
                  "wrong amount billed", "subscription cancelled", "product returned"]
DISPUTE_REASONS = ["duplicate charge", "charged twice", "service not delivered",
                   "wrong amount", "subscription cancelled after payment",
                   "unauthorised transaction"]


def _last_month_range(today: date) -> tuple[date, date]:
    first_this = today.replace(day=1)
    last_month_end = first_this - timedelta(days=1)
    return last_month_end.replace(day=1), last_month_end


def seed(db_path: Path = DB_PATH, today: date | None = None) -> Path:
    today = today or date.today()
    rng = random.Random(42)
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        customers = []
        for i, (name, city) in enumerate(CUSTOMERS, start=1):
            email = f"{name.lower().replace(' ', '.')}@example.com"
            phone = f"9{rng.randrange(100000000, 999999999)}"
            account = f"ACC{rng.randrange(10000000, 99999999)}"
            conn.execute(
                "INSERT INTO customers VALUES (?,?,?,?,?,?)",
                (i, name, city, email, phone, account))
            customers.append((i, name, city))
        mumbai = [c for c in customers if c[2] == "Mumbai"]
        invoices = []
        for inv_id in range(1, 41):
            cust_id = rng.choice(customers)[0]
            amount = rng.randrange(5_000, 2_500_000)
            issued = today - timedelta(days=rng.randrange(0, 120))
            status = rng.choices(["paid", "pending", "overdue"],
                                 weights=[70, 20, 10])[0]
            conn.execute("INSERT INTO invoices VALUES (?,?,?,?,?)",
                         (inv_id, cust_id, amount, issued.isoformat(), status))
            invoices.append((inv_id, cust_id, amount, issued, status))
        paid = [inv for inv in invoices if inv[4] == "paid"]
        for pay_id, inv in enumerate(paid[:35], start=1):
            conn.execute(
                "INSERT INTO payments VALUES (?,?,?,?,?)",
                (pay_id, inv[0], inv[2], (inv[3] + timedelta(days=rng.randrange(0, 7))).isoformat(),
                 rng.choice(["upi", "card", "netbanking"])))
        refunded = rng.sample(paid, 10)
        for ref_id, inv in enumerate(refunded, start=1):
            amount = min(inv[2], rng.randrange(1_000, inv[2] + 1))
            conn.execute(
                "INSERT INTO refunds VALUES (?,?,?,?,?)",
                (ref_id, inv[0], amount, rng.choice(REFUND_REASONS),
                 (inv[3] + timedelta(days=rng.randrange(1, 30))).isoformat()))
        # Pin two Mumbai refunds into last month so the demo question lands.
        lm_start, lm_end = _last_month_range(today)
        mumbai_paid = [inv for inv in paid if inv[1] in {c[0] for c in mumbai}]
        for ref_id, inv in enumerate(mumbai_paid[:2], start=11):
            conn.execute(
                "INSERT INTO refunds VALUES (?,?,?,?,?)",
                (ref_id, inv[0], rng.randrange(5_000, 100_000),
                 "duplicate charge",
                 (lm_start + timedelta(days=rng.randrange(0, (lm_end - lm_start).days))).isoformat()))
        disputed = rng.sample(invoices, 10)
        for dsp_id, inv in enumerate(disputed, start=1):
            opened = inv[3] + timedelta(days=rng.randrange(1, 20))
            resolved = rng.random() < 0.6
            conn.execute(
                "INSERT INTO disputes VALUES (?,?,?,?,?,?,?,?)",
                (dsp_id, inv[1], inv[0], rng.choice(DISPUTE_REASONS),
                 "resolved" if resolved else "open", opened.isoformat(),
                 (opened + timedelta(days=rng.randrange(1, 10))).isoformat() if resolved else None,
                 "Refund issued" if resolved else None))
        conn.commit()
    finally:
        conn.close()
    return db_path


if __name__ == "__main__":
    path = seed(Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH)
    conn = sqlite3.connect(path)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("customers", "invoices", "payments", "refunds", "disputes")}
    conn.close()
    print(f"seeded {path}: {counts}")
