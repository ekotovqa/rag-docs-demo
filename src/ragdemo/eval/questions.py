"""Golden questions derived from the corpus ground truth.

Every answerable question knows the file and the pages that hold its answer, and the exact
value to expect. Unanswerable questions ask for things the documents never state, so the
right answer is a refusal. The set is deterministic for a given corpus.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from ..corpus.generate import Doc, Line, date_variants, load_ground_truth


@dataclass
class Question:
    id: str
    question: str
    kind: str
    answerable: bool
    expected: list[str]  # any one of these in the answer counts
    expected_type: str  # number | text | date | none
    gold: list[tuple[str, int]]  # (file, page) pairs that hold the answer; any one is a hit
    scan: bool
    multi_doc: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["gold"] = [list(g) for g in self.gold]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Question":
        d = dict(d)
        d["gold"] = [tuple(g) for g in d["gold"]]
        return cls(**d)


def money(value: float) -> list[str]:
    return [f"{value:.2f}"]


def build_questions(docs: list[Doc], seed: int = 7) -> list[Question]:
    rng = random.Random(seed)
    invoices = [d for d in docs if d.kind == "invoice"]
    orders = [d for d in docs if d.kind == "purchase_order"]
    qs: list[Question] = []
    n = 0

    def add(kind: str, text: str, doc: Doc | None, expected: list[str], etype: str,
            gold: list[tuple[str, int]], answerable: bool = True, multi_doc: bool = False) -> None:
        nonlocal n
        n += 1
        qs.append(Question(f"q{n:03d}", text, kind, answerable, expected, etype, gold,
                           bool(doc.scan) if doc else False, multi_doc))

    # totals: every invoice, and a third of the orders
    for d in invoices:
        text = rng.choice([
            f"What is the total amount due on invoice {d.number}?",
            f"How much do we owe on invoice {d.number}?",
            f"What is the total of invoice {d.number}, including tax?",
        ])
        add("total", text, d, money(d.total), "number", [(d.file, d.totals_page)])
    for d in rng.sample(orders, k=len(orders) // 3):
        add("total", f"What is the order total of purchase order {d.number}?", d, money(d.total), "number", [(d.file, d.totals_page)])

    # who issued it, when, when is it due, which PO
    for d in rng.sample(invoices, k=8):
        add("vendor", rng.choice([f"Who issued invoice {d.number}?", f"Which supplier sent us invoice {d.number}?"]),
            d, [d.vendor, d.vendor_short], "text", [(d.file, 1)])
    for d in rng.sample(invoices, k=8):
        add("due_date", rng.choice([f"When is invoice {d.number} due?", f"What is the due date of invoice {d.number}?"]),
            d, date_variants(d.due_date or d.date), "date", [(d.file, 1), (d.file, d.totals_page)])
    for d in rng.sample(invoices, k=5):
        add("doc_date", f"On what date was invoice {d.number} issued?", d, date_variants(d.date), "date", [(d.file, 1)])
    for d in rng.sample([i for i in invoices if i.po_reference], k=6):
        add("po_ref", rng.choice([f"Which purchase order does invoice {d.number} refer to?",
                                  f"What is our PO reference on invoice {d.number}?"]),
            d, [d.po_reference or ""], "text", [(d.file, 1)])
    for d in rng.sample(invoices, k=5):
        add("terms", f"What are the payment terms on invoice {d.number}?", d, [d.terms], "text", [(d.file, 1), (d.file, d.totals_page)])

    # line items: quantities and unit prices, picked so that second pages get asked about.
    # Only SKUs that occur once in the document: "how many X" has one answer then.
    def unique_lines(d: Doc) -> list[Line]:
        counts = Counter(line.sku for line in d.lines)
        return [line for line in d.lines if counts[line.sku] == 1]

    for d in rng.sample([o for o in orders if unique_lines(o)], k=8):
        lines = unique_lines(d)
        line = max(lines, key=lambda line: line.page) if rng.random() < 0.6 else rng.choice(lines)
        add("qty", rng.choice([f"How many {line.unit}(s) of {line.sku} were ordered on {d.number}?",
                               f"What quantity of {line.sku} does purchase order {d.number} specify?"]),
            d, [str(line.qty)], "number", [(d.file, line.page)])
    for d in rng.sample([i for i in invoices if unique_lines(i)], k=6):
        lines = unique_lines(d)
        line = max(lines, key=lambda line: line.page) if rng.random() < 0.6 else rng.choice(lines)
        add("unit_price", f"What is the unit price of \"{line.description}\" on invoice {d.number}?",
            d, money(line.unit_price), "number", [(d.file, line.page)])

    # tax, shipping, delivery dates, bank details
    for d in rng.sample([i for i in invoices if i.tax > 0], k=min(4, len([i for i in invoices if i.tax > 0]))):
        add("tax", f"How much VAT or sales tax is charged on invoice {d.number}?", d, money(d.tax), "number", [(d.file, d.totals_page)])
    for d in rng.sample([x for x in docs if x.shipping > 0], k=4):
        add("shipping", f"What is the shipping and handling charge on {d.number}?", d, money(d.shipping), "number", [(d.file, d.totals_page)])
    for d in rng.sample(orders, k=4):
        add("delivery_date", f"What is the requested delivery date on purchase order {d.number}?",
            d, date_variants(d.delivery_date or d.date), "date", [(d.file, 1), (d.file, d.totals_page)])
    for d in rng.sample([i for i in invoices if i.vendor_bank], k=3):
        add("iban", f"Which IBAN should we pay invoice {d.number} to?", d, [d.vendor_bank or ""], "text", [(d.file, d.totals_page)])

    # across documents: which vendor sells a product (any document with the SKU is a hit)
    by_sku: dict[str, list[tuple[Doc, int]]] = {}
    for d in docs:
        for line in d.lines:
            by_sku.setdefault(line.sku, []).append((d, line.page))
    for sku in rng.sample(sorted(by_sku), k=4):
        d0 = by_sku[sku][0][0]
        description = next(line.description for line in d0.lines if line.sku == sku)
        add("cross_vendor", f"Which supplier do we buy \"{description}\" from?",
            None, [d0.vendor, d0.vendor_short], "text", [(d.file, p) for d, p in by_sku[sku]], multi_doc=True)

    # unanswerable: the documents never say these things
    missing_number = "INV-2025-9999"
    add("unanswerable", f"What is the total amount due on invoice {missing_number}?", None, [], "none", [], answerable=False)
    for d in rng.sample([i for i in invoices if not i.vendor_bank], k=2):
        add("unanswerable", f"What is the IBAN of {d.vendor_short} on invoice {d.number}?", d, [], "none", [(d.file, 1)], answerable=False)
    for d in rng.sample(invoices, k=3):
        add("unanswerable", rng.choice([f"Has invoice {d.number} been paid yet?",
                                        f"What is the weight of the shipment billed on invoice {d.number}?",
                                        f"What is the phone number of the vendor on invoice {d.number}?"]),
            d, [], "none", [(d.file, 1)], answerable=False)
    for d in rng.sample(orders, k=2):
        add("unanswerable", rng.choice([f"Who approved purchase order {d.number}?",
                                        f"What is the tracking number for the delivery of {d.number}?"]),
            d, [], "none", [(d.file, 1)], answerable=False)
    add("unanswerable", "What was our total spend with all vendors in 2024?", None, [], "none", [], answerable=False)
    add("unanswerable", "Which invoices are overdue as of today?", None, [], "none", [], answerable=False)
    return qs


def save_questions(questions: list[Question], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([q.to_dict() for q in questions], indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")


def load_questions(path: Path) -> list[Question]:
    return [Question.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def questions_for(corpus_dir: Path, eval_dir: Path) -> list[Question]:
    """Build from the ground truth and write `questions.json`; the file is what the eval reads."""
    qs = build_questions(load_ground_truth(corpus_dir))
    save_questions(qs, eval_dir / "questions.json")
    return qs
