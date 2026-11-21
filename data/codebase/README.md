# Billing service (sample repo)

Small Python billing module used by the Axiom dev slice as the codebase
the agents read. Money is stored in paise (int); see `docs/adr-001.md`.

## Layout

- `billing.py` — refund/invoice helpers
- `test_billing.py` — existing tests (no display-value test for refunds)
- `docs/adr-001.md` — money in paise, display in rupees
- `docs/adr-002.md` — refunds are idempotent per invoice

## Run the tests

```bash
python -m pytest test_billing.py -q
```
