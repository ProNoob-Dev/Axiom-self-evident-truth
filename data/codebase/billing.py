"""Billing service (sample codebase for the Axiom dev slice).

Money is stored in paise (int) everywhere; only the presentation layer
converts to rupees. See docs/adr-001.md.
"""

from __future__ import annotations


def total_refunds_paise(refunds: list[int]) -> int:
    """Sum refund amounts, in paise."""
    return sum(refunds)


def format_inr(paise: int) -> str:
    """Paise -> 'Rs 1,234.56' with two decimals."""
    rupees = paise / 100
    return f"Rs {rupees:,.2f}"


def refund_summary(refunds: list[int]) -> str:
    """One-line refund total for the dashboard.

    BUG (TKT-101): the paise total is formatted directly as rupees
    (``f"Rs {total:,.2f}"``) instead of going through ``format_inr``,
    so a Rs 1,234.56 refund shows as Rs 123,456.00 — 100x too large.
    The fix is ``format_inr(total_refunds_paise(refunds))``.
    """
    total = total_refunds_paise(refunds)
    return f"Total refunds: Rs {total:,.2f}"


def invoice_total_paise(items: list[int]) -> int:
    """Sum invoice line items, in paise."""
    return sum(items)


def apply_refund(invoice_paise: int, refund_paise: int) -> int:
    """Remaining balance after a refund, in paise. Raises on over-refund."""
    if refund_paise < 0:
        raise ValueError("refund must not be negative")
    if refund_paise > invoice_paise:
        raise ValueError("refund exceeds invoice total")
    return invoice_paise - refund_paise
