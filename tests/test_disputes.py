"""Disputes slice: precedent citations, no-precedent honesty, PII masking."""

import pytest

from app import disputes


def test_resolve_wrong_amount_cites_precedents(db_path, tmp_path):
    result = disputes.resolve_dispute(1, db_path=db_path,
                                      audit_path=str(tmp_path / "audit.log"))
    assert result["reason_cluster"] == "wrong amount"
    assert len(result["precedents"]) >= 1
    assert result["citations"] == [p["id"] for p in result["precedents"]]
    assert all(p["reason"] == "wrong amount" for p in result["precedents"])
    assert result["citations"][0] in result["draft_resolution"]
    assert result["precedents"][0]["resolution"] in result["draft_resolution"]
    assert result["human_review"] is False


def test_resolve_duplicate_charge_cites_precedents(db_path, tmp_path):
    result = disputes.resolve_dispute(8, db_path=db_path,
                                      audit_path=str(tmp_path / "audit.log"))
    assert result["reason_cluster"] == "duplicate billing"
    assert result["precedents"]
    assert result["human_review"] is False


def test_resolve_no_precedent_routes_to_human(db_path, tmp_path):
    # Dispute 2 (unauthorised transaction) has no precedent in the corpus.
    result = disputes.resolve_dispute(2, db_path=db_path,
                                      audit_path=str(tmp_path / "audit.log"))
    assert result["precedents"] == []
    assert result["citations"] == []
    assert result["human_review"] is True
    assert "human review" in result["draft_resolution"]


def test_resolve_unknown_dispute_raises(db_path, tmp_path):
    with pytest.raises(KeyError):
        disputes.resolve_dispute(999, db_path=db_path,
                                 audit_path=str(tmp_path / "audit.log"))


def test_pii_masked_in_dispute_output(db_path):
    dispute = disputes.load_dispute(1, db_path=db_path)
    assert dispute["customer"]["email"] == "[EMAIL]"
    assert dispute["customer"]["phone"] == "[PHONE]"
    assert dispute["customer"]["name"] == "Divya Menon"


def test_reason_clusters_sum_to_live_disputes(db_path):
    clusters = disputes.reason_clusters(db_path=db_path)
    assert sum(clusters.values()) == 10
    assert clusters["refund delay"] == 6
    assert clusters["duplicate billing"] == 1


def test_list_disputes_all_have_status_and_reason(db_path):
    listed = disputes.list_disputes(db_path=db_path)
    assert len(listed) == 10
    assert all(d["status"] in ("open", "resolved") and d["reason"] for d in listed)


def test_match_dispute_by_explicit_id(db_path):
    matched = disputes.match_dispute("Draft a resolution for dispute DSP-7", db_path=db_path)
    assert matched and matched["id"] == 7
