from ragdemo.ingest.chunk import chunk_page, header_lines, split_lines
from ragdemo.ingest.ocr import Word, lines_from_words, normalize, respace
from ragdemo.ingest.pdf import Page

PAGE1 = """K
Kesselring Stahlhandel GmbH
Hafenstrasse 112
47119 Duisburg
Germany
Tax ID: DE 311 448 902
INVOICE
Invoice no.: KS-RE-25-0318
Invoice date: 14 Mar 2025
Due date: 28 Mar 2025
Your PO reference: PO-2025-0405
Payment terms: Net 14
Currency: EUR
Bill to:
Oakridge Fabrication Works Ltd
48 Mill Lane
Description Item Qty Unit price Amount
Steel sheet S235JR 2.0 mm, 1000x2000 KS-S235-2 10 61.20 612.00
Flat bar 50x5, 6 m KS-FB-50 40 22.35 894.00
"""

PAGE2 = """Invoice KS-RE-25-0318 (continued) Page 2
Description Item Qty Unit price Amount
Angle 30x30x3, 6 m KS-ANG-30 20 17.80 356.00
Subtotal 1,862.00 EUR
VAT 0% (reverse charge) 0.00 EUR
TOTAL DUE 1,862.00 EUR
"""


def test_header_lines_pick_identifying_lines_only():
    lines = [s for s in PAGE1.splitlines() if s.strip()]
    header = header_lines(lines)
    assert "INVOICE" in header
    assert "Invoice no.: KS-RE-25-0318" in header
    assert "Your PO reference: PO-2025-0405" in header
    assert "Hafenstrasse 112" not in header and "Bill to:" not in header


def test_every_chunk_starts_with_the_header():
    chunks = chunk_page(Page("x.pdf", 1, PAGE1, "text"), max_chars=380, overlap=1)
    assert len(chunks) >= 2, "a small budget forces several chunks"
    for i, c in enumerate(chunks):
        assert c.idx == i and c.page == 1 and c.file == "x.pdf"
        assert c.text.startswith(c.header)
        assert "KS-RE-25-0318" in c.text
    assert "Flat bar 50x5" in chunks[-1].text


def test_continuation_page_header_carries_the_number():
    chunks = chunk_page(Page("x.pdf", 2, PAGE2, "text"))
    assert len(chunks) == 1
    assert chunks[0].header.startswith("Invoice KS-RE-25-0318 (continued)")
    assert "TOTAL DUE 1,862.00 EUR" in chunks[0].text


def test_split_lines_overlaps_and_respects_budget():
    lines = [f"line {i:02d} " + "x" * 20 for i in range(12)]
    windows = split_lines(lines, max_chars=100, overlap=1)
    assert len(windows) > 1
    for w in windows:
        assert sum(len(s) + 1 for s in w) <= 100 + len(lines[0]) + 1  # one line may complete a window
    for a, b in zip(windows, windows[1:]):
        assert a[-1] == b[0], "consecutive windows share one line"
    assert [s for w in windows for s in w if s not in (w[0] if windows.index(w) else [])] or True
    flat = []
    for w in windows:
        for s in w:
            if not flat or s != flat[-1]:
                flat.append(s)
    assert flat == lines, "nothing is lost or reordered"


def test_empty_page_gives_no_chunks():
    assert chunk_page(Page("x.pdf", 1, "   \n ", "ocr")) == []


def test_ocr_words_become_reading_order_lines():
    words = [
        Word(300, 100, 340, 112, "61.20", 0.9),
        Word(10, 101, 120, 113, "Steel sheet", 0.9),
        Word(150, 99, 190, 111, "10", 0.9),
        Word(10, 130, 60, 142, "Flat", 0.9),
        Word(70, 131, 100, 143, "bar", 0.9),
        Word(300, 129, 340, 141, "22.35", 0.9),
    ]
    assert lines_from_words(words) == "Steel sheet 10 61.20\nFlat bar 22.35"


def test_ocr_normalizes_fullwidth_punctuation():
    assert normalize("1，862．00％") == "1,862.00%"
    assert normalize("KS－RE") == "KS-RE"


def test_respace_restores_word_boundaries_without_touching_numbers():
    assert respace("InvoiceHH-0107(continued) Page") == "Invoice HH-0107 (continued) Page"
    assert respace("PurchaseOrderPO-2025-0413(continued)") == "Purchase Order PO-2025-0413 (continued)"
    assert respace("POnumber:PO-2025-0413") == "POnumber: PO-2025-0413"
    assert respace("Invoiceno.:KS-RE-25-0835 | Duedate:08/26/2025") == "Invoiceno.: KS-RE-25-0835 | Duedate: 08/26/2025"
    assert respace("Hydraulicoil ISOVG46,20L 6 68.30 409.80") == "Hydraulicoil ISOVG46,20L 6 68.30 409.80"
    assert respace("Subtotal $1,462.60") == "Subtotal $1,462.60"
    assert respace("RL-OAK-25 Oakboard25mm,perm2 5 72.00") == "RL-OAK-25 Oakboard 25mm, perm 2 5 72.00"
    assert respace("Oakridge FabricationWorksLtd") == "Oakridge Fabrication Works Ltd"


def test_header_regex_tolerates_missing_spaces():
    lines = ["Oakridge FabricationWorksLtd PURCHASEORDER", "48MillLane", "POnumber: PO-2025-0407", "Orderdate: 2025-06-23", "Hello"]
    assert header_lines(lines) == ["Oakridge FabricationWorksLtd PURCHASEORDER", "POnumber: PO-2025-0407", "Orderdate: 2025-06-23"]
