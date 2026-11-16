"""Synthetic purchase orders and invoices with a known ground truth.

Why synthetic: every value on every page is known, so the eval set has exact answers;
there is no third-party data or licence in the repository; and the generator is
reproducible (one seed, one corpus). Six layouts, three currencies, four number
formats and five date formats stand in for the variety of a real inbox. About a third
of the files are rendered as scans (see `scan.py`) and have no text layer.
"""
from __future__ import annotations

import json
import random
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfgen import canvas

from .vendors import BUYER, NO_PO_VENDORS, NOTES, TERMS_DAYS, VENDORS, Company, Vendor

SEED = 20251116
MARGIN = 50


@dataclass
class Line:
    sku: str
    description: str
    qty: int
    unit: str
    unit_price: float
    total: float
    page: int = 0


@dataclass
class Doc:
    kind: str  # "invoice" | "purchase_order"
    number: str
    file: str
    date: str  # ISO
    due_date: str | None
    delivery_date: str | None
    vendor: str
    vendor_short: str
    vendor_tax_id: str | None
    vendor_bank: str | None
    buyer: str
    currency: str
    lines: list[Line]
    subtotal: float
    shipping: float
    tax_rate: float
    tax_label: str
    tax: float
    total: float
    terms: str
    po_reference: str | None
    notes: list[str]
    layout: int
    scan: bool
    pages: int = 0
    totals_page: int = 0

    @property
    def title(self) -> str:
        return "INVOICE" if self.kind == "invoice" else "PURCHASE ORDER"


@dataclass(frozen=True)
class Layout:
    name: str
    font: str
    bold: str
    header_side: str  # issuer block on the left or the right
    date_fmt: str
    num_fmt: str  # us: 1,234.50 | de: 1.234,50 | fr: 1 234,50
    sym: str  # prefix: $1,234.50 | suffix: 1,234.50 USD
    cols: tuple[str, ...]
    rows_first: int
    rows_next: int
    rules: bool
    size: float = 9.5


LAYOUTS: tuple[Layout, ...] = (
    Layout("classic", "Helvetica", "Helvetica-Bold", "left", "%Y-%m-%d", "us", "prefix",
           ("sku", "description", "qty", "unit_price", "total"), 9, 26, True),
    Layout("serif-right", "Times-Roman", "Times-Bold", "right", "%d %b %Y", "us", "suffix",
           ("description", "sku", "qty", "unit_price", "total"), 10, 28, False),
    Layout("mono", "Courier", "Courier-Bold", "left", "%m/%d/%Y", "us", "prefix",
           ("qty", "sku", "description", "unit_price", "total"), 8, 24, True, 8.5),
    Layout("continental", "Helvetica", "Helvetica-Bold", "right", "%d.%m.%Y", "de", "suffix",
           ("sku", "description", "qty", "unit", "unit_price", "total"), 9, 26, True),
    Layout("letterhead", "Times-Roman", "Times-Bold", "left", "%B %d, %Y", "us", "prefix",
           ("description", "qty", "unit_price", "total"), 10, 28, False),
    Layout("nordic", "Helvetica", "Helvetica-Bold", "right", "%d/%m/%Y", "fr", "suffix",
           ("sku", "description", "qty", "unit_price", "total"), 9, 26, True),
)

COL_WIDTH = {"sku": 72, "qty": 38, "unit": 46, "unit_price": 78, "total": 88}
COL_TITLE = {"sku": "Item", "description": "Description", "qty": "Qty", "unit": "Unit",
             "unit_price": "Unit price", "total": "Amount"}
NUMERIC = {"qty", "unit_price", "total"}
CURRENCY_SYMBOL = {"USD": "$", "EUR": "EUR ", "GBP": "£"}


# ---------------------------------------------------------------- formatting

def fmt_number(value: float, num_fmt: str) -> str:
    s = f"{value:,.2f}"
    if num_fmt == "de":
        s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    elif num_fmt == "fr":
        s = s.replace(",", " ").replace(".", ",")
    return s


def fmt_money(value: float, currency: str, layout: Layout) -> str:
    s = fmt_number(value, layout.num_fmt)
    if layout.sym == "prefix":
        return f"{CURRENCY_SYMBOL[currency]}{s}"
    return f"{s} {currency}"


def fmt_date(iso: str, layout: Layout) -> str:
    return date.fromisoformat(iso).strftime(layout.date_fmt)


def date_variants(iso: str) -> list[str]:
    """Every spelling of a date the corpus or a model is likely to use."""
    d = date.fromisoformat(iso)
    out = {iso}
    for fmt in ("%d %b %Y", "%m/%d/%Y", "%d.%m.%Y", "%B %d, %Y", "%d/%m/%Y", "%d %B %Y", "%b %d, %Y"):
        out.add(d.strftime(fmt))
        out.add(d.strftime(fmt).replace(" 0", " ").lstrip("0"))
    return sorted(out)


def slug(number: str) -> str:
    return number.replace("/", "-").replace(" ", "")


# ---------------------------------------------------------------- document data

def _pick_lines(rng: random.Random, vendor: Vendor, n: int) -> list[Line]:
    products = rng.sample(vendor.catalog, k=min(n, len(vendor.catalog)))
    # a long order repeats catalog items with different quantities, like a call-off
    while len(products) < n:
        products.append(rng.choice(vendor.catalog))
    lines = []
    for p in products:
        qty = rng.choice([1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 24, 30, 40, 50])
        if p.price > 150:
            qty = rng.choice([1, 1, 2, 2, 3, 4])
        lines.append(Line(p.sku, p.description, qty, p.unit, p.price, round(qty * p.price, 2)))
    return lines


def _money(doc_lines: list[Line], shipping: float, tax_rate: float) -> tuple[float, float, float]:
    subtotal = round(sum(line.total for line in doc_lines), 2)
    tax = round((subtotal + shipping) * tax_rate, 2)
    total = round(subtotal + shipping + tax, 2)
    return subtotal, tax, total


def build_documents(seed: int = SEED, n_po: int = 16, n_invoices: int = 24) -> list[Doc]:
    rng = random.Random(seed)
    docs: list[Doc] = []
    po_vendors = [v for v in VENDORS if v.company.name not in NO_PO_VENDORS]
    start = date(2025, 1, 6)

    pos: list[tuple[Doc, Vendor]] = []
    for i in range(n_po):
        vendor = po_vendors[i % len(po_vendors)] if i < len(po_vendors) else rng.choice(po_vendors)
        n_lines = rng.choice([3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 14])
        lines = _pick_lines(rng, vendor, n_lines)
        d = start + timedelta(days=rng.randint(0, 230))
        shipping = rng.choice([0.0, 0.0, 0.0, 45.0, 85.0, 120.0, 180.0])
        subtotal, tax, total = _money(lines, shipping, vendor.tax_rate)
        number = f"PO-2025-{401 + i:04d}"
        doc = Doc(
            kind="purchase_order", number=number, file=f"{slug(number)}.pdf",
            date=d.isoformat(), due_date=None,
            delivery_date=(d + timedelta(days=rng.randint(7, 28))).isoformat(),
            vendor=vendor.company.name, vendor_short=vendor.company.short,
            vendor_tax_id=vendor.company.tax_id, vendor_bank=None,
            buyer=BUYER.name, currency=vendor.currency, lines=lines,
            subtotal=subtotal, shipping=shipping, tax_rate=vendor.tax_rate, tax_label=vendor.tax_label,
            tax=tax, total=total, terms=vendor.terms, po_reference=None,
            notes=rng.sample(NOTES, k=rng.choice([1, 1, 2])),
            layout=0, scan=rng.random() < 0.2,
        )
        pos.append((doc, vendor))
        docs.append(doc)

    counters: dict[str, int] = {}

    def invoice_number(vendor: Vendor) -> str:
        n = counters.get(vendor.company.name, rng.randint(100, 900)) + 1
        counters[vendor.company.name] = n
        return f"{vendor.invoice_prefix}{n:04d}"

    # Invoices against purchase orders: same vendor, same lines, sometimes a partial delivery.
    referenced = rng.sample(pos, k=min(14, len(pos)))
    for po, vendor in referenced:
        lines = [Line(**asdict(line)) for line in po.lines]
        if rng.random() < 0.3 and len(lines) > 3:
            lines = lines[: len(lines) - rng.randint(1, 2)]  # the rest ships later
        for line in lines:
            line.page = 0
        d = date.fromisoformat(po.date) + timedelta(days=rng.randint(10, 35))
        shipping = po.shipping
        subtotal, tax, total = _money(lines, shipping, vendor.tax_rate)
        number = invoice_number(vendor)
        docs.append(Doc(
            kind="invoice", number=number, file=f"{slug(number)}.pdf",
            date=d.isoformat(), due_date=(d + timedelta(days=TERMS_DAYS[vendor.terms])).isoformat(),
            delivery_date=None,
            vendor=vendor.company.name, vendor_short=vendor.company.short,
            vendor_tax_id=vendor.company.tax_id, vendor_bank=vendor.company.bank,
            buyer=BUYER.name, currency=vendor.currency, lines=lines,
            subtotal=subtotal, shipping=shipping, tax_rate=vendor.tax_rate, tax_label=vendor.tax_label,
            tax=tax, total=total, terms=vendor.terms, po_reference=po.number,
            notes=rng.sample(NOTES, k=rng.choice([1, 2])),
            layout=1 + VENDORS.index(vendor) % (len(LAYOUTS) - 1), scan=rng.random() < 0.35,
        ))

    # Invoices without a purchase order: freight, consumables, small repeat orders.
    freight = next(v for v in VENDORS if v.company.name in NO_PO_VENDORS)
    standalone_vendors = [freight, freight, freight] + rng.sample(po_vendors, k=n_invoices - len(referenced) - 3)
    for vendor in standalone_vendors:
        lines = _pick_lines(rng, vendor, rng.choice([2, 3, 3, 4, 5, 9, 12]))
        d = start + timedelta(days=rng.randint(0, 260))
        shipping = 0.0 if vendor is freight else rng.choice([0.0, 0.0, 35.0, 60.0])
        subtotal, tax, total = _money(lines, shipping, vendor.tax_rate)
        number = invoice_number(vendor)
        docs.append(Doc(
            kind="invoice", number=number, file=f"{slug(number)}.pdf",
            date=d.isoformat(), due_date=(d + timedelta(days=TERMS_DAYS[vendor.terms])).isoformat(),
            delivery_date=None,
            vendor=vendor.company.name, vendor_short=vendor.company.short,
            vendor_tax_id=vendor.company.tax_id, vendor_bank=vendor.company.bank,
            buyer=BUYER.name, currency=vendor.currency, lines=lines,
            subtotal=subtotal, shipping=shipping, tax_rate=vendor.tax_rate, tax_label=vendor.tax_label,
            tax=tax, total=total, terms=vendor.terms, po_reference=None,
            notes=rng.sample(NOTES, k=1),
            layout=1 + VENDORS.index(vendor) % (len(LAYOUTS) - 1), scan=rng.random() < 0.35,
        ))

    docs.sort(key=lambda d: (d.date, d.number))
    return docs


# ---------------------------------------------------------------- rendering

def _fit(c: canvas.Canvas, text: str, width: float, font: str, size: float) -> float:
    while size > 5.5 and c.stringWidth(text, font, size) > width:
        size -= 0.5
    return size


class _Renderer:
    def __init__(self, doc: Doc, vendor: Vendor | None, layout: Layout, out: Path):
        self.doc, self.vendor, self.layout = doc, vendor, layout
        issuer_country = BUYER.country if doc.kind == "purchase_order" else (vendor.company.country if vendor else "UK")
        self.W, self.H = LETTER if issuer_country == "US" else A4
        self.c = canvas.Canvas(str(out), pagesize=(self.W, self.H))
        self.c.setTitle(f"{doc.title.title()} {doc.number}")
        self.page = 1
        self.y = self.H - MARGIN
        self.table_x = MARGIN
        self.table_w = self.W - 2 * MARGIN
        self.widths = self._col_widths()

    def _col_widths(self) -> dict[str, float]:
        fixed = sum(COL_WIDTH[c] for c in self.layout.cols if c != "description")
        return {c: (self.table_w - fixed if c == "description" else COL_WIDTH[c]) for c in self.layout.cols}

    # text helpers --------------------------------------------------------
    def text(self, x: float, y: float, s: str, bold: bool = False, size: float | None = None, right: bool = False):
        font = self.layout.bold if bold else self.layout.font
        size = size or self.layout.size
        self.c.setFont(font, size)
        (self.c.drawRightString if right else self.c.drawString)(x, y, s)

    def block(self, x: float, y: float, lines: list[str], bold_first: bool = True, size: float | None = None) -> float:
        size = size or self.layout.size
        for i, s in enumerate(lines):
            self.text(x, y, s, bold=(bold_first and i == 0), size=size)
            y -= size + 2.5
        return y

    # page parts ----------------------------------------------------------
    def first_header(self) -> None:
        doc, c, lay = self.doc, self.c, self.layout
        issuer: Company = BUYER if doc.kind == "purchase_order" else self.vendor.company  # type: ignore[union-attr]
        other: Company = self.vendor.company if doc.kind == "purchase_order" else BUYER  # type: ignore[union-attr]
        left_x, right_x = MARGIN, self.W / 2 + 20
        issuer_x = left_x if lay.header_side == "left" else right_x
        meta_x = right_x if lay.header_side == "left" else left_x

        # issuer block with a small monogram box
        c.setLineWidth(0.8)
        c.rect(issuer_x, self.y - 22, 22, 22, stroke=1, fill=0)
        self.text(issuer_x + 11 - c.stringWidth(issuer.name[0], lay.bold, 11) / 2, self.y - 16, issuer.name[0], bold=True, size=11)
        y = self.block(issuer_x + 30, self.y - 8, [issuer.name, *issuer.address], size=lay.size)
        if issuer.tax_id:
            y = self.block(issuer_x + 30, y, [f"Tax ID: {issuer.tax_id}"], bold_first=False, size=lay.size - 1)

        # title + meta
        self.text(meta_x, self.y - 10, doc.title, bold=True, size=18)
        meta = [f"{'Invoice no.' if doc.kind == 'invoice' else 'PO number'}: {doc.number}",
                f"{'Invoice date' if doc.kind == 'invoice' else 'Order date'}: {fmt_date(doc.date, lay)}"]
        if doc.kind == "invoice":
            meta.append(f"Due date: {fmt_date(doc.due_date or doc.date, lay)}")
            if doc.po_reference:
                meta.append(f"Your PO reference: {doc.po_reference}")
        else:
            meta.append(f"Requested delivery: {fmt_date(doc.delivery_date or doc.date, lay)}")
        meta.append(f"Payment terms: {doc.terms}")
        meta.append(f"Currency: {doc.currency}")
        self.block(meta_x, self.y - 32, meta, bold_first=False)

        self.y = min(y, self.y - 32 - len(meta) * (lay.size + 2.5)) - 18

        # counterpart blocks
        label = "Bill to:" if doc.kind == "invoice" else "Vendor:"
        y1 = self.block(left_x, self.y, [label, other.name, *other.address], size=lay.size)
        if other.tax_id:
            y1 = self.block(left_x, y1, [f"Tax ID: {other.tax_id}"], bold_first=False, size=lay.size - 1)
        ship_label = "Ship to:" if doc.kind == "purchase_order" else "Delivered to:"
        y2 = self.block(right_x, self.y, [ship_label, BUYER.name, *BUYER.address], size=lay.size)
        self.y = min(y1, y2) - 16

    def continuation_header(self) -> None:
        self.text(MARGIN, self.y - 4, f"{self.doc.title.title()} {self.doc.number} (continued)", bold=True, size=11)
        self.text(self.W - MARGIN, self.y - 4, f"Page {self.page}", right=True, size=self.layout.size)
        self.y -= 26

    def table_header(self) -> None:
        c, lay = self.c, self.layout
        x = self.table_x
        if lay.rules:
            c.setLineWidth(0.6)
            c.line(self.table_x, self.y + 4, self.table_x + self.table_w, self.y + 4)
        for col in lay.cols:
            w = self.widths[col]
            if col in NUMERIC:
                self.text(x + w - 2, self.y - 8, COL_TITLE[col], bold=True, right=True)
            else:
                self.text(x + 2, self.y - 8, COL_TITLE[col], bold=True)
            x += w
        self.y -= 14
        c.setLineWidth(0.6)
        c.line(self.table_x, self.y, self.table_x + self.table_w, self.y)
        self.y -= 4

    def row(self, line: Line) -> None:
        lay = self.layout
        x = self.table_x
        values = {
            "sku": line.sku, "description": line.description, "qty": str(line.qty), "unit": line.unit,
            "unit_price": fmt_number(line.unit_price, lay.num_fmt), "total": fmt_number(line.total, lay.num_fmt),
        }
        row_h = lay.size + 5
        for col in lay.cols:
            w = self.widths[col]
            s = values[col]
            size = _fit(self.c, s, w - 4, lay.font, lay.size)
            if col in NUMERIC:
                self.text(x + w - 2, self.y - lay.size, s, size=size, right=True)
            else:
                self.text(x + 2, self.y - lay.size, s, size=size)
            x += w
        self.y -= row_h
        if lay.rules:
            self.c.setLineWidth(0.2)
            self.c.setStrokeGray(0.6)
            self.c.line(self.table_x, self.y, self.table_x + self.table_w, self.y)
            self.c.setStrokeGray(0)
        self.y -= 2

    def footer(self) -> None:
        issuer = BUYER if self.doc.kind == "purchase_order" else self.vendor.company  # type: ignore[union-attr]
        self.c.setFont(self.layout.font, 7.5)
        self.c.setFillGray(0.35)
        self.c.drawString(MARGIN, 28, f"{issuer.name} - {', '.join(issuer.address)}")
        self.c.drawRightString(self.W - MARGIN, 28, f"Page {self.page}")
        self.c.setFillGray(0)

    def new_page(self) -> None:
        self.footer()
        self.c.showPage()
        self.page += 1
        self.y = self.H - MARGIN
        self.continuation_header()
        self.table_header()

    def totals_and_notes(self) -> None:
        doc, lay = self.doc, self.layout
        x_label = self.table_x + self.table_w - 230
        x_val = self.table_x + self.table_w - 2
        self.y -= 6
        rows = [("Subtotal", fmt_money(doc.subtotal, doc.currency, lay))]
        if doc.shipping:
            rows.append(("Shipping & handling", fmt_money(doc.shipping, doc.currency, lay)))
        rows.append((doc.tax_label, fmt_money(doc.tax, doc.currency, lay)))
        for label, value in rows:
            self.text(x_label, self.y - lay.size, label)
            self.text(x_val, self.y - lay.size, value, right=True)
            self.y -= lay.size + 5
        self.c.setLineWidth(1)
        self.c.line(x_label, self.y - 1, x_val, self.y - 1)
        total_label = "TOTAL DUE" if doc.kind == "invoice" else "ORDER TOTAL"
        self.text(x_label, self.y - lay.size - 6, total_label, bold=True, size=lay.size + 2)
        self.text(x_val, self.y - lay.size - 6, fmt_money(doc.total, doc.currency, lay), bold=True, size=lay.size + 2, right=True)
        self.y -= lay.size + 26
        doc.totals_page = self.page

        notes = list(doc.notes)
        if doc.kind == "invoice":
            if doc.vendor_bank:
                notes.append(f"Payment by bank transfer to IBAN {doc.vendor_bank}, reference {doc.number}.")
            notes.append(f"Payment terms {doc.terms}: amount due by {fmt_date(doc.due_date or doc.date, lay)}.")
        else:
            notes.append(f"Please confirm receipt of this order and the delivery date {fmt_date(doc.delivery_date or doc.date, lay)}.")
            notes.append(f"Purchasing contact: purchasing@oakridge-fabrication.example, ref {doc.number}.")
        self.text(MARGIN, self.y - lay.size, "Notes", bold=True)
        self.y -= lay.size + 4
        for note in notes:
            size = _fit(self.c, note, self.table_w, lay.font, lay.size - 0.5)
            self.text(MARGIN, self.y - lay.size, note, size=size)
            self.y -= lay.size + 3

    def render(self) -> Doc:
        lay, doc = self.layout, self.doc
        self.first_header()
        self.table_header()
        capacity = lay.rows_first
        on_page = 0
        for line in doc.lines:
            if on_page >= capacity:
                self.new_page()
                capacity, on_page = lay.rows_next, 0
            self.row(line)
            line.page = self.page
            on_page += 1
        if self.y < 150 + 14 * (len(doc.notes) + 3):
            self.new_page()
        self.totals_and_notes()
        self.footer()
        self.c.save()
        doc.pages = self.page
        return doc


def render_pdf(doc: Doc, out: Path) -> Doc:
    vendor = next((v for v in VENDORS if v.company.name == doc.vendor), None)
    return _Renderer(doc, vendor, LAYOUTS[doc.layout], out).render()


# ---------------------------------------------------------------- corpus

def write_corpus(out_dir: Path, seed: int = SEED, work_dir: Path | None = None) -> list[Doc]:
    """Render every document; scans go through `scan.py`. Returns the ground truth."""
    from .scan import scan_pdf  # local import: Pillow/pypdfium2 are only needed here

    out_dir.mkdir(parents=True, exist_ok=True)
    work = work_dir or (out_dir.parent / "work" / "text-pdfs")
    work.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.pdf"):
        old.unlink()
    rng = random.Random(seed ^ 0x5CA7)
    docs = build_documents(seed)
    for doc in docs:
        text_pdf = work / doc.file
        render_pdf(doc, text_pdf)
        if doc.scan:
            scan_pdf(text_pdf, out_dir / doc.file, rng)
        else:
            shutil.copyfile(text_pdf, out_dir / doc.file)
    (out_dir / "ground_truth.json").write_text(
        json.dumps({"seed": seed, "buyer": asdict(BUYER), "documents": [asdict(d) for d in docs]},
                   indent=1, ensure_ascii=False),
        encoding="utf-8", newline="\n",
    )
    return docs


def load_ground_truth(corpus_dir: Path) -> list[Doc]:
    data = json.loads((corpus_dir / "ground_truth.json").read_text(encoding="utf-8"))
    docs = []
    for d in data["documents"]:
        d["lines"] = [Line(**line) for line in d["lines"]]
        docs.append(Doc(**d))
    return docs


if __name__ == "__main__":  # pragma: no cover
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/corpus")
    written = write_corpus(target)
    scans = sum(d.scan for d in written)
    print(f"{len(written)} documents -> {target} ({scans} scans, {sum(d.pages for d in written)} pages)")
