"""FastAPI app: endpoints and wiring.

Phase 2: tickets + disputes routers wired below; /ask routes dev/disputes
questions to those domains (see app/graph.py domain_route).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import audit, config
from .disputes import router as disputes_router
from .graph import Axiom
from .llm import AzureError
from .semantic import SemanticLayer
from .tickets import router as tickets_router

BASE_DIR = Path(__file__).resolve().parent.parent


class AskRequest(BaseModel):
    question: str
    session_id: str | None = None


def _read_eval_last_run() -> dict | None:
    path = BASE_DIR / "eval" / "last_run.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def _audit_stats() -> dict:
    records = audit.read_all()
    by_event = Counter(r["event"] for r in records)
    bands = Counter(
        r["payload"].get("band") for r in records
        if r["event"] == "routing_decision" and r["payload"].get("band"))
    domains = Counter(
        r["payload"].get("domain") for r in records
        if r["event"] == "routing_decision" and r["payload"].get("domain"))
    return {"total_questions": by_event.get("question_received", 0),
            "answered": by_event.get("answered", 0),
            "routed": by_event.get("routed", 0),
            "confidence_bands": dict(bands),
            "domains": dict(domains)}


def create_app(axiom: Axiom | None = None) -> FastAPI:
    axiom = axiom or Axiom()
    semantic = axiom.semantic
    app = FastAPI(title="Axiom", version="0.1.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "llm_provider": config.llm_provider(),
                "retrieval": config.retrieval_backend()}

    @app.post("/ask")
    def ask(req: AskRequest):
        if not req.question.strip():
            raise HTTPException(status_code=422, detail="question must not be empty")
        try:
            return axiom.ask(req.question, req.session_id)
        except AzureError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/schema")
    def schema():
        return semantic.to_dict()

    @app.get("/router")
    def router():
        stats = _audit_stats()
        last_run = _read_eval_last_run()
        golden = (last_run or {}).get("golden", {})
        return {
            "confidence_distribution": stats["confidence_bands"],
            "domains": stats["domains"],
            # False-positive rate is measured on the golden set only;
            # live traffic has no labels, so nothing is invented here.
            "golden_router_accuracy": golden.get("router_accuracy"),
            "golden_false_positive_rate": golden.get("false_positive_rate"),
            "golden_runs": golden.get("total"),
        }

    @app.get("/metrics")
    def metrics():
        stats = _audit_stats()
        last_run = _read_eval_last_run()
        golden = (last_run or {}).get("golden", {})
        total = stats["total_questions"]
        return {
            "total_questions": total,
            "answered": stats["answered"],
            "routed_elsewhere": stats["routed"],
            "autonomy_rate": (stats["answered"] / total) if total else None,
            "routing_accuracy_golden": golden.get("router_accuracy"),
            "analytics_correct_golden": golden.get("analytics_correct"),
            "sql_validation_correct_golden": golden.get("sql_validation_correct"),
        }

    # Phase 2 routers: tickets + disputes slices.
    app.include_router(tickets_router)
    app.include_router(disputes_router)

    return app


app = create_app()
