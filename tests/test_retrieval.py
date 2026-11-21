from app.retrieval import Doc, get_retriever

DOCS = [
    Doc(id="pq-0", text="What were total refunds in Mumbai last month? "
         "SELECT SUM(r.amount_paise) FROM refunds r JOIN customers c",
        meta={"domain": "analytics"}),
    Doc(id="pq-1", text="Show me revenue by city "
         "SELECT c.city, SUM(p.amount_paise) FROM payments p",
        meta={"domain": "analytics"}),
    Doc(id="pq-2", text="Why was this invoice disputed? duplicate charge",
        meta={"domain": "disputes"}),
]


def test_retrieval_grounds_in_right_doc():
    retriever = get_retriever(DOCS)
    top = retriever.retrieve("total refunds in Mumbai", k=1)
    assert len(top) == 1
    assert top[0].doc.id == "pq-0"


def test_retrieval_revenue_by_city():
    retriever = get_retriever(DOCS)
    top = retriever.retrieve("revenue by city", k=1)
    assert top[0].doc.id == "pq-1"


def test_retrieval_empty_query():
    assert get_retriever(DOCS).retrieve("", k=3) == []


def test_retrieval_no_match():
    assert get_retriever(DOCS).retrieve("quantum entanglement", k=3) == []
