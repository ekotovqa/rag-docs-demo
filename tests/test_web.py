import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ragdemo.answer import Answer, Citation
from ragdemo.store import Hit
from ragdemo.web import app as web


class FakeStore:
    def stats(self):
        return {"documents": 2, "pages": 3, "ocr_pages": 1, "chunks": 5}

    def files(self):
        return [{"file": "A.pdf", "pages": 2, "ocr_pages": 0, "ingested_at": None}, {"file": "B.pdf", "pages": 1, "ocr_pages": 1, "ingested_at": None}]


class FakeAnswerer:
    def __init__(self, cost=0.01):
        self.cost, self.questions = cost, []

    def ask(self, question, k=None):
        self.questions.append((question, k))
        hit = Hit(1, "A.pdf", 2, 0, "TOTAL DUE 1,440.00 USD", "text", 0.03, 1, 0.1, 1, 3.4)
        return Answer(question, "1,440.00 USD[1]", False, [Citation(1, "A.pdf", 2, "TOTAL DUE 1,440.00 USD", 1)],
                      "claude-opus-5-5", "claude-opus-5-5", "end_turn", 3000, 40, 0, 0, self.cost, 1800.0, 95.0, [hit], "1,440.00 USD")


@pytest.fixture
def client(monkeypatch):
    st = web.State()
    st.store, st.answerer, st.ready = FakeStore(), FakeAnswerer(), True
    st.guard = web.Guard(web.Limits(per_minute=3, per_visitor_day=10, global_per_minute=100, max_inflight=2,
                                    daily_usd=0.025, monthly_usd=1.0))
    monkeypatch.setattr(web, "state", st)
    with TestClient(web.create_app(boot=False)) as c:
        yield c, st


def test_index_and_health(client):
    c, _ = client
    assert "<title>rag-docs-demo</title>" in c.get("/").text
    h = c.get("/api/health").json()
    assert h["status"] == "ok" and h["chunks"] == 5


def test_ask_returns_answer_citations_hits_and_budget(client):
    c, st = client
    r = c.post("/api/ask", json={"question": "What is the total due on invoice A?"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["answer"] == "1,440.00 USD[1]" and j["refused"] is False
    assert j["citations"][0] == {"n": 1, "file": "A.pdf", "page": 2, "quote": "TOTAL DUE 1,440.00 USD", "chunk_id": 1}
    assert j["hits"][0]["file"] == "A.pdf" and j["hits"][0]["lex_score"] == 3.4
    assert j["cost_usd"] == 0.01 and j["spent_today_usd"] == 0.01 and j["daily_budget_usd"] == 0.025
    assert j["questions_left_today"] == 9 and j["questions_per_visitor_day"] == 10
    assert st.answerer.questions == [("What is the total due on invoice A?", None)]


def test_no_model_still_returns_the_retrieved_chunks(client):
    from ragdemo.retrieve import Retrieval

    c, st = client

    class NoKey:
        def ask(self, question, k=None):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    class FakeRetriever:
        def search(self, question, k=None):
            return Retrieval(question, [Hit(7, "B.pdf", 1, 0, "chunk", "ocr", 0.02, 1, 0.3, None, None)], [], 5.0, 50.0)

    st.answerer, st.retriever = NoKey(), FakeRetriever()
    j = c.post("/api/ask", json={"question": "anything about B"}).json()
    assert j["error"] == "ANTHROPIC_API_KEY is not set" and j["refused"] and j["citations"] == []
    assert j["hits"][0]["file"] == "B.pdf" and j["retrieval_ms"] == 55.0 and j["spent_today_usd"] == 0


def test_health_is_readable_from_the_front_door_origin_only(client):
    c, _ = client
    r = c.get("/api/health", headers={"Origin": "https://ekotovqa.github.io"})
    assert r.headers.get("access-control-allow-origin") == "https://ekotovqa.github.io"
    r = c.get("/api/health", headers={"Origin": "http://localhost:8765"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8765", "a local copy of the door"
    r = c.get("/api/health", headers={"Origin": "https://somewhere.example"})
    assert "access-control-allow-origin" not in r.headers


def test_question_validation(client):
    c, _ = client
    assert c.post("/api/ask", json={"question": "hi"}).status_code == 422
    assert c.post("/api/ask", json={"question": "x" * 301}).status_code == 422
    assert c.post("/api/ask", json={"question": "fine question", "k": 99}).status_code == 422


def test_rate_limit_and_daily_budget(client):
    c, _ = client
    codes = [c.post("/api/ask", json={"question": "question number %d" % i}).status_code for i in range(4)]
    # three calls spend $0.03 > $0.025 budget: the third succeeds (checked before spending), the fourth is refused on budget
    assert codes == [200, 200, 200, 429]
    assert "budget" in c.post("/api/ask", json={"question": "one more"}).json()["detail"]


def limits(**over):
    base = dict(per_minute=100, per_visitor_day=100, global_per_minute=1000, max_inflight=100, daily_usd=100, monthly_usd=1000)
    base.update(over)
    return web.Limits(**base)


def test_rate_limit_per_minute():
    g = web.Guard(limits(per_minute=2))
    g.admit("1.1.1.1"); g.admit("1.1.1.1")
    with pytest.raises(web.HTTPException) as exc:
        g.admit("1.1.1.1")
    assert exc.value.status_code == 429 and "per minute" in exc.value.detail
    assert exc.value.headers["Retry-After"] == "60"
    g.admit("2.2.2.2")  # another client is not affected


def test_visitor_daily_quota_is_the_demo_mode(client):
    """A visitor gets a few questions a day; the count is per client address, refusals do not burn it."""
    c, st = client
    st.guard = web.Guard(limits(per_visitor_day=2))
    web.state.guard = st.guard
    first = c.post("/api/ask", json={"question": "first question"}).json()
    second = c.post("/api/ask", json={"question": "second question"}).json()
    assert (first["questions_left_today"], second["questions_left_today"]) == (1, 0)
    third = c.post("/api/ask", json={"question": "third question"})
    assert third.status_code == 429 and "questions a day" in third.json()["detail"]
    assert "Retry-After" not in third.headers
    # the ingress puts the real client last in X-Forwarded-For; a spoofed first entry changes nothing
    spoof = c.post("/api/ask", json={"question": "fourth question"}, headers={"X-Forwarded-For": "9.9.9.9, testclient"})
    assert spoof.status_code == 429
    other = c.post("/api/ask", json={"question": "fifth question"}, headers={"X-Forwarded-For": "203.0.113.7"})
    assert other.status_code == 200 and other.json()["questions_left_today"] == 1


def test_everyone_together_is_limited_too():
    g = web.Guard(limits(global_per_minute=3))
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        g.admit(ip)
    with pytest.raises(web.HTTPException) as exc:
        g.admit("4.4.4.4")
    assert exc.value.status_code == 429 and "busy" in exc.value.detail


def test_questions_in_flight_are_capped():
    g = web.Guard(limits(max_inflight=2))
    g.admit("1.1.1.1"); g.admit("2.2.2.2")
    with pytest.raises(web.HTTPException) as exc:
        g.admit("3.3.3.3")
    assert exc.value.status_code == 503 and exc.value.headers["Retry-After"] == "5"
    g.release(0.01)
    g.admit("3.3.3.3")  # a slot came free


def test_spend_survives_a_restart_and_the_month_has_a_ceiling():
    ledger = web.MemoryLedger()
    g = web.Guard(limits(daily_usd=0.05, monthly_usd=0.08), ledger)
    for _ in range(3):
        g.admit("1.1.1.1"); g.release(0.02)
    assert g.today_usd == pytest.approx(0.06)
    with pytest.raises(web.HTTPException) as exc:
        g.admit("1.1.1.1")
    assert "daily budget" in exc.value.detail
    restarted = web.Guard(limits(daily_usd=0.05, monthly_usd=0.08), ledger)  # a cold start: memory is gone
    with pytest.raises(web.HTTPException):
        restarted.admit("1.1.1.1")  # ...but the ledger is not
    assert restarted.today_usd == pytest.approx(0.06)
    # a new day within the same month: the daily ceiling resets, the monthly one does not
    tomorrow = web.Guard(limits(daily_usd=0.05, monthly_usd=0.08), ledger, clock=lambda: time.time() + 86400)
    tomorrow.admit("1.1.1.1"); tomorrow.release(0.02)
    assert tomorrow.today_usd == pytest.approx(0.02) and tomorrow.month_usd == pytest.approx(0.08)
    with pytest.raises(web.HTTPException) as exc:
        tomorrow.admit("1.1.1.1")
    assert "monthly budget" in exc.value.detail


def test_visitor_quota_resets_with_the_day():
    now = [time.time()]
    g = web.Guard(limits(per_visitor_day=1), clock=lambda: now[0])
    g.admit("1.1.1.1"); g.release(None)
    with pytest.raises(web.HTTPException):
        g.admit("1.1.1.1")
    now[0] += 86400
    g.admit("1.1.1.1")


def test_ledger_failure_does_not_open_the_gate():
    class Broken:
        def spend_load(self, day):
            raise RuntimeError("db down")

        def spend_add(self, day, usd):
            raise RuntimeError("db down")

    g = web.Guard(limits(daily_usd=0.03), Broken())
    g.admit("1.1.1.1"); g.release(0.02)
    g.admit("1.1.1.1"); g.release(0.02)
    with pytest.raises(web.HTTPException):
        g.admit("1.1.1.1")  # the in-memory total still counts


def test_oversized_bodies_are_refused_before_parsing(client):
    c, _ = client
    r = c.post("/api/ask", content=b'{"question": "' + b"x" * 5000 + b'"}', headers={"content-type": "application/json"})
    assert r.status_code == 413 and "4096" in r.json()["detail"]


def test_not_ready_and_failed_startup(monkeypatch):
    st = web.State()
    monkeypatch.setattr(web, "state", st)
    with TestClient(web.create_app(boot=False)) as c:
        assert c.get("/api/health").json()["status"] == "starting"
        assert c.post("/api/ask", json={"question": "anything at all"}).status_code == 503
        st.error = "boom"
        assert c.get("/api/health").status_code == 503
        assert "boom" in c.post("/api/ask", json={"question": "anything at all"}).json()["detail"]


def test_documents_and_files(client, tmp_path, monkeypatch):
    c, _ = client
    assert c.get("/api/documents").json()[1] == {"file": "B.pdf", "pages": 1, "ocr_pages": 1}
    (tmp_path / "ok.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(web, "settings", lambda: SimpleNamespace(ragdemo_corpus_dir=tmp_path, ragdemo_eval_dir=tmp_path))
    assert c.get("/files/ok.pdf").status_code == 200
    assert c.get("/files/missing.pdf").status_code == 404
    assert c.get("/files/..%2Fok.pdf").status_code == 404
    assert c.get("/api/eval").text == "No eval has been run yet."
    assert c.get("/api/examples").json() == []
