"""OCR for pages without a text layer. RapidOCR runs on ONNX Runtime: no system packages, no service, no key."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from statistics import median

import numpy as np
from PIL import Image


def normalize(text: str) -> str:
    """RapidOCR is trained on Chinese and Latin text and emits full-width punctuation; fold it to ASCII."""
    return unicodedata.normalize("NFKC", text)


_RESPACE = (
    (re.compile(r"([a-z])([A-Z])"), r"\1 \2"),          # InvoiceHH-0107 -> Invoice HH-0107, PurchaseOrderPO -> Purchase Order PO
    (re.compile(r"([a-z])(\d)"), r"\1 \2"),             # Oakboard25mm -> Oakboard 25mm
    (re.compile(r":(?=\S)"), ": "),                     # POnumber:PO-2025-0413 -> POnumber: PO-2025-0413
    (re.compile(r",(?=[A-Za-z])"), ", "),               # oil,20L -> oil, 20L (never inside 1,462.60)
    (re.compile(r"(?<=\S)\("), " ("),                   # 0107(continued) -> 0107 (continued)
    (re.compile(r"\)(?=\S)"), ") "),
    (re.compile(r"[ ]{2,}"), " "),
)


def respace(text: str) -> str:
    """Put back the spaces the recognizer drops inside a line.

    The lexical index tokenizes the text with the same parser the query goes through, so a
    document number glued to the word before it ("InvoiceHH-0107") is a different token than
    the number on its own. These rules only add spaces; they never change characters.
    """
    out = []
    for line in text.splitlines():
        for pattern, repl in _RESPACE:
            line = pattern.sub(repl, line)
        out.append(line.strip())
    return "\n".join(out)


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    score: float

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def lines_from_words(words: list[Word]) -> str:
    """Group OCR boxes into reading-order lines. A table row must stay one line: retrieval depends on it."""
    if not words:
        return ""
    h = median(w.height for w in words) or 10.0
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.yc, w.x0)):
        if rows and abs(rows[-1][-1].yc - w.yc) <= 0.55 * h:
            rows[-1].append(w)
        else:
            rows.append([w])
    out = []
    for row in rows:
        row.sort(key=lambda w: w.x0)
        out.append(" ".join(w.text.strip() for w in row if w.text.strip()))
    return "\n".join(line for line in out if line)


class Ocr:
    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # slow import, loaded on first use

        self._engine = RapidOCR()

    def words(self, image: Image.Image) -> list[Word]:
        result, _elapsed = self._engine(np.asarray(image.convert("RGB")))
        words: list[Word] = []
        for box, text, score in result or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            words.append(Word(min(xs), min(ys), max(xs), max(ys), normalize(str(text)), float(score)))
        return words

    def read(self, image: Image.Image) -> str:
        return respace(lines_from_words(self.words(image)))
