from app.classifier import Classifier


def test_billing_question_routes_analytics_high():
    result = Classifier().classify("What were total refunds in Mumbai last month?")
    assert result["domain"] == "analytics"
    assert result["band"] == "high"
    assert result["confidence"] >= 0.75
    assert result["needs_verification"] is False


def test_revenue_by_city_high():
    result = Classifier().classify("Show me revenue by city")
    assert result["domain"] == "analytics"
    assert result["band"] == "high"


def test_vague_question_asks_clarification():
    result = Classifier().classify("tell me about stuff")
    assert result["band"] == "low"
    assert result["domain"] == "unknown"
    assert "clarification" in result


def test_out_of_domain_not_routed():
    result = Classifier().classify("What's the weather like in Pune?")
    assert result["domain"] == "unknown"
    assert result["band"] == "low"
    assert "clarification" in result


def test_medium_band_needs_verification_not_silent_route():
    result = Classifier().classify("Work on ticket PROJ-123")
    assert result["domain"] == "dev"
    assert result["band"] == "medium"
    assert result["needs_verification"] is True


def test_dev_question_high():
    result = Classifier().classify("Write tests for the payment service")
    assert result["domain"] == "dev"
    assert result["band"] == "high"


def test_disputes_question_high():
    result = Classifier().classify("Find similar past disputes about duplicate charges")
    assert result["domain"] == "disputes"
    assert result["band"] == "high"
