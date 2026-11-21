"""Existing tests for the billing service.

Note: no test covers refund_summary's display value — that gap is
what TKT-101 is about.
"""

from billing import apply_refund, format_inr, invoice_total_paise, total_refunds_paise


def test_total_refunds_paise():
    assert total_refunds_paise([10000, 23456]) == 33456


def test_format_inr():
    assert format_inr(123456) == "Rs 1,234.56"


def test_invoice_total_paise():
    assert invoice_total_paise([5000, 7500]) == 12500


def test_apply_refund():
    assert apply_refund(100000, 25000) == 75000


def test_apply_refund_over_refund_raises():
    try:
        apply_refund(1000, 2000)
    except ValueError:
        return
    raise AssertionError("expected ValueError for over-refund")
