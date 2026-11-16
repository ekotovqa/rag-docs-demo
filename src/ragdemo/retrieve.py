"""Question in, ranked chunks out. Decides what the lexical side should look for and how much each term weighs."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .embed import Embedder
from .store import Hit, Store

# Document numbers, SKUs and the like: INV-2025-0137, NC/2025/0912, KS-RE-25-0318, AE-TB-2.5, SDP0144, 2025-0101.
ID_RE = re.compile(r"\b[A-Za-z]{1,4}[-/][A-Za-z0-9]+(?:[-/][A-Za-z0-9.]+)*\b|\b[A-Za-z]{2,4}\d{3,}\b|\b\d{4}-\d{3,}\b")
NUMBER_RE = re.compile(r"(?<![\w-])\d+(?:[.,]\d+)*(?![\w-])")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z']{2,}")

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "what", "which", "when", "who", "whom", "where", "how",
    "many", "much", "does", "did", "was", "were", "are", "has", "have", "had", "please", "tell", "show", "give",
    "there", "any", "all", "its", "our", "your", "their", "into", "onto", "about", "per", "you", "can", "could",
    "would", "should", "been", "being", "than", "then", "also", "list", "find", "get",
}
# Words that appear in nearly every document: they still count, but little.
GENERIC = {
    "invoice", "invoices", "order", "orders", "purchase", "document", "number", "amount", "total", "due",
    "date", "vendor", "supplier", "buyer", "price", "unit", "quantity", "items", "item", "line", "pay",
    "payment", "terms", "currency", "reference", "ref", "page", "sum", "cost",
}

W_ID, W_NUMBER, W_WORD, W_GENERIC = 3.0, 2.0, 1.0, 0.4


def lexical_terms(question: str) -> list[tuple[str, float]]:
    """Terms for the lexical side with weights: identifiers first, amounts next, then content words."""
    terms: dict[str, float] = {}
    ids = ID_RE.findall(question)
    for ident in ids:
        terms[ident] = W_ID
    masked = ID_RE.sub(" ", question)
    for number in NUMBER_RE.findall(masked):
        if len(number.replace(",", "").replace(".", "")) >= 2:
            terms.setdefault(number, W_NUMBER)
    for word in WORD_RE.findall(masked):
        low = word.lower()
        if low in STOPWORDS:
            continue
        terms.setdefault(low, W_GENERIC if low in GENERIC else W_WORD)
    return sorted(terms.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class Retrieval:
    question: str
    hits: list[Hit]
    terms: list[tuple[str, float]]
    embed_ms: float
    search_ms: float
    pages: list[tuple[str, int]] = field(init=False)

    def __post_init__(self) -> None:
        seen: list[tuple[str, int]] = []
        for h in self.hits:
            if (h.file, h.page) not in seen:
                seen.append((h.file, h.page))
        self.pages = seen


class Retriever:
    def __init__(self, store: Store, embedder: Embedder, top_k: int = 6) -> None:
        self.store, self.embedder, self.top_k = store, embedder, top_k

    def search(self, question: str, k: int | None = None) -> Retrieval:
        t0 = time.perf_counter()
        qvec = self.embedder.query(question)
        t1 = time.perf_counter()
        terms = lexical_terms(question)
        hits = self.store.search(qvec, terms, k=k or self.top_k)
        t2 = time.perf_counter()
        return Retrieval(question, hits, terms, (t1 - t0) * 1000, (t2 - t1) * 1000)
