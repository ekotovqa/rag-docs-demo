"""Deterministic checks: no model grades another model here."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

NUMBER_RE = re.compile(r"\d(?:[\d,.\s]*\d)?")


def parse_number(token: str) -> Decimal | None:
    """1,862.00 / 1.862,00 / 1 862,00 / 1862 / 61.20 -> Decimal. Both thousands conventions are accepted."""
    s = token.strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(",", ".")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = head.replace(",", "") + "." + tail if len(tail) == 2 and s.count(",") == 1 else s.replace(",", "")
    elif "." in s:
        head, _, tail = s.rpartition(".")
        s = head.replace(".", "") + "." + tail if len(tail) == 2 and s.count(".") == 1 else s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def numbers_in(text: str) -> set[Decimal]:
    out: set[Decimal] = set()
    for m in NUMBER_RE.finditer(text):
        value = parse_number(m.group(0))
        if value is not None:
            out.add(value)
    return out


def answer_matches(answer: str, expected: list[str], expected_type: str) -> bool:
    if expected_type == "number":
        found = numbers_in(answer)
        return any(Decimal(e) in found for e in expected)
    low = answer.lower()
    return any(e.lower() in low for e in expected)


def hit_at(pages: list[tuple[str, int]], gold: list[tuple[str, int]], k: int) -> bool:
    gold_set = set(gold)
    return any(p in gold_set for p in pages[:k])


def doc_hit_at(pages: list[tuple[str, int]], gold: list[tuple[str, int]], k: int) -> bool:
    gold_files = {f for f, _ in gold}
    seen: list[str] = []
    for f, _ in pages:
        if f not in seen:
            seen.append(f)
    return any(f in gold_files for f in seen[:k])


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
