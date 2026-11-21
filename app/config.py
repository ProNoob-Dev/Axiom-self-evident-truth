"""Environment config (single source of truth). Env only, never files."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

ROW_LIMIT = int(os.environ.get("ROW_LIMIT", "100"))
HIGH_CONFIDENCE = 0.75
MEDIUM_CONFIDENCE = 0.45

AUDIT_LOG_PATH = os.environ.get("AUDIT_LOG_PATH", str(DATA_DIR / "audit.log"))
DB_PATH = os.environ.get("BILLING_DB_PATH", str(DATA_DIR / "billing.sqlite"))


def azure_api_key() -> str:
    return os.environ.get("AZURE_OPENAI_API_KEY", "")


def azure_endpoint() -> str:
    return os.environ.get("AZURE_OPENAI_ENDPOINT", "")


def azure_deployment() -> str:
    return os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")


def llm_provider() -> str:
    return "azure" if azure_api_key() else "offline"


def retrieval_backend() -> str:
    return "pgvector" if os.environ.get("DATABASE_URL") else "tfidf"
