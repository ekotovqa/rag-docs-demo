"""Retrieved chunks -> Claude -> an answer with page-level citations, or an explicit refusal.

Each chunk goes to the model as its own document block with citations enabled, so every
quote the model relies on comes back tied to a document index, and the index maps to a
file and a page. Nothing in the answer is trusted that the model did not cite.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

import anthropic

from .config import settings
from .pricing import cost_usd
from .retrieve import Retrieval, Retriever
from .store import Hit

REFUSAL_TOKEN = "NOT_IN_DOCUMENTS"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = f"""You answer questions from an accounts team about their purchase orders and invoices.

Rules:
- Use only the documents provided in this request. Do not use outside knowledge and do not guess.
- If the documents do not contain the answer, reply with exactly {REFUSAL_TOKEN} followed by one short sentence saying what is missing.
- Otherwise answer in one to three sentences. Quote values exactly as written in the document (numbers, currency, dates) and name the document number you took them from.
- Several documents look alike. Check the document number in the chunk header before you answer, and if two documents could match, say so.
- Some pages were scanned and read by OCR, so characters may be garbled. If a value is unreadable, say that rather than guessing.
"""


@dataclass
class Citation:
    n: int
    file: str
    page: int
    quote: str
    chunk_id: int


@dataclass
class Answer:
    question: str
    answer: str
    refused: bool
    citations: list[Citation]
    model: str
    requested_model: str
    stop_reason: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    latency_ms: float
    retrieval_ms: float
    hits: list[Hit] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["hits"] = [{"file": h.file, "page": h.page, "idx": h.idx, "source": h.source, "score": round(h.score, 5),
                      "vec_rank": h.vec_rank, "lex_rank": h.lex_rank, "lex_score": h.lex_score, "text": h.text}
                     for h in self.hits]
        return d


def document_blocks(hits: list[Hit]) -> list[dict[str, Any]]:
    blocks = []
    for h in hits:
        blocks.append({
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": h.text},
            "title": f"{h.file}, page {h.page}",
            "context": f"Chunk {h.idx} of page {h.page} of {h.file}; text from {'OCR' if h.source == 'ocr' else 'the PDF text layer'}.",
            "citations": {"enabled": True},
        })
    return blocks


def parse_response(content: list[Any], hits: list[Hit]) -> tuple[str, list[Citation], str]:
    """Join the text blocks, number the citations, map document indexes back to file and page.

    Returns (display text with [n] markers, citations, raw text without markers).
    """
    parts: list[str] = []
    raw: list[str] = []
    citations: list[Citation] = []
    seen: dict[tuple[str, int, str], int] = {}
    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        text = block.text
        raw.append(text)
        markers: list[int] = []
        for c in getattr(block, "citations", None) or []:
            index = getattr(c, "document_index", None)
            if index is None or index >= len(hits):
                continue
            hit = hits[index]
            quote = " ".join((getattr(c, "cited_text", "") or "").split())
            key = (hit.file, hit.page, quote)
            if key not in seen:
                seen[key] = len(citations) + 1
                citations.append(Citation(seen[key], hit.file, hit.page, quote, hit.id))
            if seen[key] not in markers:
                markers.append(seen[key])
        parts.append(text + ("".join(f"[{n}]" for n in markers) if markers else ""))
    return "".join(parts).strip(), citations, "".join(raw).strip()


def is_refusal(raw_text: str) -> bool:
    return raw_text.lstrip().startswith(REFUSAL_TOKEN)


def display_text(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith(REFUSAL_TOKEN):
        rest = stripped[len(REFUSAL_TOKEN):].lstrip(" :.-")
        return "Not in the documents. " + rest if rest else "Not in the documents."
    return text


class Answerer:
    def __init__(self, retriever: Retriever, client: Any | None = None, model: str | None = None,
                 effort: str | None = None, fallbacks: bool | None = None, max_tokens: int = 1024) -> None:
        s = settings()
        self.retriever = retriever
        self.model = model or s.ragdemo_model
        self.effort = effort or s.ragdemo_effort
        self.fallbacks = s.ragdemo_fallbacks if fallbacks is None else fallbacks
        self.max_tokens = max_tokens
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            key = settings().anthropic_api_key
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set: put it in .env (see .env.example)")
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def request(self, question: str, hits: list[Hit]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": [*document_blocks(hits), {"type": "text", "text": f"Question: {question}"}]}],
            "output_config": {"effort": self.effort},
        }
        if self.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def ask(self, question: str, k: int | None = None) -> Answer:
        retrieval: Retrieval = self.retriever.search(question, k=k)
        hits = retrieval.hits
        if not hits:
            return Answer(question, "Not in the documents. Nothing in the index matched the question.", True, [],
                          self.model, self.model, "no_retrieval", 0, 0, 0, 0, 0.0, 0.0,
                          retrieval.embed_ms + retrieval.search_ms, [], REFUSAL_TOKEN)
        t0 = time.perf_counter()
        response = self.client.beta.messages.create(**self.request(question, hits))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self.build(question, response, hits, latency_ms, retrieval.embed_ms + retrieval.search_ms)

    def build(self, question: str, response: Any, hits: list[Hit], latency_ms: float, retrieval_ms: float) -> Answer:
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        served_by = getattr(response, "model", self.model) or self.model
        stop_reason = getattr(response, "stop_reason", "") or ""
        if stop_reason == "refusal":
            text, citations, raw = "The model declined to answer this request.", [], ""
            refused = True
        else:
            text, citations, raw = parse_response(response.content, hits)
            refused = is_refusal(raw)
            text = display_text(text)
            if stop_reason == "max_tokens":
                text += " [answer cut off at the token limit]"
        return Answer(
            question=question, answer=text, refused=refused, citations=citations,
            model=served_by, requested_model=self.model, stop_reason=stop_reason,
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read, cache_write_tokens=cache_write,
            cost_usd=cost_usd(served_by, usage.input_tokens, usage.output_tokens, cache_read, cache_write),
            latency_ms=latency_ms, retrieval_ms=retrieval_ms, hits=hits, raw_text=raw,
        )
