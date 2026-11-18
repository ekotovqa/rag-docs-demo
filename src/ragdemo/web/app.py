"""One page and a small JSON API. The page shows the answer, its citations, the retrieved chunks, the cost and the time."""
from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..config import settings
from .guard import BodyLimit, Guard, Limits, MemoryLedger, client_ip, limits_from_env

__all__ = ["app", "create_app", "state", "State", "Guard", "Limits", "MemoryLedger", "HTTPException"]

log = logging.getLogger("ragdemo.web")
STATIC = Path(__file__).parent / "static"

MAX_QUESTION_CHARS = 300
MAX_BODY_BYTES = 4096  # a question is 300 characters; anything larger is not a question
AUTO_INGEST = os.environ.get("RAGDEMO_AUTO_INGEST", "").lower() in ("1", "true", "yes")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=MAX_QUESTION_CHARS)
    k: int | None = Field(default=None, ge=1, le=8)  # more chunks cost more; the page never asks for them


class State:
    def __init__(self) -> None:
        self.store = None
        self.retriever = None
        self.answerer = None
        self.guard = Guard(limits_from_env())
        self.ready = False
        self.error: str | None = None

    def load(self) -> None:
        from ..answer import Answerer
        from ..embed import Embedder
        from ..retrieve import Retriever
        from ..store import Store

        s = settings()
        self.store = Store(s.database_url)
        self.store.init_schema()
        self.guard.attach(self.store)  # today's and this month's spend come from the database, not from memory
        embedder = Embedder(s.ragdemo_embed_model, s.ragdemo_model_cache)
        self.retriever = Retriever(self.store, embedder, s.ragdemo_top_k)
        self.answerer = Answerer(self.retriever)
        if AUTO_INGEST and s.ragdemo_corpus_dir.is_dir():
            from ..ingest.ocr import Ocr
            from ..ingest.pipeline import ingest_dir

            report = ingest_dir(s.ragdemo_corpus_dir, self.store, embedder, Ocr(), log=log.info)
            log.info("auto-ingest: %d new, %d unchanged, %.0fs", len(report.ingested), len(report.skipped), report.seconds)
        self.ready = True


state = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    def boot() -> None:
        try:
            state.load()
        except Exception as exc:  # pragma: no cover - surfaced through /api/health
            state.error = f"{type(exc).__name__}: {exc}"
            log.exception("startup failed")

    threading.Thread(target=boot, name="ragdemo-boot", daemon=True).start()
    yield


router = APIRouter()


def require_ready() -> State:
    if state.error:
        raise HTTPException(503, f"startup failed: {state.error}")
    if not state.ready:
        raise HTTPException(503, "warming up: loading the embedding model and checking the index")
    return state


@router.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@router.get("/api/health")
def health() -> JSONResponse:
    body: dict[str, Any] = {"status": "error" if state.error else ("ok" if state.ready else "starting"), "error": state.error,
                            "questions_per_visitor_day": state.guard.limits.per_visitor_day}
    if state.ready and state.store is not None:
        body.update(state.store.stats())
    return JSONResponse(body, status_code=200 if not state.error else 503)


@router.get("/api/documents")
def documents() -> list[dict[str, Any]]:
    st = require_ready()
    return [{"file": r["file"], "pages": r["pages"], "ocr_pages": r["ocr_pages"]} for r in st.store.files()]


@router.get("/api/examples")
def examples() -> list[str]:
    path = settings().ragdemo_eval_dir / "questions.json"
    if not path.exists():
        return []
    qs = json.loads(path.read_text(encoding="utf-8"))
    picks: list[str] = []
    wanted = ["total", "qty", "due_date", "cross_vendor", "iban", "unanswerable"]
    for kind in wanted:
        for q in qs:
            if q["kind"] == kind and (kind != "total" or q["scan"]):
                picks.append(q["question"])
                break
    return picks


@router.get("/api/eval", response_class=PlainTextResponse)
def eval_report() -> str:
    d = settings().ragdemo_eval_dir
    parts = [p.read_text(encoding="utf-8") for p in (d / "latest-full.md", d / "latest-retrieval.md") if p.exists()]
    return "\n\n".join(parts) if parts else "No eval has been run yet."


@router.get("/files/{name}")
def pdf(name: str) -> FileResponse:
    if "/" in name or "\\" in name or not name.lower().endswith(".pdf"):
        raise HTTPException(404)
    path = settings().ragdemo_corpus_dir / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/pdf", filename=name, content_disposition_type="inline")


@router.post("/api/ask")
def ask(req: AskRequest, request: Request) -> dict[str, Any]:
    st = require_ready()
    ip = client_ip(request)
    st.guard.admit(ip)  # 429 / 503 with the reason when the visitor, the demo or the budget is at its limit
    cost: float | None = None
    try:
        question = req.question.strip()
        try:
            answer = st.answerer.ask(question, k=req.k)
        except RuntimeError as exc:
            # No model (no key): retrieval still works, and the page shows what would have gone to the model.
            retrieval = st.retriever.search(question, k=req.k)
            out = retrieval_only_payload(question, retrieval, str(exc))
        else:
            cost = answer.cost_usd
            out = answer.to_dict()
            out["error"] = None
    finally:
        st.guard.release(cost)
    out["spent_today_usd"] = round(st.guard.today_usd, 4)
    out["daily_budget_usd"] = st.guard.limits.daily_usd
    out["questions_left_today"] = st.guard.left_today(ip)
    out["questions_per_visitor_day"] = st.guard.limits.per_visitor_day
    return out


def retrieval_only_payload(question: str, retrieval: Any, error: str) -> dict[str, Any]:
    from ..answer import Answer

    stub = Answer(question, "", True, [], "", "", "no_model", 0, 0, 0, 0, None, 0.0,
                  retrieval.embed_ms + retrieval.search_ms, retrieval.hits, "")
    out = stub.to_dict()
    out["error"] = error
    return out


def create_app(boot: bool = True) -> FastAPI:
    """`boot=False` skips loading models and the database: tests use it with a fake state."""
    application = FastAPI(title="rag-docs-demo", lifespan=lifespan if boot else None)
    # The front door (a static page that wakes the app and shows progress) reads /api/health from
    # another origin; a local copy of it runs on localhost. Reads only: asking stays same-origin.
    origins = [o.strip() for o in settings().ragdemo_cors_origins.split(",") if o.strip()]
    application.add_middleware(CORSMiddleware, allow_origins=origins,
                               allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
                               allow_methods=["GET"], max_age=600)
    application.add_middleware(BodyLimit, max_bytes=MAX_BODY_BYTES)
    application.include_router(router)
    return application


app = create_app()
