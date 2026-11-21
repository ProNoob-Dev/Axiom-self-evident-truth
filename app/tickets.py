"""Dev slice: plan -> code read -> test write -> review diff (HITL).

All offline and deterministic: TF-IDF retrieval over data/codebase, no
LLM. Every step is audit-logged. No code change ships without a human
review decision (approve/reject) — see review_ticket.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import audit, config
from .pii import mask_pii
from .retrieval import Doc, get_retriever, tokenize

BASE_DIR = Path(__file__).resolve().parent.parent
TICKETS_PATH = BASE_DIR / "data" / "tickets.json"
CODEBASE_DIR = BASE_DIR / "data" / "codebase"

router = APIRouter()


class ReviewRequest(BaseModel):
    decision: str
    note: str | None = None


# -- store -------------------------------------------------------------
def load_tickets(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path is not None else TICKETS_PATH
    return json.loads(p.read_text(encoding="utf-8"))["tickets"]


def _save(tickets: list[dict], path: str | Path | None = None) -> None:
    p = Path(path) if path is not None else TICKETS_PATH
    p.write_text(json.dumps({"tickets": tickets}, indent=2), encoding="utf-8")


def _find(tickets: list[dict], ticket_id: str) -> dict | None:
    return next((t for t in tickets if t["id"] == ticket_id), None)


def _mask(ticket: dict) -> dict:
    out = dict(ticket)
    for field in ("title", "description"):
        if out.get(field):
            out[field] = mask_pii(out[field])
    for field in ("plan", "review_summary"):
        if isinstance(out.get(field), list):
            out[field] = [mask_pii(s) for s in out[field]]
        elif out.get(field):
            out[field] = mask_pii(out[field])
    return out


# -- codebase retrieval --------------------------------------------------
def _codebase_docs(codebase_dir: str | Path | None = None) -> list[Doc]:
    root = Path(codebase_dir) if codebase_dir is not None else CODEBASE_DIR
    docs = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".md"):
            docs.append(Doc(id=str(path.relative_to(root)),
                            text=path.read_text(encoding="utf-8")))
    return docs


def search_tickets(query: str, path: str | Path | None = None) -> dict | None:
    """Best TF-IDF ticket match for a question, or None."""
    tickets = load_tickets(path)
    retriever = get_retriever([
        Doc(id=t["id"], text=f"{t['title']} {t['description']} {t['repo_area']}")
        for t in tickets])
    hits = retriever.retrieve(query, k=1)
    if not hits:
        return None
    ticket = _find(tickets, hits[0].doc.id)
    return {"ticket": _mask(ticket), "score": round(hits[0].score, 3)}


def _code_read(ticket: dict, codebase_dir: str | Path | None = None) -> list[dict]:
    docs = _codebase_docs(codebase_dir)
    retriever = get_retriever(docs)
    query = f"{ticket['title']} {ticket['description']} {ticket['repo_area']}"
    terms = set(tokenize(query))
    hits = []
    by_id = {d.id: d for d in docs}
    for scored in retriever.retrieve(query, k=3):
        lines = by_id[scored.doc.id].text.splitlines()
        refs = [i + 1 for i, line in enumerate(lines)
                if terms & set(tokenize(line))][:5]
        excerpt = next((lines[i - 1].strip() for i in refs), "")
        hits.append({"file": scored.doc.id, "score": round(scored.score, 3),
                     "lines": refs, "excerpt": mask_pii(excerpt[:160])})
    return hits


# -- agents --------------------------------------------------------------
def _plan(ticket: dict, code_read: list[dict]) -> list[str]:
    top_file = code_read[0]["file"] if code_read else "the codebase"
    steps = [
        f"Reproduce the issue: {ticket['repo_area']} — {ticket['title'].lower()}.",
        f"Read {top_file} and the ADRs it references before changing anything.",
    ]
    text = f"{ticket['title']} {ticket['description']}".lower()
    if "refund" in text or "paise" in text:
        steps.append("Check the money path against ADR-001 (paise in, rupees out).")
    if "sql" in text:
        steps.append("Check the query parses under sqlglot before touching validation.")
    steps += [
        "Write a failing test that captures the expected behaviour.",
        "Implement the fix in the smallest change that satisfies the test.",
        "Run the existing test suite plus the new test.",
        "Submit the diff for human review — no change ships without approval.",
    ]
    return [mask_pii(s) for s in steps]


def _proposed_test(ticket: dict) -> str:
    if ticket["id"] == "TKT-101":
        return (
            "from billing import refund_summary\n\n\n"
            "def test_refund_summary_displays_rupees():\n"
            "    # 33456 paise is Rs 334.56, not Rs 33,456.00 (TKT-101).\n"
            "    assert refund_summary([10000, 23456]) == "
            '"Total refunds: Rs 334.56"\n')
    slug = re.sub(r"[^a-z0-9]+", "_", ticket["title"].lower()).strip("_")
    return (
        f"# Proposed test for {ticket['id']} ({ticket['repo_area']}).\n"
        f"# Captures the expected behaviour before the fix lands.\n\n\n"
        f"def test_{slug[:48]}():\n"
        f"    # TODO: assert the fixed behaviour for: {ticket['title']}\n"
        "    raise NotImplementedError('fill in from the plan steps above')\n")


def _review_diff(ticket: dict, proposed_test: str) -> str:
    if ticket["id"] == "TKT-101":
        return (
            "--- a/billing.py\n"
            "+++ b/billing.py\n"
            "@@ - refund_summary: format the paise total as rupees @@\n"
            "-    return f\"Total refunds: Rs {total:,.2f}\"\n"
            "+    return f\"Total refunds: {format_inr(total)}\"\n"
            "\n"
            "--- a/test_billing.py (new test)\n"
            "+++ b/test_billing.py\n"
            + "".join(f"+{line}\n" for line in proposed_test.splitlines()))
    return (
        f"# Diff proposal for {ticket['id']}: no source change drafted yet —\n"
        "# the plan and the proposed test below are the reviewable artefacts.\n"
        "# The source change is drafted by the reviewer from the plan steps.\n"
        "\n"
        "--- a/test file (proposed test)\n"
        "+++ b/test file\n"
        + "".join(f"+{line}\n" for line in proposed_test.splitlines()))


def _log(event: str, request_id: str, payload: dict,
         audit_path: str | Path | None) -> None:
    audit.append(event, request_id, payload, path=audit_path)


def work_ticket(ticket_id: str, path: str | Path | None = None,
                codebase_dir: str | Path | None = None,
                audit_path: str | Path | None = None) -> dict:
    """Run plan -> code read -> test write -> review on a ticket."""
    request_id = "tkt_" + uuid.uuid4().hex[:12]
    tickets = load_tickets(path)
    ticket = _find(tickets, ticket_id)
    if ticket is None:
        raise KeyError(ticket_id)
    code_read = _code_read(ticket, codebase_dir)
    plan = _plan(ticket, code_read)
    _log("ticket_planned", request_id, {"ticket_id": ticket_id, "plan": plan}, audit_path)
    _log("ticket_code_read", request_id, {"ticket_id": ticket_id, "files": code_read}, audit_path)
    proposed_test = _proposed_test(ticket)
    _log("ticket_test_written", request_id, {"ticket_id": ticket_id}, audit_path)
    diff = _review_diff(ticket, proposed_test)
    summary = (f"Review proposal for {ticket_id} ({ticket['title']}): "
               f"{len(plan)} plan steps, {len(code_read)} code files read, "
               "1 proposed test, diff attached. Awaiting human review.")
    _log("ticket_reviewed", request_id,
         {"ticket_id": ticket_id, "summary": summary}, audit_path)
    ticket.update({"status": "awaiting_review", "plan": plan,
                   "code_read": code_read, "proposed_test": proposed_test,
                   "diff": diff, "review_summary": summary})
    _save(tickets, path)
    _log("ticket_worked", request_id, {"ticket_id": ticket_id}, audit_path)
    return _mask(ticket)


def review_ticket(ticket_id: str, decision: str, note: str | None = None,
                  path: str | Path | None = None,
                  audit_path: str | Path | None = None) -> dict:
    """HITL decision on a worked ticket. Only from awaiting_review."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    request_id = "tkt_" + uuid.uuid4().hex[:12]
    tickets = load_tickets(path)
    ticket = _find(tickets, ticket_id)
    if ticket is None:
        raise KeyError(ticket_id)
    if ticket.get("status") != "awaiting_review":
        raise ValueError(f"ticket {ticket_id} is not awaiting review")
    ticket["status"] = "approved" if decision == "approve" else "rejected"
    if note:
        ticket["review_note"] = mask_pii(note)
    _save(tickets, path)
    _log("ticket_review_decision", request_id,
         {"ticket_id": ticket_id, "decision": decision,
          "note": mask_pii(note) if note else ""}, audit_path)
    return _mask(ticket)


def list_tickets(path: str | Path | None = None) -> list[dict]:
    return [_mask(t) for t in load_tickets(path)]


def get_ticket(ticket_id: str, path: str | Path | None = None) -> dict:
    ticket = _find(load_tickets(path), ticket_id)
    if ticket is None:
        raise KeyError(ticket_id)
    return _mask(ticket)


# -- router --------------------------------------------------------------
@router.post("/tickets/{ticket_id}/work")
def work(ticket_id: str):
    try:
        return work_ticket(ticket_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ticket {ticket_id}") from None


@router.get("/tickets")
def list_all():
    return {"tickets": list_tickets()}


@router.get("/tickets/{ticket_id}")
def get_one(ticket_id: str):
    try:
        return get_ticket(ticket_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ticket {ticket_id}") from None


@router.post("/tickets/{ticket_id}/review")
def review(ticket_id: str, req: ReviewRequest):
    try:
        return review_ticket(ticket_id, req.decision, req.note)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown ticket {ticket_id}") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
