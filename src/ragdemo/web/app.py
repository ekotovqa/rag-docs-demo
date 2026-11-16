"""One page and a small JSON API. The page shows the answer, its citations, the retrieved chunks, the cost and the time."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..config import settings

log = logging.getLogger("ragdemo.web")
STATIC = Path(__file__).parent / "static"

MAX_QUESTION_CHARS = 300
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RAGDEMO_RATE_LIMIT_PER_MINUTE", "10"))
DAILY_BUDGET_USD = float(os.environ.get("RAGDEMO_DAILY_BUDGET_USD", "2.0"))
AUTO_INGEST = os.environ.get("RAGDEMO_AUTO_INGEST", "").lower() in ("1", "true", "yes")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=MAX_QUESTION_CHARS)
    k: int | None = Field(default=None, ge=1, le=12)


class Budget:
    """In-memory spend guard for a public demo: per-IP rate limit and a daily cost ceiling."""

    def __init__(self, per_minute: int, daily_usd: float) -> None:
        self.per_minute, self.daily_usd = per_minute, daily_usd
        self.spent_usd = 0.0
        self.day = time.strftime("%Y-%m-%d")
        self.calls: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, ip: str) -> None:
        with self.lock:
            today = time.strftime("%Y-%m-%d")
            if today != self.day:
                self.day, self.spent_usd = today, 0.0
            if self.spent_usd >= self.daily_usd:
                raise HTTPException(429, "The demo's daily budget is used up; try again tomorrow.")
            now = time.monotonic()
            q = self.calls[ip]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                raise HTTPException(429, f"At most {self.per_minute} questions per minute, please.")
            q.append(now)

    def add(self, cost: float | None) -> None:
        with self.lock:
            self.spent_usd += cost or 0.0


class State:
    def __init__(self) -> None:
        self.store = None
        self.retriever = None
        self.answerer = None
        self.budget = Budget(RATE_LIMIT_PER_MINUTE, DAILY_BUDGET_USD)
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
    body: dict[str, Any] = {"status": "error" if state.error else ("ok" if state.ready else "starting"), "error": state.error}
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
    ip = request.client.host if request.client else "?"
    st.budget.check(ip)
    try:
        answer = st.answerer.ask(req.question.strip(), k=req.k)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    st.budget.add(answer.cost_usd)
    out = answer.to_dict()
    out["spent_today_usd"] = round(st.budget.spent_usd, 4)
    out["daily_budget_usd"] = st.budget.daily_usd
    return out


def create_app(boot: bool = True) -> FastAPI:
    """`boot=False` skips loading models and the database: tests use it with a fake state."""
    application = FastAPI(title="rag-docs-demo", lifespan=lifespan if boot else None)
    application.include_router(router)
    return application


app = create_app()
