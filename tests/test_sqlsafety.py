from app.sqlsafety import enforce_limit, execute_readonly, validate


def test_valid_select_passes():
    ok, errors = validate(
        "SELECT c.city, SUM(p.amount_paise) AS revenue FROM payments p "
        "JOIN invoices i ON p.invoice_id = i.id "
        "JOIN customers c ON i.customer_id = c.id GROUP BY c.city")
    assert ok and errors == []


def test_delete_rejected():
    ok, errors = validate("DELETE FROM refunds WHERE id = 1")
    assert not ok
    assert any("SELECT" in e for e in errors)


def test_ddl_rejected():
    ok, _ = validate("DROP TABLE customers")
    assert not ok


def test_multiple_statements_rejected():
    ok, errors = validate("SELECT 1; SELECT 2")
    assert not ok
    assert any("multiple statements" in e for e in errors)


def test_unknown_table_rejected():
    ok, errors = validate("SELECT * FROM secrets")
    assert not ok
    assert any("secrets" in e for e in errors)


def test_pii_column_blocked():
    ok, errors = validate("SELECT name, email FROM customers")
    assert not ok
    assert any("email" in e for e in errors)


def test_masked_pii_column_allowed():
    ok, errors = validate(
        "SELECT substr(email, 1, 3) AS email_prefix FROM customers")
    assert ok and errors == []


def test_limit_enforced():
    ok, errors = validate("SELECT id FROM invoices LIMIT 500")
    assert not ok
    assert any("LIMIT" in e for e in errors)
    limited = enforce_limit("SELECT id FROM invoices")
    assert "LIMIT 100" in limited


def test_execute_readonly_returns_rows(db_path):
    columns, rows = execute_readonly(
        "SELECT id, city FROM customers", db_path)
    assert columns == ["id", "city"]
    assert len(rows) == 15


def test_execute_readonly_rejects_bad_sql(db_path):
    try:
        execute_readonly("UPDATE customers SET city = 'X'", db_path)
    except ValueError as exc:
        assert "SELECT" in str(exc)
    else:
        raise AssertionError("expected ValueError")
