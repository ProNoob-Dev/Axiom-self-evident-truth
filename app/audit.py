"""Append-only JSONL audit log: every question, routing decision, SQL,
validation result, execution and guardrail decision.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config


def append(event: str, request_id: str, payload: dict | None = None,
           path: str | Path | None = None) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "request_id": request_id,
        "payload": payload or {},
    }
    log_path = Path(path) if path is not None else Path(config.AUDIT_LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def read_all(path: str | Path | None = None) -> list[dict]:
    log_path = Path(path) if path is not None else Path(config.AUDIT_LOG_PATH)
    if not log_path.exists():
        return []
    records = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records
