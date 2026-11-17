"""OCR for pages without a text layer. RapidOCR runs on ONNX Runtime: no system packages, no service, no key."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from statistics import mean, median

import numpy as np
from PIL import Image


def normalize(text: str) -> str:
    """RapidOCR is trained on Chinese and Latin text and emits full-width punctuation; fold it to ASCII."""
    return unicodedata.normalize("NFKC", text)


_RESPACE = (
    (re.compile(r"([a-z])([A-Z])"), r"\1 \2"),          # InvoiceHH-0107 -> Invoice HH-0107, PurchaseOrderPO -> Purchase Order PO
    (re.compile(r"([a-z])(\d)"), r"\1 \2"),             # Oakboard25mm -> Oakboard 25mm
    (re.compile(r"(\d)([A-Z][a-z])"), r"\1 \2"),       # 23Jul 2025 -> 23 Jul 2025, 48Mill Lane -> 48 Mill Lane
    (re.compile(r"(\d)([A-Z]{2,}-)"), r"\1 \2"),       # 8KS-PIPE-33 -> 8 KS-PIPE-33 (quantity glued to the SKU)
    (re.compile(r"\b(SUB)?(TOTAL)(DUE)\b"), r"\1\2 \3"),  # TOTALDUE -> TOTAL DUE
    (re.compile(r"\b(ORDER)(TOTAL)\b"), r"\1 \2"),
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
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def skew(words: list[Word], h: float) -> float:
    """The scan's rotation as a slope dy/dx, estimated from each box's nearest neighbour to the right.

    Scans are rotated by a degree or so. Over the width of a page that moves the cells of one
    table row by almost a row pitch, so grouping by raw y chains neighbouring rows together.
    The nearest box to the right inside a narrow band is almost always the next cell of the
    same row, and the median of those slopes is the page angle.
    """
    by_x = sorted(words, key=lambda w: w.x0)
    slopes = []
    for a in by_x:
        right = [b for b in by_x if b is not a and b.x0 >= a.x1 - 0.2 * h and abs(b.yc - a.yc) < 0.6 * h]
        if not right:
            continue
        b = min(right, key=lambda b: b.x0)
        dx = b.xc - a.xc
        if dx >= 2 * h:
            slopes.append((b.yc - a.yc) / dx)
    return median(slopes) if len(slopes) >= 3 else 0.0


def lines_from_words(words: list[Word]) -> str:
    """Group OCR boxes into reading-order lines. A table row must stay one line: retrieval depends on it."""
    if not words:
        return ""
    h = median(w.height for w in words) or 10.0
    slope = skew(words, h)
    level = {id(w): w.yc - slope * w.xc for w in words}  # y with the rotation taken out
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (level[id(w)], w.x0)):
        if rows and abs(level[id(w)] - mean(level[id(x)] for x in rows[-1])) <= 0.55 * h:
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

        # The default reduces the long side to 2000 px before detection; at that scale the detector
        # dropped a whole line between two tight neighbours. The 220 dpi render stays as rendered.
        self._engine = RapidOCR(max_side_len=4000)

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
