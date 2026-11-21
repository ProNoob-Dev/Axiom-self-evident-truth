import sqlite3


def _expected_refunds_mumbai_last_month(db_path):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT COALESCE(SUM(r.amount_paise), 0) FROM refunds r "
        "JOIN invoices i ON r.invoice_id = i.id "
        "JOIN customers c ON i.customer_id = c.id "
        "WHERE c.city = 'Mumbai' AND r.refunded_on >= date('now', 'start of month', '-1 month') "
        "AND r.refunded_on < date('now', 'start of month')").fetchone()
    conn.close()
    return row[0]


def test_ask_refunds_mumbai_last_month(client, db_path):
    resp = client.post("/ask", json={"question": "What were total refunds in Mumbai last month?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "answered"
    assert body["sql"] and "refunds" in body["sql"].lower()
    assert body["rows"], "seeded Mumbai refunds in last month expected"
    assert body["rows"][0]["refunds"] == _expected_refunds_mumbai_last_month(db_path)
    assert "₹" in body["answer"]


def test_ask_revenue_by_city(client):
    resp = client.post("/ask", json={"question": "Show me revenue by city"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "answered"
    assert "GROUP BY" in body["sql"]
    cities = {row["city"] for row in body["rows"]}
    assert "Mumbai" in cities and "Pune" in cities
    assert all("revenue" in row for row in body["rows"])


def test_ask_average_invoice_value(client, db_path):
    resp = client.post("/ask", json={"question": "What is the average invoice value?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "answered"
    conn = sqlite3.connect(db_path)
    expected = conn.execute("SELECT AVG(amount_paise) FROM invoices").fetchone()[0]
    conn.close()
    assert abs(body["rows"][0]["avg_invoice_value"] - expected) < 0.01


def test_ask_empty_result_handled(client):
    # A GROUP BY query with no matching city returns zero rows.
    resp = client.post("/ask", json={"question": "Show me total refunds by city in Indore last month?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "answered"
    assert body["rows"] == []
    assert body["validation"]["empty_result"] is True
    assert "No rows found" in body["answer"]


def test_ask_zero_aggregate_is_zero_not_empty(client):
    # A plain aggregate over no matching rows returns one row: 0.
    resp = client.post("/ask", json={"question": "What were total refunds in Indore last month?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "answered"
    assert body["rows"] == [{"refunds": 0}]
    assert "₹0" in body["answer"]


def test_ask_ambiguous_metric_clarifies(client):
    resp = client.post("/ask", json={"question": "Compare gross revenue and net revenue"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "clarified"
    assert "gross_revenue" in body["answer"] and "revenue" in body["answer"]
    assert body["sql"] is None


def test_ask_vague_question_clarifies(client):
    resp = client.post("/ask", json={"question": "tell me about stuff"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["route"] == "clarified"
    assert body["answer"]


def test_ask_never_returns_unmasked_pii(client):
    resp = client.post(
        "/ask", json={"question": "What were total refunds for aarav.mehta@example.com?"})
    assert resp.status_code == 200
    body = resp.json()
    assert "aarav.mehta@example.com" not in resp.text
    assert "example.com" not in resp.text
    assert all("email" not in row for row in body["rows"])


def test_health_schema_router_metrics(client):
    assert client.get("/health").json()["status"] == "ok"
    schema = client.get("/schema").json()
    assert "revenue" in schema["metrics"] and "invoices" in schema["tables"]
    assert client.get("/router").status_code == 200
    assert client.get("/metrics").status_code == 200
