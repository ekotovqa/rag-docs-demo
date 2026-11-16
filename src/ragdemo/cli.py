"""`ragdemo` command line: generate | ingest | search | ask | eval | serve."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import settings


def _store():
    from .store import Store

    return Store(settings().database_url)


def _embedder():
    from .embed import Embedder

    s = settings()
    return Embedder(s.ragdemo_embed_model, s.ragdemo_model_cache)


def cmd_generate(args: argparse.Namespace) -> int:
    from .corpus.generate import write_corpus

    docs = write_corpus(Path(args.out), seed=args.seed)
    print(f"{len(docs)} documents -> {args.out} ({sum(d.scan for d in docs)} scans, {sum(d.pages for d in docs)} pages)")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from .ingest.ocr import Ocr
    from .ingest.pipeline import ingest_dir

    report = ingest_dir(Path(args.corpus), _store(), _embedder(), None if args.no_ocr else Ocr(), force=args.force)
    print(f"ingested {len(report.ingested)}, skipped {len(report.skipped)}, removed {report.removed}; "
          f"{report.pages} pages ({report.ocr_pages} OCR), {report.chunks} chunks, {report.seconds:.1f}s")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from .retrieve import Retriever

    r = Retriever(_store(), _embedder(), settings().ragdemo_top_k).search(args.question, k=args.k)
    print(f"terms: {r.terms}")
    print(f"embed {r.embed_ms:.0f} ms, search {r.search_ms:.0f} ms")
    for h in r.hits:
        print(f"\n[{h.score:.4f}] {h.file} p{h.page} #{h.idx} ({h.source}) vec={h.vec_rank} lex={h.lex_rank}/{h.lex_score}")
        print("   " + h.text[:300].replace("\n", "\n   "))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .answer import Answerer
    from .retrieve import Retriever

    answerer = Answerer(Retriever(_store(), _embedder(), settings().ragdemo_top_k))
    result = answerer.ask(args.question)
    if args.json:
        print(json.dumps(result.to_dict(), indent=1, ensure_ascii=False))
        return 0
    print(result.answer)
    print()
    for c in result.citations:
        print(f"  [{c.n}] {c.file} p.{c.page}: \"{c.quote[:120]}\"")
    print(f"\n{result.model} | ${result.cost_usd:.4f} | {result.latency_ms:.0f} ms | in {result.input_tokens} out {result.output_tokens}"
          f" | cache read {result.cache_read_tokens}" + (" | REFUSED" if result.refused else ""))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval.run import run_eval

    return run_eval(Path(args.corpus), Path(args.out), retrieval_only=args.retrieval_only, limit=args.limit)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("ragdemo.web.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    s = settings()
    p = argparse.ArgumentParser(prog="ragdemo", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="render the synthetic corpus")
    g.add_argument("--out", default=str(s.ragdemo_corpus_dir))
    g.add_argument("--seed", type=int, default=20251116)
    g.set_defaults(fn=cmd_generate)

    i = sub.add_parser("ingest", help="index the corpus into Postgres")
    i.add_argument("--corpus", default=str(s.ragdemo_corpus_dir))
    i.add_argument("--force", action="store_true", help="re-index unchanged files too")
    i.add_argument("--no-ocr", action="store_true")
    i.set_defaults(fn=cmd_ingest)

    q = sub.add_parser("search", help="retrieval only, no model call")
    q.add_argument("question")
    q.add_argument("-k", type=int, default=None)
    q.set_defaults(fn=cmd_search)

    a = sub.add_parser("ask", help="retrieve and answer with citations")
    a.add_argument("question")
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_ask)

    e = sub.add_parser("eval", help="run the golden questions and write the metrics")
    e.add_argument("--corpus", default=str(s.ragdemo_corpus_dir))
    e.add_argument("--out", default=str(s.ragdemo_eval_dir))
    e.add_argument("--retrieval-only", action="store_true", help="recall@k only, no model calls")
    e.add_argument("--limit", type=int, default=None)
    e.set_defaults(fn=cmd_eval)

    w = sub.add_parser("serve", help="web UI and API")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--reload", action="store_true")
    w.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
