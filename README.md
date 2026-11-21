# Axiom

[![CI](https://github.com/ProNoob-Dev/Axiom/actions/workflows/ci.yml/badge.svg)](https://github.com/ProNoob-Dev/Axiom/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)

> **This is an invented demo / reference project. It is NOT real client work.**
> There is no real client, no real engineering team, and no real billing or
> disputes data. All tickets, invoices, customers, and disputes are synthetic,
> written for this demo. The figures sometimes quoted for this project idea
> (88% autonomous, under 2% false-positive routing, 40% faster first response)
> are invented claims, **not** measured results — this repo reports only
> measured numbers from its own golden eval set (see [Eval](#eval)).

One enterprise copilot platform — a shared LangGraph core with three agent
domains: developer assistant, text-to-SQL copilot, disputes intelligence —
every answer ships with its proof.

Three GenAI pilots that never shipped, replaced by one platform teams can
trust: a developer assistant grounded in the team's own codebase, a
text-to-SQL copilot that only runs validated queries, and a disputes agent
that resolves from precedent — all on one LangGraph core with confidence
routing, guardrails, evals, and a human in the loop.

> Every answer ships with its proof: the SQL that ran, the sources it came
> from, the precedents it cites, and the audit trail behind it.

## What it does

| Endpoint | Behaviour |
|---|---|
| `POST /ask` | Plain-English question → validated answer with the SQL and sources shown; dev/disputes questions route to their domain with the next action attached; low-confidence questions get a clarification, never a guess |
| `POST /tickets/{id}/work` | Dev agents work a ticket end to end: plan → code read → proposed test → review diff, left `awaiting_review` |
| `POST /tickets/{id}/review` | HITL approve / reject of a worked ticket (only from `awaiting_review`) — no code change ships without a human decision |
| `GET /tickets` · `GET /tickets/{id}` | Ticket queue and single ticket (PII-masked) |
| `POST /disputes/{id}/resolve` | Draft resolution from precedent cases, with citations; no precedent above threshold → honest `human_review: true`, no resolution drafted |
| `GET /disputes` · `GET /disputes/{id}` | Live disputes with reason clusters (PII-masked) |
| `GET /schema` | Semantic layer: canonical metrics, synonyms, tables |
| `GET /router` | Confidence distribution, domains, golden-set accuracy and false-positive rate (measured only — live traffic has no labels) |
| `GET /metrics` | Measured counts: answered, routed, autonomy rate, golden-set scores |
| `GET /health` | `{"status": "ok", "llm_provider": "offline", "retrieval": "tfidf"}` |

## Quickstart

```bash
git clone https://github.com/ProNoob-Dev/Axiom.git
cd Axiom
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/seed.py   # builds data/billing.sqlite (idempotent)
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8125
```

```bash
# Analytics: ask a billing question, get the answer with its SQL
curl -X POST localhost:8125/ask -H 'Content-Type: application/json' \
  -d '{"question": "What were total refunds in Mumbai last month?"}'

# Dev: work a ticket, then approve the review proposal
curl -X POST localhost:8125/tickets/TKT-101/work
curl -X POST localhost:8125/tickets/TKT-101/review \
  -H 'Content-Type: application/json' -d '{"decision": "approve"}'

# Disputes: draft a resolution from precedent
curl -X POST localhost:8125/disputes/1/resolve
```

No Azure key, Postgres, or Docker is needed to run or test. The default is
fully offline: deterministic SQL templates over a stdlib TF-IDF retriever
and a local SQLite billing database.

## Environment variables

| Var | Required? | Meaning |
|---|---|---|
| `AZURE_OPENAI_API_KEY` | No | Unset ⇒ offline mode (default). Set ⇒ Azure OpenAI mode |
| `AZURE_OPENAI_ENDPOINT` | Only in Azure mode | e.g. `https://<resource>.openai.azure.com/` |
| `AZURE_OPENAI_DEPLOYMENT` | Only in Azure mode | e.g. `gpt-4o` |
| `DATABASE_URL` | No | Unset ⇒ in-memory TF-IDF. Set ⇒ Postgres + pgvector |
| `BILLING_DB_PATH` | No | Path to the billing SQLite DB (`data/billing.sqlite`) |
| `AUDIT_LOG_PATH` | No | Audit JSONL path (`data/audit.log`) |
| `ROW_LIMIT` | No | Max rows a query may return (`100`) |
| `LOG_LEVEL` | No | `INFO` |

Never commit a real `.env` file. Copy `.env.example` if you need one locally.

In Azure mode, if the Azure call fails at runtime the request fails loudly
with HTTP 502 — it never silently falls back to a canned answer mid-request.
Tests never make a real Azure call.

## Real outputs, step by step

Every transcript below is real output from a live sweep of the API on
21 Nov 2026 (recorded in `eval/final_check.json`), not a mock-up.

### 1. Ask a billing question — answer, SQL, sources, validation

```bash
curl -X POST localhost:8125/ask -H 'Content-Type: application/json' \
  -d '{"question": "What were total refunds in Mumbai last month?"}'
```

```json
{
  "route": "answered",
  "answer": "Refunds in Mumbai from 2026-10-01 to 2026-10-31: ₹429.",
  "sql": "SELECT COALESCE(SUM(r.amount_paise), 0) AS refunds FROM refunds AS r JOIN invoices AS i ON r.invoice_id = i.id JOIN customers AS c ON i.customer_id = c.id WHERE c.city = 'Mumbai' AND r.refunded_on >= '2026-10-01' AND r.refunded_on <= '2026-10-31' LIMIT 100",
  "rows": [{"refunds": 42916}],
  "confidence": 0.99,
  "band": "high",
  "metric": "refunds",
  "validation": {"empty_result": false, "outliers": [], "schema_conformant": true},
  "sources": {"schema": [{"table": "refunds", "description": "Refunds issued against invoices. Amounts in paise.", "columns": ["id", "invoice_id", "amount_paise", "reason", "refunded_on"], "score": 1}]}
}
```

The answer, the exact SQL that ran, the row it returned, the validation
checks, and the schema source it grounded on — all in one response.

### 2. Ask something out of scope — a clarification, never a guess

```bash
curl -X POST localhost:8125/ask -H 'Content-Type: application/json' \
  -d '{"question": "Tell me a joke"}'
```

```json
{
  "route": "clarified",
  "band": "low",
  "answer": "I can help with billing analytics, dev tickets, or charge disputes. Could you say which one this is about — for example, 'total refunds in Mumbai last month'?"
}
```

### 3. Ask a disputes question — routed to the domain, with the next action

```bash
curl -X POST localhost:8125/ask -H 'Content-Type: application/json' \
  -d '{"question": "Draft a resolution for dispute DSP-7"}'
```

```json
{
  "route": "disputes",
  "answer": "This looks like a dispute about subscription cancelled after payment (dispute 7, Kavya Singh). Run POST /disputes/7/resolve to draft a resolution from precedent."
}
```

`/ask` does not pretend to have done the work — it routes, and the
dedicated endpoint does it.

### 4. Work a ticket — plan, code read, test, diff, human review

`POST /tickets/TKT-101/work` (ticket: "Refund totals displayed 100x too
large on dashboard"):

```json
{
  "id": "TKT-101",
  "status": "awaiting_review",
  "plan": [
    "Reproduce the issue: billing — refund totals displayed 100x too large on dashboard.",
    "Read billing.py and the ADRs it references before changing anything.",
    "Check the money path against ADR-001 (paise in, rupees out).",
    "Write a failing test that captures the expected behaviour.",
    "Implement the fix in the smallest change that satisfies the test.",
    "Run the existing test suite plus the new test.",
    "Submit the diff for human review — no change ships without approval."
  ],
  "code_read": [
    {"file": "billing.py", "score": 0.284, "lines": [1, 3, 4, 10, 11]},
    {"file": "test_billing.py", "score": 0.238, "lines": [1, 3, 7, 10, 11]},
    {"file": "docs/adr-001.md", "score": 0.174, "lines": [1, 5, 6, 10, 11]}
  ],
  "review_summary": "Review proposal for TKT-101 (Refund totals displayed 100x too large on dashboard): 7 plan steps, 3 code files read, 1 proposed test, diff attached. Awaiting human review."
}
```

The review diff it attaches:

```diff
--- a/billing.py
+++ b/billing.py
@@ - refund_summary: format the paise total as rupees @@
-    return f"Total refunds: Rs {total:,.2f}"
+    return f"Total refunds: {format_inr(total)}"

--- a/test_billing.py (new test)
+++ b/test_billing.py
+def test_refund_summary_displays_rupees():
+    # 33456 paise is Rs 334.56, not Rs 33,456.00 (TKT-101).
+    assert refund_summary([10000, 23456]) == "Total refunds: Rs 334.56"
```

`POST /tickets/TKT-101/review {"decision": "approve"}` then returns the
ticket with `"status": "approved"`. Nothing ships without that decision.

### 5. Resolve a dispute — precedent citations, draft resolution

`POST /disputes/1/resolve` (dispute 1: "wrong amount", invoice ₹22,243,
customer Divya Menon — email/phone masked as `[EMAIL]` / `[PHONE]`):

```json
{
  "reason_cluster": "wrong amount",
  "citations": ["DSP-P08", "DSP-P07", "DSP-P09"],
  "precedents": [
    {"id": "DSP-P08", "reason": "wrong amount", "resolution": "Charged the annual price instead of monthly; corrected the invoice and refunded the difference.", "score": 0.339},
    {"id": "DSP-P07", "reason": "wrong amount", "resolution": "Invoice corrected to the contracted price and the overcharge refunded; billing run re-checked for the same error.", "score": 0.31},
    {"id": "DSP-P09", "reason": "wrong amount", "resolution": "Usage tier miscalculated; invoice re-issued at the correct tier and the excess refunded with an apology credit.", "score": 0.281}
  ],
  "draft_resolution": "Draft resolution for dispute 1 (wrong amount, ₹22,243): following precedent DSP-P08, DSP-P07, DSP-P09 — Charged the annual price instead of monthly; corrected the invoice and refunded the difference. Apply the same remedy, confirm the credit with the customer, and record the outcome against the precedent.",
  "human_review": false
}
```

A dispute with no precedent above the match threshold (e.g. the
unauthorised-transaction dispute) returns `human_review: true` and no
drafted resolution — it is routed to a human, not answered from nothing.

### 6. Health

```bash
curl localhost:8125/health
```

```json
{"status": "ok", "llm_provider": "offline", "retrieval": "tfidf"}
```

## Eval

Deterministic scorer over a 30-case golden set (`data/golden_eval.json`):
12 router, 6 analytics, 4 SQL-validation, 4 tickets, 4 disputes cases.
Run in-process with no server and no network:

```bash
.venv/bin/python scripts/eval.py   # writes eval/last_run.json, exits non-zero on a gate breach
```

Measured on this repo (21 Nov 2026, offline mode):

| Metric | Measured | Gate |
|---|---|---|
| router accuracy | 1.00 (12/12) | ≥ 0.80 |
| false-positive rate | 0.00 (0 false positives) | reported only |
| analytics correct | 6/6 | ≥ 5/6 |
| SQL validation correct | 4/4 | all correct |
| tickets correct | 4/4 | all correct |
| disputes correct | 4/4 | all correct |

CI runs the same eval as a gate (`.github/workflows/ci.yml`): a breach
fails the build. These are golden-set measurements on synthetic data, not
production outcomes — there is no baseline for "88% autonomous" or "40%
faster" in this repo, so those figures are not measured and not claimed here.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q   # offline; no Azure, Postgres, or Docker
```

67 tests, all passing (21 Nov 2026). Every module has a test file; every
endpoint and every routing class is exercised.

## Architecture

One shared core, three thin domains on top:

```
POST /ask
  └─ app/graph.py        Axiom.ask() — LangGraph StateGraph
       intake            strip greetings/filler, dates, city/customer/group-by
       classify          rules + TF-IDF vs exemplars, bands 0.75 / 0.45
       verify            medium band: LLM verify (Azure) or ask to confirm
       clarify           low band / ambiguous metric -> clarification
       retrieve_schema   semantic.search_schema + past verified queries
       generate_sql      offline templates, or LLM when a key is set
       validate_sql      sqlglot: single SELECT, whitelist, PII, LIMIT
       execute           SQLite read-only, LIMIT 100
       validate_result   empty / outlier / schema checks
       respond           formatted answer, or escalate

POST /tickets/{id}/work  -> plan -> code read (TF-IDF over data/codebase)
                            -> proposed test -> review diff -> awaiting_review
                            -> POST /tickets/{id}/review (HITL approve/reject)

POST /disputes/{id}/resolve -> precedent search (TF-IDF over 12 resolved
                            disputes, 4 reason clusters) -> draft resolution
                            with citations, or human_review when no precedent

Every step -> data/audit.log (JSONL) + counters -> GET /router, GET /metrics
```

### Confidence bands

- **High (≥ 0.75):** routes straight to the domain.
- **Medium (0.45–0.75):** needs LLM verification — offline, the API asks
  for confirmation and never routes silently.
- **Low (< 0.45):** asks a clarification question. `unknown` is always low.

### Three safety gates (analytics)

1. **Validation before execution:** sqlglot parse, single `SELECT` only,
   whitelisted tables (`customers`, `invoices`, `payments`, `refunds`,
   `disputes`), PII columns blocked unless masked, `SELECT *` blocked,
   `LIMIT ≤ 100`.
2. **Controlled execution:** read-only SQLite proxy, row/cost limits.
3. **Result validation after:** empty-result, outlier (> 3× median), and
   schema-conformance checks. A failure regenerates once, then escalates —
   never an invented answer.

### Modules

| module | job |
|---|---|
| `app/config.py` | env config; Azure vars lazy, never from files |
| `app/llm.py` | Azure OpenAI only when keys set; failure → `AzureError` → HTTP 502 |
| `app/pii.py` | mask email/phone/Aadhaar/PAN/account/card; `contains_pii()` |
| `app/retrieval.py` | stdlib TF-IDF over `{id, text, meta}` docs; pgvector only with `DATABASE_URL` |
| `app/semantic.py` | `data/metrics.json`: metrics, synonyms, table/column map, disambiguation |
| `app/classifier.py` | rules + TF-IDF ensemble; bands high/medium/low |
| `app/sqlsafety.py` | sqlglot validation + `execute_readonly` (SQLite) |
| `app/audit.py` | append-only JSONL audit log |
| `app/analytics.py` | intake, date normalisation, template SQL builder, result checks |
| `app/tickets.py` | dev slice: plan → code read → test → diff → HITL review |
| `app/disputes.py` | disputes slice: precedent search, clusters, draft resolution |
| `app/graph.py` | LangGraph orchestrator + `Axiom.ask()` |
| `app/mcp_server.py` | stdio JSON-RPC MCP server (see below) |
| `app/main.py` | FastAPI: all endpoints above |

## Retrieval backends

Retrieval is lexical TF-IDF by default (stdlib only, in-memory). Setting
`DATABASE_URL` selects the pgvector backend, but honestly: the pgvector
`embedding` column stays NULL in local mode — no embeddings are computed
without Azure — so retrieval stays lexical (TF-IDF over the same docs)
until Azure embeddings exist. Selecting pgvector today changes the backend
name, not the retrieval quality. `GET /health` reports the *configured*
backend (`"retrieval": "tfidf" | "pgvector"`), not a semantic capability.

## Docker

```bash
docker compose up --build    # api + pgvector/pgvector:pg16
```

Docker files are written but were **not built on this machine** (no Docker
here) — the compose stack is untested locally. Set `POSTGRES_PASSWORD` in a
gitignored `.env` file first — compose refuses to start without it (no
default password is committed). Postgres has no host port published; it is
reachable only inside the compose network.

## MCP

A minimal stdlib-only stdio tool server, four tools, no extra dependency:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | .venv/bin/python app/mcp_server.py
```

Tools: `search_codebase(query, k=3)`, `ask_analytics(question)`,
`resolve_dispute(dispute_id)`, `work_ticket(ticket_id)`. One JSON-RPC
request per line; one JSON object per line on stdout.

## Interview knowledge-map coverage

Axiom is the capstone for the backend + GenAI interview knowledge map
(19 sections). Honest coverage, per the map's own accuracy note — what was
implemented vs what is known conceptually:

**Hands-on in this build:** §1 Python Core (Pydantic, dataclasses,
exception handling, type hints; money math in integer paise, formatted by
`format_inr`) · §2 Backend / FastAPI / APIs (routers, OpenAPI, health
checks) · §3 SQL / Databases (Postgres, pgvector, joins / CTEs, indexes) ·
§5 GenAI / LLM Fundamentals (prompting, tokens, hallucination, guardrails) ·
§6 RAG (chunking, embeddings, hybrid search, re-ranking) · §7 RAGAS /
DeepEval (the eval harness: groundedness, relevance, faithfulness) · §8
LangGraph / Agents / MCP (orchestrator, supervisor + specialist agents, MCP
tool servers — LangGraph only, no LangChain) · §11 System Design (the
architecture itself, plus production incident scenarios as eval cases) ·
§12 Cloud / DevOps / Observability (Docker + docker-compose, GitHub Actions
CI, JSONL audit log as the observability spine) · §13 Testing (unit,
integration, contract and API suites on every release) · §14 Story Bank +
§17 Answer Frameworks (the interviewer guide is the story-bank entry for
this project) · §16 Capstone (Axiom replaces the map's proposed healthcare
capstone).

**Partial (touched, not deep):** §4 Machine Learning — the small intent
classifier (precision/recall, calibration, thresholds); not full algorithms
or time series. §10 Healthcare AI — safety / privacy / reliability patterns
transfer (PII masking, audit, HITL); the healthcare domain itself does not
belong in Axiom.

**Conceptual / revision only (not in this build):** §3 Spark / lakehouse /
distributed data systems; §4 algorithms and time series beyond the
classifier; §1 internals theory (GIL, GC) beyond what the code exercises;
§9 caching / performance / cost (response caching, token cost governance —
not implemented yet); §12 OpenTelemetry / Langfuse vendor traces (the audit
log is the observability spine for now); §15 coding practice (stays as
practice problems).

## UI

The `site/` front-end (mixed Linear + Hex + LangSmith direction) is being
built in parallel; screenshots will be added here once reviewed. No
screenshots are embedded until then.

## What is deliberately not built

RAGAS / DeepEval as a library (the eval is a deterministic scorer),
LangChain (LangGraph alone), Langfuse / OpenTelemetry vendor calls (local
JSONL + `/metrics`), auth / multi-tenancy / RBAC, email / Slack sending
(drafts only, nothing is sent), a live learning loop (the golden-set eval
in CI is the loop for now), embeddings in local mode, a second frontend.
See `SPEC.md`.

## Stack

Python 3.12, FastAPI, LangGraph, sqlglot, SQLite (+ pgvector when
`DATABASE_URL` is set). LangGraph only — no LangChain anywhere in the code.
No deployed URL, no live demo — this repo is the demo.
