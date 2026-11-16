"""Page text out of a PDF: the text layer when there is one, OCR when there is not."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

from .ocr import Ocr

OCR_DPI = 220
MIN_TEXT_CHARS = 25  # less than this on a page and we treat it as an image


@dataclass
class Page:
    file: str
    page: int  # 1-based
    text: str
    source: str  # "text" | "ocr"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_pages(path: Path, ocr: Ocr | None = None) -> list[Page]:
    """One `Page` per PDF page. `ocr=None` disables OCR; image pages then come back empty."""
    pdf = pdfium.PdfDocument(str(path))
    pages: list[Page] = []
    try:
        for i, page in enumerate(pdf, start=1):
            text = page.get_textpage().get_text_bounded().replace("\r\n", "\n").strip()
            source = "text"
            if len(text) < MIN_TEXT_CHARS:
                source = "ocr"
                text = ""
                if ocr is not None:
                    image = page.render(scale=OCR_DPI / 72, grayscale=True).to_pil()
                    text = ocr.read(image)
            pages.append(Page(path.name, i, text, source))
    finally:
        pdf.close()
    return pages
