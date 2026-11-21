import os
import sys

os.environ.setdefault("TAVILY_API_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


def test_score_current_high_confidence():
    assert main.freshness_score("current", 1.0) == 100


def test_score_outdated_is_low():
    assert main.freshness_score("outdated", 0.9) <= 5


def test_score_unverifiable_is_middle():
    assert main.freshness_score("unverifiable", 0.0) == 40


def test_score_clamps_bad_confidence():
    assert main.freshness_score("current", 5) == 100


def test_check_endpoint_with_mocks(monkeypatch):
    monkeypatch.setattr(main, "extract_claims", lambda t, n: [
        {"claim": "X costs $5", "type": "price", "volatility": "high", "search_query": "X price"}])
    monkeypatch.setattr(main, "gather_evidence", lambda c: [
        {"title": "t", "url": "https://example.com", "content": "X costs $7", "published": None}])
    monkeypatch.setattr(main, "judge", lambda c, e: {
        "verdict": "outdated", "confidence": 0.9, "corrected_fact": "X costs $7",
        "best_source_url": "https://example.com", "reason": "price changed"})
    res = TestClient(main.app).post("/check", json={"text": "X costs $5"}).json()
    claim = res["claims"][0]
    assert claim["verdict"] == "outdated"
    assert claim["score"] < 10
    assert claim["sources"] == ["https://example.com"]


def test_failure_becomes_unverifiable(monkeypatch):
    def boom(c):
        raise RuntimeError("tavily down")
    monkeypatch.setattr(main, "gather_evidence", boom)
    result = main.check_claim({"claim": "c", "search_query": "q"})
    assert result["verdict"] == "unverifiable"
    assert "tavily down" in result["reason"]


CLAIM = {"claim": "X is latest", "type": "version", "volatility": "high", "search_query": "x"}


def _mock_pipeline(monkeypatch, verdict="outdated"):
    monkeypatch.setattr(main, "extract_claims", lambda t, n: [dict(CLAIM)])
    monkeypatch.setattr(main, "gather_evidence", lambda c: [
        {"title": "t", "url": "https://example.com", "content": "c", "published": None}])
    monkeypatch.setattr(main, "judge", lambda c, e: {
        "verdict": verdict, "confidence": 0.9, "corrected_fact": "Y is latest",
        "best_source_url": "https://example.com", "reason": "r"})


def test_run_check_keeps_claim_order(monkeypatch):
    claims = [dict(CLAIM, claim=f"claim {i}") for i in range(5)]
    monkeypatch.setattr(main, "extract_claims", lambda t, n: claims)
    monkeypatch.setattr(main, "check_claim", lambda c: {**c, "score": 1})
    result = main.run_check("text")
    assert [c["claim"] for c in result["claims"]] == [f"claim {i}" for i in range(5)]


def test_fix_answer_unchanged_when_nothing_stale():
    claims = [{"claim": "a", "verdict": "current", "corrected_fact": None}]
    assert main.fix_answer("q", "original answer", claims) == "original answer"


def test_ask_endpoint_rewrites_stale_answer(monkeypatch):
    _mock_pipeline(monkeypatch)
    monkeypatch.setattr(main, "answer_question", lambda q: "X is latest")
    monkeypatch.setattr(main, "fix_answer", lambda q, a, c: "Y is latest")
    res = TestClient(main.app).post("/ask", json={"question": "what is latest?"}).json()
    assert res["answer"] == "X is latest"
    assert res["corrected_answer"] == "Y is latest"
    assert res["changed"] is True
    assert res["check"]["claims"][0]["verdict"] == "outdated"


def test_ask_endpoint_unchanged_when_current(monkeypatch):
    _mock_pipeline(monkeypatch, verdict="current")
    monkeypatch.setattr(main, "answer_question", lambda q: "X is latest")
    res = TestClient(main.app).post("/ask", json={"question": "q"}).json()
    assert res["corrected_answer"] == "X is latest"
    assert res["changed"] is False


def test_ask_endpoint_reports_gateway_error(monkeypatch):
    def boom(q):
        raise RuntimeError("bifrost down")
    monkeypatch.setattr(main, "answer_question", boom)
    res = TestClient(main.app).post("/ask", json={"question": "q"}).json()
    assert "bifrost down" in res["error"]
