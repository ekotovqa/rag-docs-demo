"""Run the golden questions through retrieval (and, unless --retrieval-only, through the model) and write the metrics."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from ..embed import Embedder
from ..retrieve import Retriever
from ..store import Store
from .metrics import answer_matches, doc_hit_at, hit_at, mean
from .questions import Question, questions_for

KS = (1, 3, 5)


def evaluate_question(q: Question, retriever: Retriever, answerer: Any | None) -> dict[str, Any]:
    row: dict[str, Any] = {"id": q.id, "kind": q.kind, "answerable": q.answerable, "scan": q.scan,
                           "multi_doc": q.multi_doc, "question": q.question}
    retrieval = retriever.search(q.question)
    pages = retrieval.pages
    row["retrieved"] = [list(p) for p in pages]
    if q.answerable:
        for k in KS:
            row[f"page_hit@{k}"] = hit_at(pages, q.gold, k)
            row[f"doc_hit@{k}"] = doc_hit_at(pages, q.gold, k)
    row["retrieval_ms"] = round(retrieval.embed_ms + retrieval.search_ms, 1)
    if answerer is None:
        return row
    t0 = time.perf_counter()
    answer = answerer.ask(q.question)
    row["answer"] = answer.answer
    row["refused"] = answer.refused
    row["citations"] = [[c.file, c.page] for c in answer.citations]
    row["grounded"] = (not answer.refused) and bool(answer.citations)
    row["citation_on_gold"] = any((c.file, c.page) in set(q.gold) for c in answer.citations) if q.gold else None
    if q.answerable:
        row["correct"] = (not answer.refused) and answer_matches(answer.answer, q.expected, q.expected_type)
        row["false_refusal"] = answer.refused
    else:
        row["correct"] = answer.refused
    row["model"] = answer.model
    row["input_tokens"] = answer.input_tokens
    row["output_tokens"] = answer.output_tokens
    row["cache_read_tokens"] = answer.cache_read_tokens
    row["cost_usd"] = answer.cost_usd
    row["latency_ms"] = round(answer.latency_ms, 1)
    row["wall_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return row


def summarize(rows: list[dict[str, Any]], with_answers: bool) -> dict[str, Any]:
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    out: dict[str, Any] = {"questions": len(rows), "answerable": len(answerable), "unanswerable": len(unanswerable)}
    for k in KS:
        out[f"page_recall@{k}"] = mean([float(r[f"page_hit@{k}"]) for r in answerable])
        out[f"doc_recall@{k}"] = mean([float(r[f"doc_hit@{k}"]) for r in answerable])
    for label, subset in (("text", [r for r in answerable if not r["scan"]]), ("scan", [r for r in answerable if r["scan"]])):
        out[f"page_recall@3_{label}"] = mean([float(r["page_hit@3"]) for r in subset])
        out[f"n_{label}"] = len(subset)
    out["retrieval_ms_mean"] = mean([r["retrieval_ms"] for r in rows])
    if with_answers:
        out["answer_accuracy"] = mean([float(r["correct"]) for r in answerable])
        out["answer_accuracy_text"] = mean([float(r["correct"]) for r in answerable if not r["scan"]])
        out["answer_accuracy_scan"] = mean([float(r["correct"]) for r in answerable if r["scan"]])
        out["false_refusal_rate"] = mean([float(r["false_refusal"]) for r in answerable])
        out["correct_refusal_rate"] = mean([float(r["correct"]) for r in unanswerable])
        answered = [r for r in rows if not r["refused"]]
        out["grounded_rate"] = mean([float(r["grounded"]) for r in answered])
        out["citation_on_gold_rate"] = mean([float(r["citation_on_gold"]) for r in answered if r["citation_on_gold"] is not None])
        out["latency_ms_mean"] = mean([r["latency_ms"] for r in rows])
        out["latency_ms_p90"] = sorted(r["latency_ms"] for r in rows)[int(0.9 * (len(rows) - 1))] if rows else 0
        costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
        out["cost_usd_total"] = sum(costs)
        out["cost_usd_mean"] = mean(costs)
        out["input_tokens_mean"] = mean([r["input_tokens"] for r in rows])
        out["cache_read_tokens_total"] = sum(r["cache_read_tokens"] for r in rows)
        out["model"] = sorted({r["model"] for r in rows})
    by_kind: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = by_kind.setdefault(r["kind"], {"n": 0, "page_hit@3": [], "correct": []})
        b["n"] += 1
        if r["answerable"]:
            b["page_hit@3"].append(float(r["page_hit@3"]))
        if with_answers:
            b["correct"].append(float(r["correct"]))
    out["by_kind"] = {k: {"n": v["n"], "page_recall@3": mean(v["page_hit@3"]) if v["page_hit@3"] else None,
                          "accuracy": mean(v["correct"]) if v["correct"] else None} for k, v in by_kind.items()}
    return out


def pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def markdown_report(summary: dict[str, Any], with_answers: bool, when: str) -> str:
    lines = [f"# Eval — {when}", "",
             f"{summary['questions']} questions: {summary['answerable']} answerable, {summary['unanswerable']} unanswerable.", "",
             "## Retrieval", "", "| Metric | Value |", "|---|---|"]
    for k in KS:
        lines.append(f"| page recall@{k} | {pct(summary[f'page_recall@{k}'])} |")
        lines.append(f"| document recall@{k} | {pct(summary[f'doc_recall@{k}'])} |")
    lines.append(f"| page recall@3, text-layer pages (n={summary['n_text']}) | {pct(summary['page_recall@3_text'])} |")
    lines.append(f"| page recall@3, scanned pages (n={summary['n_scan']}) | {pct(summary['page_recall@3_scan'])} |")
    lines.append(f"| retrieval time, mean | {summary['retrieval_ms_mean']:.0f} ms |")
    if with_answers:
        lines += ["", "## Answers", "", "| Metric | Value |", "|---|---|",
                  f"| answer accuracy (answerable) | {pct(summary['answer_accuracy'])} |",
                  f"| accuracy on text-layer documents | {pct(summary['answer_accuracy_text'])} |",
                  f"| accuracy on scanned documents | {pct(summary['answer_accuracy_scan'])} |",
                  f"| correct refusals (unanswerable) | {pct(summary['correct_refusal_rate'])} |",
                  f"| false refusals (answerable) | {pct(summary['false_refusal_rate'])} |",
                  f"| answers with at least one citation | {pct(summary['grounded_rate'])} |",
                  f"| answers citing a gold page | {pct(summary['citation_on_gold_rate'])} |",
                  f"| model latency, mean / p90 | {summary['latency_ms_mean']:.0f} / {summary['latency_ms_p90']:.0f} ms |",
                  f"| cost per question, mean | ${summary['cost_usd_mean']:.4f} |",
                  f"| cost of the whole run | ${summary['cost_usd_total']:.2f} |",
                  f"| input tokens per question, mean | {summary['input_tokens_mean']:.0f} |",
                  f"| model | {', '.join(summary['model'])} |"]
    lines += ["", "## By question kind", "", "| Kind | n | page recall@3 | accuracy |", "|---|---|---|---|"]
    for kind, v in sorted(summary["by_kind"].items()):
        lines.append(f"| {kind} | {v['n']} | {pct(v['page_recall@3'])} | {pct(v['accuracy'])} |")
    return "\n".join(lines) + "\n"


def run_eval(corpus_dir: Path, out_dir: Path, retrieval_only: bool = False, limit: int | None = None) -> int:
    s = settings()
    questions = questions_for(corpus_dir, out_dir)
    if limit:
        questions = questions[:limit]
    retriever = Retriever(Store(s.database_url), Embedder(s.ragdemo_embed_model, s.ragdemo_model_cache), s.ragdemo_top_k)
    answerer = None
    if not retrieval_only:
        from ..answer import Answerer

        answerer = Answerer(retriever)
    rows = []
    for i, q in enumerate(questions, 1):
        row = evaluate_question(q, retriever, answerer)
        rows.append(row)
        flag = "" if retrieval_only else ("  ok" if row["correct"] else "  MISS")
        print(f"[{i:3d}/{len(questions)}] {q.id} {q.kind:14s} hit@3={row.get('page_hit@3', '-')!s:5s}{flag}  {q.question[:70]}")
    summary = summarize(rows, with_answers=not retrieval_only)
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    mode = "retrieval" if retrieval_only else "full"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"results-{mode}-{stamp}.json").write_text(
        json.dumps({"when": when, "mode": mode, "settings": {"model": s.ragdemo_model, "effort": s.ragdemo_effort,
                    "top_k": s.ragdemo_top_k, "embed_model": s.ragdemo_embed_model}, "summary": summary, "rows": rows},
                   indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    report = markdown_report(summary, not retrieval_only, when)
    (out_dir / f"latest-{mode}.md").write_text(report, encoding="utf-8", newline="\n")
    print("\n" + report)
    return 0
