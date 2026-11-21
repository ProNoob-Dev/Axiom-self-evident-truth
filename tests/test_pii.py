from app.pii import contains_pii, mask_pii


def test_email_masked():
    assert mask_pii("contact aarav.mehta@example.com please") == "contact [EMAIL] please"


def test_phone_masked():
    masked = mask_pii("call me on 98765 43210 or +91 9876543210")
    assert "98765" not in masked and "[PHONE]" in masked


def test_account_number_masked():
    assert mask_pii("account ACC12345678") == "account [ACCOUNT]"


def test_contains_pii():
    assert contains_pii("reach me at priya@example.com")
    assert not contains_pii("total refunds in Mumbai last month")


def test_mask_is_idempotent():
    once = mask_pii("mail a@b.com, phone 9876543210")
    assert mask_pii(once) == once
