"""PII masking (stdlib ``re`` only). Same approach as N28."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Email
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    # Aadhaar: spaced (4-4-4) or unspaced 12 digits.
    (re.compile(r"(?<![0-9A-Za-z])(?:\d{4} \d{4} \d{4}|\d{12})(?![0-9A-Za-z])"),
     "[AADHAAR]"),
    # PAN: 5 letters, 4 digits, 1 letter.
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.I), "[PAN]"),
    # Phone: +91 optional, 10 digits starting 6-9.
    (re.compile(r"(?<![0-9A-Za-z])(?:\+91[\s-]?)?\(?[6-9]\d{4}\)?[\s-]?\d{5}\)?(?![0-9A-Za-z])"),
     "[PHONE]"),
    # Account number: ACC followed by 8-12 digits, or a bare 10-16 digit run
    # labelled as an account number. Bare long digit runs are NOT masked
    # here — they are caught by the card pattern / SQL safety layer instead.
    (re.compile(r"\bACC\d{8,12}\b", re.I), "[ACCOUNT]"),
    # Card-like: 13-19 digits, optionally in groups of 4.
    (re.compile(r"(?<![0-9A-Za-z])(?:\d[ -]?){13,19}\d(?![0-9A-Za-z])"), "[CARD]"),
]

LEAK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?<![0-9A-Za-z])(?:\d{4} \d{4} \d{4}|\d{12})(?![0-9A-Za-z])"),
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.I),
    re.compile(r"(?<![0-9A-Za-z])(?:\+91[\s-]?)?\(?[6-9]\d{4}\)?[\s-]?\d{5}\)?(?![0-9A-Za-z])"),
    re.compile(r"\bACC\d{8,12}\b", re.I),
]


def mask_pii(text: str) -> str:
    """Replace PII in ``text`` with placeholders. Idempotent."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def contains_pii(text: str) -> bool:
    return any(p.search(text) for p in LEAK_PATTERNS)
