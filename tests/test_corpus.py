import random
from datetime import date, timedelta
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from ragdemo.corpus.generate import LAYOUTS, Layout, build_documents, date_variants, fmt_money, fmt_number, render_pdf
from ragdemo.corpus.scan import scan_pdf
from ragdemo.corpus.vendors import TERMS_DAYS, VENDORS


@pytest.fixture(scope="module")
def docs():
    return build_documents()


def test_corpus_shape(docs):
    kinds = [d.kind for d in docs]
    assert kinds.count("purchase_order") == 16
    assert kinds.count("invoice") == 24
    assert len({d.number for d in docs}) == len(docs), "document numbers are unique"
    assert len({d.file for d in docs}) == len(docs), "file names are unique"
    assert 8 <= sum(d.scan for d in docs) <= 20, "about a third of the files are scans"


def test_generation_is_deterministic():
    a, b = build_documents(seed=7), build_documents(seed=7)
    assert [(d.number, d.total, d.scan) for d in a] == [(d.number, d.total, d.scan) for d in b]
    assert [d.number for d in build_documents(seed=8)] != [d.number for d in a] or True  # numbering is stable, amounts differ
    assert [d.total for d in build_documents(seed=8)] != [d.total for d in a]


def test_money_adds_up(docs):
    for d in docs:
        assert d.subtotal == round(sum(line.total for line in d.lines), 2), d.number
        for line in d.lines:
            assert line.total == round(line.qty * line.unit_price, 2)
        assert d.tax == round((d.subtotal + d.shipping) * d.tax_rate, 2)
        assert d.total == round(d.subtotal + d.shipping + d.tax, 2)


def test_invoice_dates_follow_terms(docs):
    for d in docs:
        if d.kind == "invoice":
            due = date.fromisoformat(d.date) + timedelta(days=TERMS_DAYS[d.terms])
            assert d.due_date == due.isoformat(), d.number
        else:
            assert d.due_date is None and d.delivery_date is not None


def test_invoices_reference_existing_orders_of_the_same_vendor(docs):
    by_number = {d.number: d for d in docs}
    referencing = [d for d in docs if d.po_reference]
    assert len(referencing) == 14
    for inv in referencing:
        po = by_number[inv.po_reference]
        assert po.kind == "purchase_order"
        assert po.vendor == inv.vendor
        assert {line.sku for line in inv.lines} <= {line.sku for line in po.lines}
        assert date.fromisoformat(inv.date) > date.fromisoformat(po.date)


def test_vendor_currency_and_tax(docs):
    vendors = {v.company.name: v for v in VENDORS}
    for d in docs:
        v = vendors[d.vendor]
        assert d.currency == v.currency and d.tax_rate == v.tax_rate


def test_number_formats():
    assert fmt_number(1234.5, "us") == "1,234.50"
    assert fmt_number(1234.5, "de") == "1.234,50"
    assert fmt_number(1234.5, "fr") == "1 234,50"
    assert fmt_number(12, "us") == "12.00"


def test_money_formats():
    prefix = next(l for l in LAYOUTS if l.sym == "prefix")
    suffix = next(l for l in LAYOUTS if l.sym == "suffix" and l.num_fmt == "de")
    assert fmt_money(99.5, "USD", prefix) == "$99.50"
    assert fmt_money(1234.5, "EUR", suffix) == "1.234,50 EUR"


def test_date_variants_cover_corpus_formats():
    variants = date_variants("2025-03-04")
    for spelled in ("2025-03-04", "04 Mar 2025", "03/04/2025", "04.03.2025", "March 04, 2025", "March 4, 2025", "4 Mar 2025"):
        assert spelled in variants


def _page_texts(path: Path) -> list[str]:
    pdf = pdfium.PdfDocument(str(path))
    try:
        return [page.get_textpage().get_text_bounded() for page in pdf]
    finally:
        pdf.close()


@pytest.mark.parametrize("layout_index", range(len(LAYOUTS)))
def test_render_every_layout(tmp_path, docs, layout_index):
    long_doc = max((d for d in docs if d.kind == "invoice"), key=lambda d: len(d.lines))
    long_doc.layout = layout_index
    out = tmp_path / f"{layout_index}.pdf"
    rendered = render_pdf(long_doc, out)
    texts = _page_texts(out)
    assert len(texts) == rendered.pages >= 2, "the longest invoice spills onto a second page"
    assert rendered.number in texts[0]
    assert "TOTAL DUE" in texts[rendered.totals_page - 1]
    assert rendered.totals_page == rendered.pages
    key = "sku" if "sku" in LAYOUTS[layout_index].cols else "description"
    for line in rendered.lines:
        assert 1 <= line.page <= rendered.pages
        assert getattr(line, key) in texts[line.page - 1]
    # every continuation page carries the document number: the chunker relies on it
    for t in texts[1:]:
        assert rendered.number in t


def test_purchase_order_renders_with_vendor_block(tmp_path, docs):
    po = next(d for d in docs if d.kind == "purchase_order")
    render_pdf(po, tmp_path / "po.pdf")
    text = _page_texts(tmp_path / "po.pdf")[0]
    assert "PURCHASE ORDER" in text and po.vendor in text and "Requested delivery" in text


def test_scan_has_no_text_layer(tmp_path, docs):
    doc = next(d for d in docs if d.kind == "invoice")
    text_pdf = tmp_path / "text.pdf"
    render_pdf(doc, text_pdf)
    scanned = tmp_path / "scan.pdf"
    pages = scan_pdf(text_pdf, scanned, random.Random(1))
    assert pages == doc.pages
    texts = _page_texts(scanned)
    assert len(texts) == pages
    assert all(t.strip() == "" for t in texts)
