"""Folder of PDFs -> pages -> chunks -> embeddings -> Postgres. Idempotent: unchanged files are skipped."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..embed import Embedder
from ..store import Store
from .chunk import chunk_pages
from .ocr import Ocr
from .pdf import extract_pages, sha256


@dataclass
class IngestReport:
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: int = 0
    pages: int = 0
    ocr_pages: int = 0
    chunks: int = 0
    seconds: float = 0.0


def ingest_dir(corpus_dir: Path, store: Store, embedder: Embedder, ocr: Ocr | None = None,
               force: bool = False, log: Callable[[str], None] = print) -> IngestReport:
    t0 = time.perf_counter()
    report = IngestReport()
    store.init_schema()
    state = {} if force else store.document_state()
    files = sorted(corpus_dir.glob("*.pdf"))
    for path in files:
        sha = sha256(path)
        if state.get(path.name) == sha:
            report.skipped.append(path.name)
            continue
        pages = extract_pages(path, ocr)
        chunks = chunk_pages(pages)
        vectors = embedder.passages([c.text for c in chunks]) if chunks else []
        ocr_pages = sum(p.source == "ocr" for p in pages)
        store.replace_document(path.name, sha, len(pages), ocr_pages, chunks, vectors)
        report.ingested.append(path.name)
        report.pages += len(pages)
        report.ocr_pages += ocr_pages
        report.chunks += len(chunks)
        log(f"  {path.name}: {len(pages)} pages ({ocr_pages} OCR), {len(chunks)} chunks")
    report.removed = store.delete_missing({p.name for p in files})
    report.seconds = time.perf_counter() - t0
    return report
