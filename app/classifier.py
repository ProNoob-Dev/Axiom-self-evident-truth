"""Confidence-routed intent classifier.

Ensemble of (a) keyword rules and (b) TF-IDF similarity against per-domain
exemplar questions. Bands: >= 0.75 high (route direct), 0.45–0.75 medium
(needs LLM verification; offline we do NOT route silently), < 0.45 low
(ask a clarification question).
"""

from __future__ import annotations

import re

from . import config
from .retrieval import Doc, TfidfRetriever

_DOMAINS = ("analytics", "dev", "disputes", "unknown")

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "analytics": (
        "revenue", "refund", "refunds", "invoice", "invoices", "payment",
        "payments", "sales", "turnover", "billing", "paid", "unpaid",
        "outstanding", "customer", "customers", "city", "amount", "gross",
        "net", "rate", "average", "total", "sum", "count", "month",
        "monthly", "rupees", "inr",
    ),
    "dev": (
        "ticket", "tickets", "code", "pull request", "pr", "review", "test",
        "tests", "bug", "fix", "implement", "implementation", "feature",
        "branch", "commit", "deploy", "sprint", "backlog", "refactor",
        "function", "plan", "write", "api",
    ),
    "disputes": (
        "dispute", "disputes", "disputed", "chargeback", "charge", "charged",
        "duplicate", "precedent", "resolution", "resolve", "open disputes",
    ),
    "unknown": (),
}

_EXEMPLARS: dict[str, tuple[str, ...]] = {
    "analytics": (
        "What were total refunds in Mumbai last month?",
        "Show me revenue by city",
        "What is the average invoice value?",
        "How many invoices were paid in October?",
        "What is the dispute rate by city?",
        "Which customers have the most refunds?",
    ),
    "dev": (
        "Work on ticket AUTH-45: plan the implementation",
        "Write tests for the payment service",
        "Review this pull request for the billing API",
        "Fix the bug in the refund handler",
        "Plan the sprint backlog for the billing team",
        "Implement the retry logic for the payment client",
    ),
    "disputes": (
        "Why was this invoice disputed?",
        "Find similar past disputes about duplicate charges",
        "Draft a resolution for dispute DSP-3",
        "Show open disputes for a customer",
        "What is the precedent for a duplicate charge dispute?",
        "List disputes about a service not delivered",
    ),
    "unknown": (
        "What is the weather like today?",
        "Tell me a joke",
        "Play some music",
        "What is the capital of France?",
    ),
}

_CLARIFICATION = (
    "I can help with billing analytics, dev tickets, or charge disputes. "
    "Could you say which one this is about — for example, "
    "'total refunds in Mumbai last month'?"
)

_COMPILED: dict[str, list[re.Pattern[str]]] = {
    domain: [re.compile(r"\b" + re.escape(kw) + r"\b", re.I) for kw in kws]
    for domain, kws in _KEYWORDS.items()
}


def _rule_scores(text: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    for domain, patterns in _COMPILED.items():
        hits = sum(1 for p in patterns if p.search(text))
        scores[domain] = min(0.95, 0.55 + 0.15 * (hits - 1)) if hits else 0.0
    return scores


class Classifier:
    def __init__(self) -> None:
        docs = [Doc(id=f"{d}-{i}", text=q, meta={"domain": d})
                for d, qs in _EXEMPLARS.items() for i, q in enumerate(qs)]
        self._retriever = TfidfRetriever(docs)

    def classify(self, text: str) -> dict:
        rule = _rule_scores(text)
        tfidf: dict[str, float] = {d: 0.0 for d in _DOMAINS}
        for scored in self._retriever.retrieve(text, k=6):
            domain = scored.doc.meta["domain"]
            tfidf[domain] = max(tfidf[domain], round(scored.score, 3))
        scores: dict[str, float] = {}
        for domain in _DOMAINS:
            conf = max(rule[domain], tfidf[domain])
            if rule[domain] > 0 and tfidf[domain] > 0 and rule[domain] == max(rule.values()):
                conf = min(0.99, conf + 0.05)
            scores[domain] = round(conf, 3)
        domain = max(scores, key=scores.get)
        confidence = scores[domain]
        # "unknown" is never routed: confidence in it means confidently out
        # of scope, so it is always the low band with a clarification.
        if domain == "unknown" or confidence < config.MEDIUM_CONFIDENCE:
            band = "low"
        elif confidence >= config.HIGH_CONFIDENCE:
            band = "high"
        else:
            band = "medium"
        if band == "low":
            domain = "unknown"
        result: dict = {
            "domain": domain,
            "confidence": confidence,
            "band": band,
            "needs_verification": band == "medium",
            "scores": scores,
        }
        if band == "low":
            result["clarification"] = _CLARIFICATION
        return result
