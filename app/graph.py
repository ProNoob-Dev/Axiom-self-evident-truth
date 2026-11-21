"""LangGraph orchestrator for the ask flow (LangGraph only, never LangChain).

Nodes: intake -> classify -> retrieve_schema -> generate_sql -> validate_sql
-> execute -> validate_result -> respond. Low-confidence classify routes to
a clarify node; validation failure regenerates once, then escalates.
"""

from __future__ import annotations

import json
import uuid
from typing import TypedDict

from langgraph.graph import END, StateGraph

from . import audit, config
from .analytics import (
    build_sql, detect_count_query, extract_entities, format_answer,
    parse_dates, validate_result,
)
from .classifier import Classifier
from .disputes import match_dispute
from .llm import AzureError, get_client
from .pii import mask_pii
from .retrieval import Doc, get_retriever
from .semantic import SemanticLayer
from .sqlsafety import enforce_limit, execute_readonly, validate
from .tickets import search_tickets


class GraphState(TypedDict, total=False):
    request_id: str
    session_id: str | None
    question: str
    entities: dict
    date_range: tuple[str, str] | None
    classification: dict
    metric: str | None
    ambiguous: list[str] | None
    schema_hits: list[dict]
    past_queries: list[dict]
    sql: str | None
    sql_valid: bool
    sql_errors: list[str]
    attempts: int
    columns: list[str]
    rows: list[tuple]
    result_validation: dict
    route: str  # answered | clarified | needs_verification | escalated | dev | disputes
    answer: str | None
    message: str | None
    matched_ticket: dict | None
    matched_dispute: dict | None
    action: str | None
    summary: str | None
    confidence: float
    band: str


class Axiom:
    def __init__(self, db_path: str | None = None, audit_path=None,
                 semantic: SemanticLayer | None = None,
                 tickets_path: str | None = None) -> None:
        self.db_path = db_path or config.DB_PATH
        self.audit_path = audit_path
        self.tickets_path = tickets_path or str(config.DATA_DIR / "tickets.json")
        self.semantic = semantic or SemanticLayer()
        self.classifier = Classifier()
        self._past_queries = json.loads(
            (config.DATA_DIR / "past_queries.json").read_text(encoding="utf-8"))
        self.retriever = get_retriever([
            Doc(id=f"pq-{i}", text=f"{pq['question']} {pq['sql']}",
                meta={"question": pq["question"], "sql": pq["sql"]})
            for i, pq in enumerate(self._past_queries)])
        self._graph = self._build_graph()

    def _log(self, event: str, request_id: str, payload: dict | None = None) -> None:
        audit.append(event, request_id, payload, path=self.audit_path)

    # -- graph ---------------------------------------------------------
    def _build_graph(self):
        def intake(state: GraphState) -> GraphState:
            question = mask_pii(state["question"])
            return {**state, "question": question,
                    "entities": extract_entities(question, self.db_path),
                    "date_range": parse_dates(question),
                    "attempts": 0}

        def classify(state: GraphState) -> GraphState:
            decision = self.classifier.classify(state["question"])
            self._log("routing_decision", state["request_id"], decision)
            return {**state, "classification": decision,
                    "confidence": decision["confidence"], "band": decision["band"]}

        def after_classify(state: GraphState) -> str:
            decision = state["classification"]
            if decision["band"] == "low":
                return "clarify"
            if decision["band"] == "medium":
                return "verify"
            if decision["domain"] != "analytics":
                return "domain_route"
            return "retrieve_schema"

        def verify(state: GraphState) -> GraphState:
            # Medium confidence needs LLM verification. Offline there is no
            # LLM, so we do NOT route silently — we ask for confirmation.
            if config.llm_provider() != "azure":
                return {**state, "route": "needs_verification",
                        "message": (
                            f"This looks like a {state['classification']['domain']} "
                            f"question (confidence {state['confidence']:.2f}), but I am "
                            "not sure enough to route it without verification. "
                            "Could you confirm the domain, or rephrase?")}
            try:
                ok = get_client().verify_routing(
                    state["question"], state["classification"]["domain"],
                    state["confidence"])
            except AzureError:
                raise
            if ok:
                decision = {**state["classification"], "band": "high",
                            "needs_verification": False}
                self._log("routing_verified", state["request_id"], decision)
                return {**state, "classification": decision,
                        "band": "high", "route": ""}
            return {**state, "route": "needs_verification",
                    "message": ("The routing check did not confirm this as a "
                                f"{state['classification']['domain']} question. "
                                "Could you rephrase, or say which area it is about?")}

        def after_verify(state: GraphState) -> str:
            if state.get("route") == "needs_verification":
                return "respond"
            if state["classification"]["domain"] != "analytics":
                return "domain_route"
            return "retrieve_schema"

        def clarify(state: GraphState) -> GraphState:
            return {**state, "route": "clarified",
                    "message": state["classification"].get("clarification")}

        def domain_route(state: GraphState) -> GraphState:
            # /ask routes and points at the dedicated endpoint; it does NOT
            # run the ticket/dispute work itself — that is what the
            # endpoint is for. Honest routing, not a pretend run.
            domain = state["classification"]["domain"]
            if domain == "dev":
                matched = search_tickets(state["question"], path=self.tickets_path)
                if matched:
                    ticket = matched["ticket"]
                    action = f"POST /tickets/{ticket['id']}/work"
                    summary = (f"This looks like dev work matching ticket "
                               f"{ticket['id']} ({ticket['title']}, "
                               f"match score {matched['score']}). Run {action} "
                               "to work it — plan, code read, test, review diff.")
                else:
                    action = None
                    summary = ("This looks like dev work, but no ticket matches "
                               "it. List tickets with GET /tickets first.")
                return {**state, "route": "dev", "matched_ticket": matched["ticket"] if matched else None,
                        "matched_dispute": None, "action": action,
                        "summary": summary, "message": summary}
            matched = match_dispute(state["question"], self.db_path)
            if matched:
                action = f"POST /disputes/{matched['id']}/resolve"
                summary = (f"This looks like a dispute about "
                           f"{matched['reason']} (dispute {matched['id']}, "
                           f"{matched['customer']['name']}). Run {action} to "
                           "draft a resolution from precedent.")
            else:
                action = None
                summary = ("This looks like a dispute question, but no live "
                           "dispute matches it. List disputes with GET /disputes first.")
            return {**state, "route": "disputes", "matched_ticket": None,
                    "matched_dispute": matched, "action": action,
                    "summary": summary, "message": summary}

        def retrieve_schema(state: GraphState) -> GraphState:
            hits = self.semantic.search_schema(state["question"])
            past = [{"question": s.doc.meta["question"], "sql": s.doc.meta["sql"],
                     "score": round(s.score, 3)}
                    for s in self.retriever.retrieve(state["question"], k=3)]
            return {**state, "schema_hits": hits, "past_queries": past}

        def generate_sql(state: GraphState) -> GraphState:
            resolved = self.semantic.resolve_metric(state["question"])
            metric = resolved["metric"]
            if resolved["ambiguous"]:
                return {**state, "metric": None,
                        "ambiguous": resolved["ambiguous"],
                        "route": "clarified",
                        "message": (
                            "That question is ambiguous: it could mean "
                            + ", ".join(resolved["ambiguous"]) +
                            ". Which one did you mean?")}
            if metric is None:
                metric = detect_count_query(state["question"])
            if metric is None:
                return {**state, "metric": None, "route": "clarified",
                        "message": ("I could not identify a billing metric in that "
                                    "question. Try one of: revenue, refunds, refund "
                                    "rate, dispute rate, average invoice value.")}
            if config.llm_provider() == "azure":
                context = json.dumps(self.semantic.to_dict())
                sql = get_client().generate_sql(state["question"], context)
            else:
                sql = build_sql(metric, state["entities"], state["date_range"])
            if sql is None:
                return {**state, "metric": metric, "route": "escalated",
                        "message": (f"No SQL template covers the {metric} metric "
                                    "with those filters yet.")}
            return {**state, "metric": metric, "sql": sql}

        def after_generate(state: GraphState) -> str:
            return "respond" if state.get("route") in ("clarified", "escalated") else "validate_sql"

        def validate_sql(state: GraphState) -> GraphState:
            ok, errors = validate(state["sql"] or "")
            self._log("sql_validated", state["request_id"],
                      {"sql": state["sql"], "ok": ok, "errors": errors})
            return {**state, "sql_valid": ok, "sql_errors": errors}

        def after_validate(state: GraphState) -> str:
            if state["sql_valid"]:
                return "execute"
            if state.get("attempts", 0) < 1:
                return "regenerate"
            return "escalate"

        def regenerate(state: GraphState) -> GraphState:
            # One retry: the offline templates are deterministic, so a
            # failure here is a template bug — retrying once is the honest
            # bound before escalating. The LLM path gets a fresh generation.
            return {**state, "attempts": state.get("attempts", 0) + 1,
                    "sql": None}

        def escalate(state: GraphState) -> GraphState:
            return {**state, "route": "escalated",
                    "message": ("The generated SQL failed validation: "
                                + "; ".join(state.get("sql_errors", [])) +
                                ". I did not run it.")}

        def execute(state: GraphState) -> GraphState:
            sql = enforce_limit(state["sql"])
            columns, rows = execute_readonly(sql, self.db_path)
            self._log("sql_executed", state["request_id"],
                      {"sql": sql, "rows": len(rows)})
            return {**state, "sql": sql, "columns": columns, "rows": rows}

        def validate_result_node(state: GraphState) -> GraphState:
            checks = validate_result(state["columns"], state["rows"])
            self._log("result_validated", state["request_id"], checks)
            return {**state, "result_validation": checks}

        def respond(state: GraphState) -> GraphState:
            if state.get("route") in ("clarified", "needs_verification",
                                      "escalated", "dev", "disputes"):
                return {**state, "answer": state.get("message"), "confidence": state.get("confidence", 0.0)}
            answer = format_answer(state["metric"], state["columns"], state["rows"],
                                   state["entities"], state["date_range"])
            if state["result_validation"].get("empty_result"):
                answer += " (No matching rows — check the city or date range.)"
            return {**state, "route": "answered", "answer": answer}

        graph = StateGraph(GraphState)
        for name, fn in [
                ("intake", intake), ("classify", classify), ("verify", verify),
                ("clarify", clarify), ("domain_route", domain_route),
                ("retrieve_schema", retrieve_schema), ("generate_sql", generate_sql),
                ("validate_sql", validate_sql), ("regenerate", regenerate),
                ("escalate", escalate), ("execute", execute),
                ("validate_result", validate_result_node), ("respond", respond)]:
            graph.add_node(name, fn)
        graph.set_entry_point("intake")
        graph.add_edge("intake", "classify")
        graph.add_conditional_edges("classify", after_classify,
                                    {"clarify": "clarify", "verify": "verify",
                                     "domain_route": "domain_route",
                                     "retrieve_schema": "retrieve_schema"})
        graph.add_conditional_edges("verify", after_verify,
                                    {"respond": "respond", "domain_route": "domain_route",
                                     "retrieve_schema": "retrieve_schema"})
        graph.add_edge("clarify", "respond")
        graph.add_edge("domain_route", "respond")
        graph.add_edge("retrieve_schema", "generate_sql")
        graph.add_conditional_edges("generate_sql", after_generate,
                                    {"respond": "respond", "validate_sql": "validate_sql"})
        graph.add_conditional_edges("validate_sql", after_validate,
                                    {"execute": "execute", "regenerate": "regenerate",
                                     "escalate": "escalate"})
        graph.add_edge("regenerate", "generate_sql")
        graph.add_edge("escalate", "respond")
        graph.add_edge("execute", "validate_result")
        graph.add_edge("validate_result", "respond")
        graph.add_edge("respond", END)
        return graph.compile()

    # -- public API ------------------------------------------------------
    def ask(self, question: str, session_id: str | None = None) -> dict:
        request_id = "req_" + uuid.uuid4().hex[:12]
        self._log("question_received", request_id,
                  {"session_id": session_id or "",
                   "question": mask_pii(question)})
        try:
            state = self._graph.invoke(
                {"question": question, "request_id": request_id,
                 "session_id": session_id})
        except AzureError:
            self._log("escalated", request_id, {"reason": "llm_unavailable"})
            raise
        route = state.get("route", "answered")
        self._log("answered" if route == "answered" else "routed",
                  request_id, {"route": route, "confidence": state.get("confidence", 0.0),
                               "metric": state.get("metric"), "sql": state.get("sql")})
        return {
            "request_id": request_id,
            "route": route,
            "answer": state.get("answer"),
            "message": state.get("message"),
            "matched_ticket": state.get("matched_ticket"),
            "matched_dispute": state.get("matched_dispute"),
            "action": state.get("action"),
            "summary": state.get("summary"),
            "sql": state.get("sql"),
            "sources": {
                "schema": state.get("schema_hits", []),
                "past_queries": state.get("past_queries", []),
            },
            "confidence": state.get("confidence", 0.0),
            "band": state.get("band", "low"),
            "metric": state.get("metric"),
            "validation": state.get("result_validation"),
            "rows": [dict(zip(state.get("columns", []), row))
                     for row in state.get("rows", [])] if route == "answered" else [],
        }
