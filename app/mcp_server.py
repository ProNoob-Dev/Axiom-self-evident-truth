"""Minimal MCP-compatible stdio tool server (stdlib only, one file).

Mirrors the protocol shape of the N28 MCP server (initialize, tools/list,
tools/call over JSON-RPC on stdio). Four tools:
- search_codebase(query, k=3): TF-IDF hits over data/codebase
- ask_analytics(question): the analytics ask flow (app/analytics via Axiom)
- resolve_dispute(dispute_id): precedent-based draft resolution
- work_ticket(ticket_id): run the dev agents on a ticket (HITL: leaves the
  ticket awaiting_review; nothing ships without a human decision)

Usage: python app/mcp_server.py < requests.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import disputes, tickets  # noqa: E402
from app.graph import Axiom  # noqa: E402
from app.retrieval import get_retriever  # noqa: E402

TOOLS = [
    {
        "name": "search_codebase",
        "description": "Search the sample codebase for files matching a query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ask_analytics",
        "description": "Answer a billing analytics question with validated SQL.",
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "resolve_dispute",
        "description": "Draft a resolution for a live dispute from precedent cases.",
        "inputSchema": {
            "type": "object",
            "properties": {"dispute_id": {"type": "integer"}},
            "required": ["dispute_id"],
        },
    },
    {
        "name": "work_ticket",
        "description": "Run the dev agents on a ticket: plan, code read, test, review diff.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
        },
    },
]


def _require_str(args: dict, field: str, tool: str) -> str:
    value = args.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{tool} requires a non-empty string '{field}'")
    return value


def main() -> None:
    axiom = Axiom()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {}) or {}

        def reply(result=None, error=None):
            msg = {"jsonrpc": "2.0", "id": req_id}
            if error is not None:
                msg["error"] = {"code": -32602, "message": error}
            else:
                msg["result"] = result
            sys.stdout.write(json.dumps(msg) + "\n")
            sys.stdout.flush()

        if method == "initialize":
            reply({"protocolVersion": "2024-11-05",
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "axiom", "version": "0.2.0"}})
        elif method == "tools/list":
            reply({"tools": TOOLS})
        elif method == "tools/call":
            try:
                name = params.get("name")
                args = params.get("arguments", {}) or {}
                if name == "search_codebase":
                    query = _require_str(args, "query", name)
                    try:
                        k = int(args.get("k", 3))
                    except (TypeError, ValueError):
                        raise ValueError("k must be an integer") from None
                    k = max(1, min(10, k))
                    # Reuse the tickets code-read retrieval over the codebase.
                    docs = tickets._codebase_docs()
                    retriever = get_retriever(docs)
                    hits = retriever.retrieve(query, k=k)
                    reply({"content": [{"type": "text", "text": json.dumps([
                        {"file": s.doc.id, "score": round(s.score, 3),
                         "excerpt": s.doc.text[:200]} for s in hits])}]})
                elif name == "ask_analytics":
                    question = _require_str(args, "question", name)
                    reply({"content": [{"type": "text",
                                        "text": json.dumps(axiom.ask(question))}]})
                elif name == "resolve_dispute":
                    if not isinstance(args.get("dispute_id"), int):
                        raise ValueError("resolve_dispute requires an integer 'dispute_id'")
                    try:
                        result = disputes.resolve_dispute(args["dispute_id"])
                    except KeyError:
                        raise ValueError(f"unknown dispute {args['dispute_id']}") from None
                    reply({"content": [{"type": "text", "text": json.dumps(result)}]})
                elif name == "work_ticket":
                    ticket_id = _require_str(args, "ticket_id", name)
                    try:
                        result = tickets.work_ticket(ticket_id)
                    except KeyError:
                        raise ValueError(f"unknown ticket {ticket_id}") from None
                    reply({"content": [{"type": "text", "text": json.dumps(result)}]})
                else:
                    reply(error=f"unknown tool: {name}")
            except Exception as exc:  # noqa: BLE001 - bad args must be a JSON-RPC error, not a crash
                reply(error=f"tools/call failed: {exc}")
        else:
            reply(error=f"unknown method: {method}")


if __name__ == "__main__":
    main()
