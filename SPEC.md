# Axiom — SPEC (Phase 2)

One enterprise agentic copilot platform: a shared LangGraph core with three
agent domains on top. **Phase 2 adds the tickets (dev) and disputes slices
plus the MCP server.** Phase 1 shipped the shared core + the conversational
analytics slice.

## Weekend MVP cut (law)

1. **Core:** LangGraph orchestrator + confidence-routed classifier + TF-IDF
   retrieval over past verified queries + governed semantic layer + guardrails
   (PII masking, SQL safety) + audit log + router dashboard.
2. **Analytics slice:** one billing question flow — ask → validated SQL →
   result, with SQL and sources shown.
3. **Dev slice:** one ticket flow — plan → code read → test write → review
   diff, HITL approval (`POST /tickets/{id}/work`, `POST /tickets/{id}/review`).
4. **Disputes slice:** one dispute flow — precedent search → draft resolution
   with citations (`POST /disputes/{id}/resolve`).
5. **MCP server:** stdio JSON-RPC (`app/mcp_server.py`) — search_codebase,
   ask_analytics, resolve_dispute, work_ticket.

Not in Phase 2: UI/site, alerts, Slack bot, digests, PR bot, RBAC, lineage
view, GraphRAG, voice.

## Ask routing (Phase 2)

High-confidence dev/disputes questions no longer return a placeholder:
`/ask` returns `{route: "dev", matched_ticket, action: "POST /tickets/{id}/work", summary}`
or `{route: "disputes", matched_dispute, action: "POST /disputes/{id}/resolve", summary}`.
The dedicated endpoint does the full work; `/ask` does not pretend to have
run it. Analytics flow is unchanged from Phase 1.

## Tickets slice

`data/tickets.json` (6 tickets) + `data/codebase/` (sample billing module
with a planted paise/rupees display bug, its tests, README, 2 ADRs).
`work_ticket` runs plan → code read (TF-IDF over the codebase, with line
references) → proposed pytest test → unified-diff-style review proposal,
and leaves the ticket `awaiting_review`. `review_ticket` approves/rejects
(HITL); only `awaiting_review` tickets can be reviewed. Ticket state is
held in memory and persisted to `data/tickets.json`; every step is
audit-logged; emails/phones are PII-masked on output.

## Disputes slice

Live disputes are read from `data/billing.sqlite` (joined to customers /
invoices); there is no second store for them. Precedents are the 12
resolved disputes in `data/disputes.json`, in 4 reason clusters (duplicate
billing, refund delay, wrong amount, service not delivered). `resolve_dispute`
runs TF-IDF precedent search (threshold 0.10) and drafts a resolution citing
the top 2–3 precedents. A dispute with no precedent above threshold is
routed to a human honestly (`human_review: true`, no resolution drafted) —
an unauthorised-transaction dispute has no precedent in the corpus.

## MCP server

Stdlib-only stdio JSON-RPC, same protocol shape as the N28 MCP server
(`initialize`, `tools/list`, `tools/call`). Run it with:

```bash
.venv/bin/python app/mcp_server.py < requests.jsonl
```

One JSON-RPC request per line; replies are one JSON object per line on stdout.

## Ask flow

`intake → classify → retrieve_schema → generate_sql → validate_sql →
execute → validate_result → respond`

- **Classifier:** keyword rules + TF-IDF vs per-domain exemplars.
  Confidence ≥ 0.75 = high (route direct); 0.45–0.75 = medium (needs LLM
  verification — offline we ask for confirmation, never route silently);
  < 0.45 = low (clarification question). "unknown" is always low band.
- **Semantic layer** (`data/metrics.json`): canonical metrics — revenue is
  net after refunds; refunds; refund_rate; dispute_rate; avg_invoice_value
  (plus gross_revenue as a separate metric). Synonyms map sales/turnover →
  revenue. A term matching 2+ metrics returns the ambiguity, never a guess.
- **SQL generation:** deterministic templates offline (metric + filters +
  group-by → SELECT). With an Azure key set, the LLM generates SQL instead,
  through the same validation.
- **SQL safety:** sqlglot parse; single SELECT only; whitelisted tables
  (customers, invoices, payments, refunds, disputes); PII columns
  (email, phone, account_number) blocked unless masked; `SELECT *` blocked;
  LIMIT ≤ 100 enforced; SQLite read-only execution.
- **Result validation:** empty-result, outlier (> 3× median), schema
  conformance. A failure regenerates once, then escalates — never an
  invented answer.
- **LLM:** Azure OpenAI only when keys are set. Unavailable/failed LLM →
  HTTP 502, never a silent canned answer.
- **Audit:** append-only JSONL (`data/audit.log`): every question, routing
  decision, SQL, validation result, execution, guardrail decision.

## Data

`scripts/seed.py` builds `data/billing.sqlite` (idempotent, dates relative
to today): 15 customers, 40 invoices, ~30 payments, 12 refunds, 10 disputes,
realistic Indian names/cities, INR amounts in paise. PII columns exist in
the data to prove masking; the API never returns them unmasked.

## Endpoints

- `GET /health` — status, llm_provider, retrieval backend
- `POST /ask` — `{question, session_id?}` → answer, SQL, sources, route,
  confidence, band, validation, rows
- `GET /schema` — metrics, synonyms, tables
- `GET /router` — confidence distribution, domains, golden-set accuracy and
  false-positive rate (measured only; live traffic has no labels)
- `GET /metrics` — autonomy rate, routing accuracy, counts

## Evals

`scripts/eval.py` runs `data/golden_eval.json` (12 router, 6 analytics,
4 SQL-validation, 4 tickets, 4 disputes cases) and writes `eval/last_run.json`
with measured numbers only. CI gates: router accuracy ≥ 0.8, all SQL-validation
cases correct, analytics ≥ 5/6, all tickets and disputes cases correct.

Measured on the golden set (21 Nov 2026, this build): router 12/12,
analytics 6/6, SQL validation 4/4, tickets 4/4, disputes 4/4,
false-positive rate 0.0. These are golden-set measurements, not production
outcomes — the draft's 88% / <2% / 40% figures are invented targets and are
not claimed anywhere here.
