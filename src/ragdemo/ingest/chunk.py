"""Page text into chunks that can stand alone.

Business documents are tables under a header. A chunk cut out of the middle of a table
knows neither which document it belongs to nor whether it is an invoice or an order, so
retrieval by document number would miss it. Every chunk therefore starts with the
page's identifying lines (title, number, dates) and then carries a window of body lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .pdf import Page

# `\s*` between the words: OCR output may have lost the spaces.
HEADER_RE = re.compile(
    r"(INVOICE|PURCHASE\s*ORDER|Invoice\s*no\.?|PO\s*number|Invoice\s*date|Order\s*date|Due\s*date|"
    r"Your\s*PO\s*reference|\(\s*continued\s*\)|Requested\s*delivery)",
    re.IGNORECASE,
)
HEADER_SCAN_LINES = 14
MAX_CHARS = 900
OVERLAP_LINES = 2


@dataclass
class Chunk:
    file: str
    page: int
    idx: int  # position within the page
    text: str
    source: str  # "text" | "ocr"
    header: str


def header_lines(lines: list[str]) -> list[str]:
    seen: list[str] = []
    for line in lines[:HEADER_SCAN_LINES]:
        if HEADER_RE.search(line) and line not in seen:
            seen.append(line)
    return seen


def split_lines(lines: list[str], max_chars: int, overlap: int) -> list[list[str]]:
    windows: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > max_chars:
            windows.append(current)
            current = current[-overlap:] if overlap else []
            size = sum(len(s) + 1 for s in current)
        current.append(line)
        size += len(line) + 1
    if current:
        windows.append(current)
    return windows


def chunk_page(page: Page, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_LINES) -> list[Chunk]:
    lines = [s.strip() for s in page.text.splitlines() if s.strip()]
    if not lines:
        return []
    header = header_lines(lines)
    header_text = " | ".join(header)
    body = [s for s in lines if s not in header]
    budget = max(200, max_chars - len(header_text))
    chunks = []
    for idx, window in enumerate(split_lines(body, budget, overlap)):
        text = (header_text + "\n" if header_text else "") + "\n".join(window)
        chunks.append(Chunk(page.file, page.page, idx, text, page.source, header_text))
    return chunks


def chunk_pages(pages: list[Page], **kw) -> list[Chunk]:
    out: list[Chunk] = []
    for page in pages:
        out.extend(chunk_page(page, **kw))
    return out
