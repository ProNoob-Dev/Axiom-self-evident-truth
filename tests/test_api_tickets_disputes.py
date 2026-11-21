"""End-to-end API for the new endpoints + /ask routing (dev/disputes)."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import disputes, tickets
from app.graph import Axiom
from app.main import create_app


@pytest.fixture()
def api_client(db_path, tmp_path, monkeypatch):
    # Point the routers at temp state so API tests never mutate the
    # shipped tickets file or the real billing DB.
    tickets_copy = tmp_path / "tickets.json"
    shutil.copy(Path(tickets.__file__).resolve().parent.parent / "data" / "tickets.json",
                tickets_copy)
    monkeypatch.setattr(tickets, "TICKETS_PATH", tickets_copy)
    monkeypatch.setattr(disputes, "DB_PATH", db_path)
    axiom = Axiom(db_path=db_path, audit_path=str(tmp_path / "audit.log"),
                  tickets_path=str(tickets_copy))
    return TestClient(create_app(axiom))


def test_ticket_work_then_approve(api_client):
    worked = api_client.post("/tickets/TKT-101/work")
    assert worked.status_code == 200
    assert worked.json()["status"] == "awaiting_review"
    assert worked.json()["diff"]
    approved = api_client.post("/tickets/TKT-101/review",
                               json={"decision": "approve"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_ticket_review_reject(api_client):
    api_client.post("/tickets/TKT-102/work")
    rejected = api_client.post("/tickets/TKT-102/review",
                               json={"decision": "reject", "note": "Not yet."})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_ticket_review_invalid_decision_is_422(api_client):
    api_client.post("/tickets/TKT-103/work")
    resp = api_client.post("/tickets/TKT-103/review", json={"decision": "maybe"})
    assert resp.status_code == 422


def test_ticket_unknown_is_404(api_client):
    assert api_client.post("/tickets/TKT-999/work").status_code == 404
    assert api_client.get("/tickets/TKT-999").status_code == 404
    assert api_client.post("/tickets/TKT-999/review",
                           json={"decision": "approve"}).status_code == 404


def test_list_tickets_masks_pii(api_client):
    resp = api_client.get("/tickets")
    assert resp.status_code == 200
    assert len(resp.json()["tickets"]) == 6
    assert "priya.sharma@example.com" not in resp.text
    assert "[EMAIL]" in resp.text


def test_dispute_resolve_with_citations(api_client):
    resp = api_client.post("/disputes/1/resolve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason_cluster"] == "wrong amount"
    assert body["citations"]
    assert body["human_review"] is False
    assert "divya.menon@example.com" not in resp.text


def test_dispute_no_precedent_routes_to_human(api_client):
    body = api_client.post("/disputes/2/resolve").json()
    assert body["human_review"] is True
    assert body["precedents"] == []


def test_dispute_list_and_clusters(api_client):
    body = api_client.get("/disputes").json()
    assert len(body["disputes"]) == 10
    assert body["reason_clusters"]["refund delay"] == 6


def test_dispute_unknown_is_404(api_client):
    assert api_client.post("/disputes/999/resolve").status_code == 404
    assert api_client.get("/disputes/999").status_code == 404


def test_ask_dev_question_routes_to_ticket(api_client):
    body = api_client.post("/ask", json={
        "question": "Fix the bug in the refund handler"}).json()
    assert body["route"] == "dev"
    assert body["matched_ticket"]["id"] == "TKT-101"
    assert body["action"] == "POST /tickets/TKT-101/work"
    assert body["summary"]


def test_ask_disputes_question_routes_to_dispute(api_client):
    body = api_client.post("/ask", json={
        "question": "Draft a resolution for dispute DSP-7"}).json()
    assert body["route"] == "disputes"
    assert body["matched_dispute"]["id"] == 7
    assert body["action"] == "POST /disputes/7/resolve"
    assert body["summary"]


def test_ask_analytics_no_regression(api_client):
    body = api_client.post("/ask", json={
        "question": "What were total refunds in Mumbai last month?"}).json()
    assert body["route"] == "answered"
    assert body["sql"] and "refunds" in body["sql"].lower()
