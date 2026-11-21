"""Governed semantic layer, loaded from data/metrics.json.

Canonical metric definitions, synonyms, and the table/column map for the
billing schema. Disambiguation: a term mapping to 2+ metrics returns the
ambiguity instead of guessing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

_METRICS_PATH = config.DATA_DIR / "metrics.json"


def load_metrics(path: Path | None = None) -> dict:
    path = path or _METRICS_PATH
    return json.loads(path.read_text(encoding="utf-8"))


class SemanticLayer:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data if data is not None else load_metrics()
        self.metrics: dict[str, dict] = self.data.get("metrics", {})
        self.tables: dict[str, dict] = self.data.get("tables", {})
        # synonym (lowercased) -> metric key. Longer synonyms first so
        # "gross revenue" wins over "revenue" in the same text.
        self._synonyms: list[tuple[str, str]] = []
        for key, metric in self.metrics.items():
            for syn in [key.replace("_", " ")] + metric.get("synonyms", []):
                self._synonyms.append((syn.lower(), key))
        self._synonyms.sort(key=lambda item: -len(item[0]))

    def resolve_metric(self, text: str) -> dict:
        """Resolve a metric from ``text``.

        Returns {"metric": key | None, "ambiguous": [keys] | None, ...}.
        Synonyms match on word boundaries; a synonym fully contained in a
        longer synonym of a *different* metric at the same spot is shadowed
        ("refund" inside "refund rate"), but a second real mention still
        counts ("gross revenue and net revenue" stays ambiguous).
        Bare "revenue" resolves to the net-revenue metric.
        """
        lowered = text.lower()
        matches: list[tuple[int, int, str]] = []  # (start, end, metric key)
        for synonym, key in self._synonyms:
            for m in re.finditer(r"\b" + re.escape(synonym) + r"\b", lowered):
                matches.append((m.start(), m.end(), key))
        kept: list[str] = []
        for start, end, key in matches:
            shadowed = any(
                s2 <= start and end <= e2 and key2 != key and (s2, e2) != (start, end)
                for s2, e2, key2 in matches)
            if not shadowed and key not in kept:
                kept.append(key)
        if len(kept) == 1:
            key = kept[0]
            return {"metric": key, "ambiguous": None,
                    "definition": self.metrics[key]["definition"]}
        if len(kept) > 1:
            return {"metric": None, "ambiguous": kept, "definition": None}
        return {"metric": None, "ambiguous": None, "definition": None}

    def search_schema(self, query: str) -> list[dict]:
        """Relevant tables/columns for a question, by keyword overlap."""
        words = set(query.lower().split())
        hits: list[dict] = []
        for table, spec in self.tables.items():
            columns = [c["name"] for c in spec.get("columns", [])]
            matched = [c for c in columns if c in words or c.replace("_", " ") in query.lower()]
            score = len(matched) + (1 if table in words else 0)
            hits.append({"table": table, "description": spec.get("description", ""),
                         "columns": columns, "matched_columns": matched, "score": score})
        hits.sort(key=lambda h: (-h["score"], h["table"]))
        return [h for h in hits if h["score"] > 0]

    def to_dict(self) -> dict:
        return {"metrics": self.metrics, "tables": self.tables}
