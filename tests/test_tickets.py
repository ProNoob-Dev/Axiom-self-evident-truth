"""Tickets slice: work flow, HITL approve/reject, 404s, PII masking."""

import json
import shutil
from pathlib import Path

import pytest

from app import tickets


@pytest.fixture()
def tickets_path(tmp_path):
    path = tmp_path / "tickets.json"
    shutil.copy(Path(tickets.__file__).resolve().parent.parent / "data" / "tickets.json", path)
    return str(path)


@pytest.fixture()
def audit_path(tmp_path):
    return str(tmp_path / "audit.log")


def test_work_flow_produces_plan_diff_and_awaiting_review(tickets_path, audit_path):
    worked = tickets.work_ticket("TKT-101", path=tickets_path, audit_path=audit_path)
    assert worked["status"] == "awaiting_review"
    assert len(worked["plan"]) >= 4
    assert worked["code_read"] and worked["code_read"][0]["file"] == "billing.py"
    assert worked["code_read"][0]["lines"], "code read must cite line numbers"
    assert "test_refund_summary_displays_rupees" in worked["proposed_test"]
    assert "format_inr(total)" in worked["diff"]
    assert "awaiting" in worked["review_summary"].lower()
    # Persisted: a fresh read sees the worked state.
    assert tickets.get_ticket("TKT-101", path=tickets_path)["status"] == "awaiting_review"
    # Audit log got one record per step.
    events = [json.loads(line)["event"] for line in Path(audit_path).read_text().splitlines()]
    for event in ("ticket_planned", "ticket_code_read", "ticket_test_written",
                  "ticket_reviewed", "ticket_worked"):
        assert event in events


def test_work_unknown_ticket_raises(tickets_path, audit_path):
    with pytest.raises(KeyError):
        tickets.work_ticket("TKT-999", path=tickets_path, audit_path=audit_path)


def test_review_approve(tickets_path, audit_path):
    tickets.work_ticket("TKT-102", path=tickets_path, audit_path=audit_path)
    reviewed = tickets.review_ticket("TKT-102", "approve", path=tickets_path, audit_path=audit_path)
    assert reviewed["status"] == "approved"


def test_review_reject_with_note(tickets_path, audit_path):
    tickets.work_ticket("TKT-103", path=tickets_path, audit_path=audit_path)
    reviewed = tickets.review_ticket("TKT-103", "reject", "Too broad — narrow the scope.",
                                     path=tickets_path, audit_path=audit_path)
    assert reviewed["status"] == "rejected"
    assert reviewed["review_note"] == "Too broad — narrow the scope."


def test_review_invalid_decision(tickets_path, audit_path):
    tickets.work_ticket("TKT-104", path=tickets_path, audit_path=audit_path)
    with pytest.raises(ValueError):
        tickets.review_ticket("TKT-104", "maybe", path=tickets_path, audit_path=audit_path)


def test_review_requires_awaiting_review(tickets_path, audit_path):
    with pytest.raises(ValueError):
        tickets.review_ticket("TKT-105", "approve", path=tickets_path, audit_path=audit_path)


def test_review_unknown_ticket_raises(tickets_path, audit_path):
    with pytest.raises(KeyError):
        tickets.review_ticket("TKT-999", "approve", path=tickets_path, audit_path=audit_path)


def test_pii_masked_on_output(tickets_path):
    ticket = tickets.get_ticket("TKT-101", path=tickets_path)
    assert "priya.sharma@example.com" not in ticket["description"]
    assert "[EMAIL]" in ticket["description"]
    ticket = tickets.get_ticket("TKT-104", path=tickets_path)
    assert "98220" not in ticket["description"]
    assert "[PHONE]" in ticket["description"]


def test_search_tickets_matches_refund_bug(tickets_path):
    matched = tickets.search_tickets("Fix the bug in the refund handler", path=tickets_path)
    assert matched and matched["ticket"]["id"] == "TKT-101"
    assert matched["score"] > 0
