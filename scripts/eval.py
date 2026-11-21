"""Golden-set eval: router accuracy, analytics correctness, SQL validation,
tickets flow, disputes precedent.

Writes eval/last_run.json with measured numbers only. Exit code 1 when a
gate fails: router accuracy < 0.8, any SQL-validation case wrong,
analytics < 5/6 correct, or any tickets/disputes case wrong.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app import disputes as disputes_mod  # noqa: E402
from app import tickets as tickets_mod  # noqa: E402
from app.classifier import Classifier  # noqa: E402
from app.graph import Axiom  # noqa: E402
from app.sqlsafety import execute_readonly, validate  # noqa: E402
from scripts.seed import seed  # noqa: E402

GOLDEN_PATH = BASE_DIR / "data" / "golden_eval.json"
OUT_PATH = BASE_DIR / "eval" / "last_run.json"


def _run_sql(sql: str, db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(sql)
        return cur.fetchall()
    finally:
        conn.close()


def main() -> int:
    db_path = BASE_DIR / "data" / "billing.sqlite"
    if not db_path.exists():
        seed(db_path)
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    classifier = Classifier()
    with tempfile.TemporaryDirectory() as tmp:
        axiom = Axiom(db_path=str(db_path),
                      audit_path=str(Path(tmp) / "audit.log"))

        router_results = []
        false_positives = 0
        for case in golden["router"]:
            got = classifier.classify(case["question"])
            correct = (got["domain"] == case["expected_domain"]
                       and got["band"] == case["expected_band"])
            # False positive: routed to a real domain with high confidence
            # when the expected domain is unknown.
            if (case["expected_domain"] == "unknown" and got["domain"] != "unknown"
                    and got["band"] == "high"):
                false_positives += 1
            router_results.append({"question": case["question"],
                                   "expected": case["expected_domain"],
                                   "expected_band": case["expected_band"],
                                   "got": got["domain"], "band": got["band"],
                                   "confidence": got["confidence"],
                                   "correct": correct})

        analytics_results = []
        for case in golden["analytics"]:
            result = axiom.ask(case["question"])
            pattern_ok = bool(result.get("sql")) and bool(
                re.search(case["expected_sql_pattern"], result["sql"], re.I | re.S))
            expected_rows = _run_sql(case["compare_sql"], str(db_path))
            got_rows = [tuple(r.values()) for r in result.get("rows", [])]
            # Order-insensitive: ties in ORDER BY make row order unstable.
            values_ok = sorted(map(str, got_rows)) == sorted(map(str, expected_rows))
            analytics_results.append({
                "question": case["question"], "sql": result.get("sql"),
                "route": result.get("route"),
                "pattern_match": pattern_ok, "values_match": values_ok,
                "correct": pattern_ok and values_ok and result.get("route") == "answered"})

        sql_results = []
        for case in golden["sql_validation"]:
            ok, errors = validate(case["sql"])
            sql_results.append({"sql": case["sql"], "expected_ok": case["expected_ok"],
                                "got_ok": ok, "errors": errors,
                                "correct": ok == case["expected_ok"],
                                "why": case["why"]})

        # Tickets: run against a temp copy so the eval never mutates the
        # shipped tickets file.
        ticket_results = []
        tmp_tickets = Path(tmp) / "tickets.json"
        shutil.copy(BASE_DIR / "data" / "tickets.json", tmp_tickets)
        for case in golden.get("tickets", []):
            expect = case.get("expect", {})
            if expect.get("error") == "not_found":
                try:
                    tickets_mod.work_ticket(case["ticket_id"], path=str(tmp_tickets),
                                            audit_path=str(Path(tmp) / "audit.log"))
                    correct = False
                except KeyError:
                    correct = True
                ticket_results.append({"ticket_id": case["ticket_id"],
                                       "correct": correct})
                continue
            worked = tickets_mod.work_ticket(
                case["ticket_id"], path=str(tmp_tickets),
                audit_path=str(Path(tmp) / "audit.log"))
            if "review" in case:
                # Work must land in awaiting_review, then the HITL decision.
                correct = worked["status"] == "awaiting_review"
            else:
                correct = (worked["status"] == expect["status"]
                           and bool(worked.get("plan")) == expect.get("has_plan", True)
                           and bool(worked.get("diff")) == expect.get("has_diff", True))
            if "review" in case:
                reviewed = tickets_mod.review_ticket(
                    case["ticket_id"], case["review"], case.get("note"),
                    path=str(tmp_tickets), audit_path=str(Path(tmp) / "audit.log"))
                correct = correct and reviewed["status"] == case["expect_status"]
            ticket_results.append({"ticket_id": case["ticket_id"],
                                   "status": worked["status"], "correct": correct})

        # Disputes: resolve is read-only on the DB; audit goes to tmp.
        dispute_results = []
        for case in golden.get("disputes", []):
            result = disputes_mod.resolve_dispute(
                case["dispute_id"], audit_path=str(Path(tmp) / "audit.log"))
            expect = case["expect"]
            if expect.get("has_precedent"):
                correct = (len(result["precedents"]) >= expect["min_precedents"]
                           and result["citations"] == [p["id"] for p in result["precedents"]]
                           and not result["human_review"])
            else:
                correct = (not result["precedents"] and result["human_review"]
                           and "human review" in result["draft_resolution"])
            dispute_results.append({"dispute_id": case["dispute_id"],
                                    "precedents": [p["id"] for p in result["precedents"]],
                                    "human_review": result["human_review"],
                                    "correct": correct})

    router_correct = sum(r["correct"] for r in router_results)
    analytics_correct = sum(r["correct"] for r in analytics_results)
    sql_correct = sum(r["correct"] for r in sql_results)
    tickets_correct = sum(r["correct"] for r in ticket_results)
    disputes_correct = sum(r["correct"] for r in dispute_results)
    report = {
        "golden": {
            "total": len(router_results) + len(analytics_results) + len(sql_results)
            + len(ticket_results) + len(dispute_results),
            "router_accuracy": router_correct / len(router_results),
            "router_correct": router_correct,
            "router_total": len(router_results),
            "false_positive_rate": false_positives / len(router_results),
            "false_positives": false_positives,
            "analytics_correct": analytics_correct,
            "analytics_total": len(analytics_results),
            "sql_validation_correct": sql_correct,
            "sql_validation_total": len(sql_results),
            "tickets_correct": tickets_correct,
            "tickets_total": len(ticket_results),
            "disputes_correct": disputes_correct,
            "disputes_total": len(dispute_results),
        },
        "router": router_results,
        "analytics": analytics_results,
        "sql_validation": sql_results,
        "tickets": ticket_results,
        "disputes": dispute_results,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["golden"], indent=2))
    gates_ok = (report["golden"]["router_accuracy"] >= 0.8
                and sql_correct == len(sql_results)
                and analytics_correct >= 5
                and tickets_correct == len(ticket_results)
                and disputes_correct == len(dispute_results))
    return 0 if gates_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
