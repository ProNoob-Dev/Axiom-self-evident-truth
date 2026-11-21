# Architecture (Phase 1)

```
POST /ask
  └─ app/graph.py        Axiom.ask() — LangGraph StateGraph
       intake            strip greetings/filler, dates, city/customer/group-by
       classify          rules + TF-IDF vs exemplars, bands 0.75 / 0.45
       verify            medium band: LLM verify (azure) or ask to confirm
       clarify           low band / ambiguous metric -> clarification
       retrieve_schema   semantic.search_schema + past verified queries
       generate_sql      offline templates, or LLM when a key is set
       validate_sql      sqlglot: single SELECT, whitelist, PII, LIMIT
       execute           SQLite read-only, LIMIT 100
       validate_result   empty / outlier / schema checks
       respond           formatted answer, or escalate
```

## Modules

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
| `app/graph.py` | LangGraph orchestrator + `Axiom.ask()` |
| `app/main.py` | FastAPI: `/health`, `/ask`, `/schema`, `/router`, `/metrics` |

## Phase 2 (done)

`app/main.py` wires the tickets/disputes routers with two
`include_router` lines. The graph routes high-confidence dev/disputes
questions to a `phase2` response that names the ticket/dispute and the
dedicated endpoint — `/ask` does not run the work itself.
