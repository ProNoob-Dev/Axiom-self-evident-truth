# Axiom

One enterprise agentic copilot platform: a shared LangGraph core with
confidence-routed intent, a governed semantic layer, and a SQL safety
harness — with three agent domains on top (developer assistant,
conversational analytics, disputes intelligence).

**Phase 2 ships the shared core + all three slices (analytics, tickets,
disputes) + the MCP server.** No UI yet — the API is the product surface
for now.

> Every answer ships with its proof: the SQL that ran, the sources it
> came from, and the audit trail behind it.

## Quickstart

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/seed.py
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8125
```

```bash
curl -X POST localhost:8125/ask -H 'Content-Type: application/json' \
  -d '{"question": "What were total refunds in Mumbai last month?"}'
```

## Endpoints

- `GET /health` · `POST /ask` · `GET /schema` · `GET /router` · `GET /metrics`
- `GET /tickets` · `GET /tickets/{id}` · `POST /tickets/{id}/work` · `POST /tickets/{id}/review`
- `GET /disputes` · `GET /disputes/{id}` · `POST /disputes/{id}/resolve`
- MCP server: `.venv/bin/python app/mcp_server.py` (stdio JSON-RPC; see `SPEC.md`)

## Evals

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/eval.py   # golden set -> eval/last_run.json
```

Measured on the golden set (this build): router 12/12, analytics 6/6,
SQL validation 4/4, tickets 4/4, disputes 4/4, false-positive rate 0.0. See `SPEC.md` — these are
golden-set measurements, not production outcomes.

## Config

See `.env.example`. Azure OpenAI is optional: without a key the analytics
slice runs fully offline on deterministic SQL templates; with a key, the
LLM generates SQL through the same validation. No key is ever committed.

## Stack

Python 3.12, FastAPI, LangGraph, sqlglot, SQLite (+ pgvector when
`DATABASE_URL` is set). LangGraph only — no LangChain anywhere in the code.
