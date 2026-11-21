"""Disputes slice: precedent search + reason clustering + draft resolution.

Live disputes come from data/billing.sqlite (joined to customers/invoices
for context). Precedents are the historic resolved corpus in
data/disputes.json — a second store is never built for live disputes.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import audit, config
from .analytics import format_inr
from .pii import mask_pii
from .retrieval import Doc, get_retriever

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = config.DB_PATH
PRECEDENTS_PATH = BASE_DIR / "data" / "disputes.json"

# Raw reason -> (cluster id, cluster label). Live reasons that match no
# known wording fall into ("other", <raw reason>) and get no precedent.
CLUSTER_MAP = {
    "duplicate charge": ("duplicate_billing", "duplicate billing"),
    "charged twice": ("duplicate_billing", "duplicate billing"),
    "duplicate billing": ("duplicate_billing", "duplicate billing"),
    "refund delay": ("refund_delay", "refund delay"),
    "refund not received": ("refund_delay", "refund delay"),
    "subscription cancelled after payment": ("refund_delay", "refund delay"),
    "wrong amount": ("wrong_amount", "wrong amount"),
    "wrong amount billed": ("wrong_amount", "wrong amount"),
    "service not delivered": ("service_not_delivered", "service not delivered"),
    "unauthorised transaction": ("unauthorised_transaction", "unauthorised transaction"),
}

PRECEDENT_THRESHOLD = 0.10  # low: lexical overlap with a precedent is enough
router = APIRouter()

_DISPUTE_ID = re.compile(r"^(?:dsp[- ]?)?(\d+)$", re.I)


def parse_dispute_id(raw: str) -> int | None:
    match = _DISPUTE_ID.match(raw.strip())
    return int(match.group(1)) if match else None


def cluster_for(reason: str) -> tuple[str, str]:
    return CLUSTER_MAP.get(reason.strip().lower(), ("other", reason.strip().lower()))


def _row_to_dispute(row: sqlite3.Row) -> dict:
    cluster_id, cluster_label = cluster_for(row["reason"])
    return {
        "id": row["id"],
        "reason": row["reason"],
        "reason_cluster": cluster_id,
        "reason_cluster_label": cluster_label,
        "status": row["status"],
        "opened_on": row["opened_on"],
        "resolved_on": row["resolved_on"],
        "resolution": row["resolution"],
        "customer": {
            "name": row["customer_name"],
            "city": row["city"],
            "email": mask_pii(row["email"]),
            "phone": mask_pii(row["phone"]),
        },
        "invoice": {"id": row["invoice_id"], "amount_paise": row["amount_paise"]},
    }


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(db_path) if db_path is not None else DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_dispute(dispute_id: int, db_path: str | Path | None = None) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT d.*, c.name AS customer_name, c.city, c.email, c.phone, "
            "i.amount_paise FROM disputes d "
            "JOIN customers c ON d.customer_id = c.id "
            "JOIN invoices i ON d.invoice_id = i.id WHERE d.id = ?",
            (dispute_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_dispute(row) if row else None


def list_disputes(db_path: str | Path | None = None) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT d.*, c.name AS customer_name, c.city, c.email, c.phone, "
            "i.amount_paise FROM disputes d "
            "JOIN customers c ON d.customer_id = c.id "
            "JOIN invoices i ON d.invoice_id = i.id ORDER BY d.id").fetchall()
    finally:
        conn.close()
    return [_row_to_dispute(r) for r in rows]


def reason_clusters(db_path: str | Path | None = None) -> dict[str, int]:
    return dict(Counter(d["reason_cluster_label"] for d in list_disputes(db_path)))


def _precedents(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path is not None else PRECEDENTS_PATH
    return json.loads(p.read_text(encoding="utf-8"))["precedents"]


def match_dispute(question: str, db_path: str | Path | None = None) -> dict | None:
    """Best live-dispute match for a question: explicit DSP-N id first,
    else TF-IDF over reason + customer name."""
    match = re.search(r"(?:dsp|dispute)[- ]?(\d+)", question, re.I)
    if match:
        found = load_dispute(int(match.group(1)), db_path)
        if found:
            return found
    disputes = list_disputes(db_path)
    retriever = get_retriever([
        Doc(id=str(d["id"]),
            text=f"{d['reason']} {d['reason_cluster_label']} {d['customer']['name']}")
        for d in disputes])
    hits = retriever.retrieve(question, k=1)
    if not hits:
        return None
    return next(d for d in disputes if d["id"] == int(hits[0].doc.id))


def resolve_dispute(dispute_id: int, db_path: str | Path | None = None,
                    precedents_path: str | Path | None = None,
                    audit_path: str | Path | None = None) -> dict:
    """Precedent search + draft resolution. No precedent above threshold
    means an honest human-review route, never an invented resolution."""
    request_id = "dsp_" + uuid.uuid4().hex[:12]
    dispute = load_dispute(dispute_id, db_path)
    if dispute is None:
        raise KeyError(dispute_id)
    precedents = _precedents(precedents_path)
    retriever = get_retriever([
        Doc(id=p["id"],
            text=f"{p['reason']} {p['resolution']} {p['customer_segment']}")
        for p in precedents])
    query = f"{dispute['reason']} {dispute['reason_cluster_label']}"
    by_id = {p["id"]: p for p in precedents}
    scored = [
        {"id": s.doc.id, "reason": by_id[s.doc.id]["reason"],
         "resolution": by_id[s.doc.id]["resolution"],
         "score": round(s.score, 3)}
        for s in retriever.retrieve(query, k=3) if s.score >= PRECEDENT_THRESHOLD]
    if not scored:
        draft = ("No precedent found above the match threshold for this "
                 f"{dispute['reason_cluster_label']} dispute — human review required. "
                 "No resolution drafted.")
        result = {"dispute": dispute,
                  "reason_cluster": dispute["reason_cluster_label"],
                  "precedents": [], "citations": [],
                  "draft_resolution": draft, "human_review": True}
    else:
        cites = ", ".join(p["id"] for p in scored)
        draft = (
            f"Draft resolution for dispute {dispute_id} "
            f"({dispute['reason']}, {format_inr(dispute['invoice']['amount_paise'])}): "
            f"following precedent {cites} — {scored[0]['resolution']} "
            "Apply the same remedy, confirm the credit with the customer, "
            "and record the outcome against the precedent.")
        result = {"dispute": dispute,
                  "reason_cluster": dispute["reason_cluster_label"],
                  "precedents": scored, "citations": [p["id"] for p in scored],
                  "draft_resolution": draft, "human_review": False}
    audit.append("dispute_resolved", request_id,
                 {"dispute_id": dispute_id,
                  "reason_cluster": dispute["reason_cluster_label"],
                  "precedent_ids": result["citations"],
                  "human_review": result["human_review"]}, path=audit_path)
    return result


# -- router --------------------------------------------------------------
@router.get("/disputes")
def list_all():
    return {"disputes": list_disputes(), "reason_clusters": reason_clusters()}


@router.get("/disputes/{dispute_id}")
def get_one(dispute_id: str):
    parsed = parse_dispute_id(dispute_id)
    if parsed is None:
        raise HTTPException(status_code=422, detail=f"bad dispute id {dispute_id}")
    dispute = load_dispute(parsed)
    if dispute is None:
        raise HTTPException(status_code=404, detail=f"unknown dispute {dispute_id}")
    return dispute


@router.post("/disputes/{dispute_id}/resolve")
def resolve(dispute_id: str):
    parsed = parse_dispute_id(dispute_id)
    if parsed is None:
        raise HTTPException(status_code=422, detail=f"bad dispute id {dispute_id}")
    try:
        return resolve_dispute(parsed)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown dispute {dispute_id}") from None
