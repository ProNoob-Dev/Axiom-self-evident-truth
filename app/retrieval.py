"""Retrieval: stdlib TF-IDF (default) and a guarded pgvector backend.

Same approach as N28: TF-IDF is lexical, not semantic — paraphrases with
no shared words will miss.
shortcut: upgrade to embeddings (pgvector + Azure embeddings) only when a
real key and Postgres exist.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from . import config

_STOPWORDS = frozenset(
    "a an the and or of to in on for with is are was were be been do does did "
    "i you he she it we they my your his her our their this that these those "
    "how what when where which who whom can could should would will shall may "
    "there here about from by at as not no yes if then than so such per "
    "me get gets got have has had show tell give find list".split()
)

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


@dataclass
class Doc:
    """One retrievable document: id, searchable text, and free-form meta."""

    id: str
    text: str
    meta: dict | None = None


@dataclass
class ScoredDoc:
    doc: Doc
    score: float


class TfidfRetriever:
    """In-memory TF-IDF retriever, standard library only."""

    def __init__(self, docs: list[Doc]) -> None:
        self.docs = docs
        self._doc_freq: Counter[str] = Counter()
        self._vectors: list[dict[str, float]] = []
        self._norms: list[float] = []
        for doc in docs:
            counts = Counter(tokenize(doc.text))
            for term in counts:
                self._doc_freq[term] += 1
        n = len(docs)
        for doc in docs:
            counts = Counter(tokenize(doc.text))
            vector = {
                term: (count / sum(counts.values()))
                * (math.log((n + 1) / (self._doc_freq[term] + 1)) + 1.0)
                for term, count in counts.items()
            }
            self._vectors.append(vector)
            self._norms.append(math.sqrt(sum(v * v for v in vector.values())) or 1.0)

    def retrieve(self, query: str, k: int = 3) -> list[ScoredDoc]:
        counts = Counter(tokenize(query))
        if not counts or not self.docs:
            return []
        total = sum(counts.values())
        n = len(self.docs)
        qvec = {
            term: (count / total) * (math.log((n + 1) / (self._doc_freq.get(term, 0) + 1)) + 1.0)
            for term, count in counts.items()
        }
        qnorm = math.sqrt(sum(v * v for v in qvec.values())) or 1.0
        scored: list[ScoredDoc] = []
        for doc, vector, norm in zip(self.docs, self._vectors, self._norms):
            dot = sum(qvec.get(term, 0.0) * weight for term, weight in vector.items())
            scored.append(ScoredDoc(doc, dot / (qnorm * norm)))
        scored.sort(key=lambda s: (-s.score, s.doc.id))
        return [s for s in scored[:k] if s.score > 0]


class PgVectorRetriever:
    """Postgres + pgvector backend. Only constructed when DATABASE_URL is set.

    No embeddings exist yet, so this scores with the same TF-IDF until they
    do. It exists so the interface is real, not to pretend semantic search
    works.
    """

    def __init__(self, docs: list[Doc]) -> None:
        self._fallback = TfidfRetriever(docs)

    def retrieve(self, query: str, k: int = 3) -> list[ScoredDoc]:
        return self._fallback.retrieve(query, k)


def get_retriever(docs: list[Doc]):
    """TF-IDF by default; pgvector only when DATABASE_URL is set."""
    if config.retrieval_backend() == "pgvector":
        return PgVectorRetriever(docs)
    return TfidfRetriever(docs)
