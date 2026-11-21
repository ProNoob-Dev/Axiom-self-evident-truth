# Axiom — SPEC (Phase 1)

One enterprise agentic copilot platform: a shared LangGraph core with three
agent domains on top. **Phase 1 ships the shared core + the conversational
analytics slice only.** Tickets (dev) and disputes slices land in Phase 2.

## Weekend MVP cut (law)

1. **Core:** LangGraph orchestrator + confidence-routed classifier + TF-IDF
   retrieval over past verified queries + governed semantic layer + guardrails
   (PII masking, SQL safety) + audit log + router dashboard.
2. **Analytics slice:** one billing question flow — ask → validated SQL →
   result, with SQL and sources shown.

Not in Phase 1: tickets slice, disputes slice, MCP server, UI/site, alerts,
Slack bot, digests, PR bot, RBAC, lineage view, GraphRAG, voice.

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
4 SQL-validation cases) and writes `eval/last_run.json` with measured
numbers only. CI gates: router accuracy ≥ 0.8, all SQL-validation cases
correct, analytics ≥ 5/6.

Measured on the golden set (21 Nov 2026, this build): router 12/12,
analytics 6/6, SQL validation 4/4, false-positive rate 0.0. These are
golden-set measurements, not production outcomes — the draft's 88% / <2% /
40% figures are invented targets and are not claimed anywhere here.
