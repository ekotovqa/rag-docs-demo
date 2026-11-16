from decimal import Decimal
from pathlib import Path

import pytest

from ragdemo.corpus.generate import load_ground_truth
from ragdemo.eval.metrics import answer_matches, doc_hit_at, hit_at, numbers_in, parse_number
from ragdemo.eval.questions import Question, build_questions, load_questions, save_questions
from ragdemo.eval.run import markdown_report, summarize


@pytest.mark.parametrize("token,value", [
    ("1,862.00", "1862.00"), ("1.862,00", "1862.00"), ("1 862,00", "1862.00"), ("1862", "1862"),
    ("61.20", "61.20"), ("61,20", "61.20"), ("1,862", "1862"), ("1.862", "1862"), ("12,345,678.90", "12345678.90"),
    ("3082.98", "3082.98"), ("10", "10"),
])
def test_parse_number(token, value):
    assert parse_number(token) == Decimal(value)


def test_numbers_in_text():
    found = numbers_in("Invoice KS-RE-25-0318 totals 1.862,00 EUR; 10 sheets at 61.20 each, due 28 Mar 2025.")
    assert {Decimal("1862.00"), Decimal("10"), Decimal("61.20"), Decimal("28"), Decimal("2025")} <= found


def test_answer_matches_by_type():
    assert answer_matches("The total due is $1,440.00.", ["1440.00"], "number")
    assert answer_matches("Total: 1.440,00 EUR", ["1440.00"], "number")
    assert not answer_matches("The total due is $1,441.00.", ["1440.00"], "number")
    assert answer_matches("Issued by Brightwater Fasteners Inc. on ...", ["Brightwater Fasteners Inc.", "Brightwater Fasteners"], "text")
    assert answer_matches("Due on 28 Mar 2025.", ["2025-03-28", "28 Mar 2025"], "date")
    assert not answer_matches("Due on 29 Mar 2025.", ["2025-03-28", "28 Mar 2025"], "date")


def test_hits_at_k():
    pages = [("a.pdf", 1), ("b.pdf", 2), ("a.pdf", 2), ("c.pdf", 1)]
    gold = [("a.pdf", 2)]
    assert not hit_at(pages, gold, 1) and not hit_at(pages, gold, 2) and hit_at(pages, gold, 3)
    assert doc_hit_at(pages, gold, 1), "the document is right even when the page is not"
    assert not doc_hit_at(pages, [("z.pdf", 1)], 5)


CORPUS = Path(__file__).resolve().parents[1] / "data" / "corpus"


def test_questions_cover_kinds_and_pages(tmp_path):
    docs = load_ground_truth(CORPUS)  # rendered: page numbers are known
    qs = build_questions(docs)
    kinds = {q.kind for q in qs}
    assert {"total", "vendor", "due_date", "qty", "unit_price", "po_ref", "terms", "unanswerable", "cross_vendor"} <= kinds
    assert 60 <= len(qs) <= 110
    assert sum(not q.answerable for q in qs) >= 8
    files = {d.file for d in docs}
    pages = {(d.file, p) for d in docs for p in range(1, d.pages + 1)}
    for q in qs:
        if q.answerable:
            assert q.expected and q.gold, q.id
            assert all(g in pages for g in q.gold), (q.id, q.gold)
        else:
            assert q.expected == [] and q.expected_type == "none"
        assert all(f in files for f, _ in q.gold)
    second_page = [q for q in qs if q.answerable and all(p > 1 for _, p in q.gold)]
    assert len(second_page) >= 5, "some answers live only on a second page"
    assert any(q.scan for q in qs), "scanned documents are asked about"
    assert build_questions(docs) == qs, "deterministic"
    save_questions(qs, tmp_path / "q.json")
    assert load_questions(tmp_path / "q.json") == qs


def test_expected_values_match_ground_truth():
    docs = load_ground_truth(CORPUS)
    by_file = {d.file: d for d in docs}
    for q in build_questions(docs):
        if q.kind == "total":
            d = by_file[q.gold[0][0]]
            assert q.expected == [f"{d.total:.2f}"] and q.gold == [(d.file, d.totals_page)]
        if q.kind == "qty":
            d = by_file[q.gold[0][0]]
            assert any(str(line.qty) == q.expected[0] and line.page == q.gold[0][1] for line in d.lines)


def test_summary_and_report_shape():
    rows = [
        {"id": "q1", "kind": "total", "answerable": True, "scan": False, "multi_doc": False, "question": "a",
         "page_hit@1": True, "page_hit@3": True, "page_hit@5": True, "doc_hit@1": True, "doc_hit@3": True, "doc_hit@5": True,
         "retrieval_ms": 100, "correct": True, "false_refusal": False, "refused": False, "grounded": True,
         "citation_on_gold": True, "latency_ms": 2000, "cost_usd": 0.01, "input_tokens": 3000, "cache_read_tokens": 0, "model": "m"},
        {"id": "q2", "kind": "unanswerable", "answerable": False, "scan": True, "multi_doc": False, "question": "b",
         "retrieval_ms": 120, "correct": True, "refused": True, "grounded": False, "citation_on_gold": None,
         "latency_ms": 1500, "cost_usd": 0.008, "input_tokens": 2500, "cache_read_tokens": 0, "model": "m"},
    ]
    s = summarize(rows, with_answers=True)
    assert s["page_recall@3"] == 1.0 and s["answer_accuracy"] == 1.0 and s["correct_refusal_rate"] == 1.0
    assert s["grounded_rate"] == 1.0 and s["cost_usd_total"] == pytest.approx(0.018)
    report = markdown_report(s, True, "now")
    assert "| page recall@3 | 100% |" in report and "| cost of the whole run | $0.02 |" in report
    s2 = summarize([{k: v for k, v in rows[0].items()}], with_answers=False)
    assert "answer_accuracy" not in s2
    assert "## Answers" not in markdown_report(s2, False, "now")
