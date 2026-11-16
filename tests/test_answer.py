from types import SimpleNamespace

import pytest

from ragdemo.answer import FALLBACK_BETA, REFUSAL_TOKEN, Answerer, display_text, document_blocks, is_refusal, parse_response
from ragdemo.pricing import cost_usd
from ragdemo.retrieve import Retrieval
from ragdemo.store import Hit


def hit(i: int, file: str, page: int, text: str = "chunk text", source: str = "text") -> Hit:
    return Hit(i, file, page, 0, text, source, 0.03, i, 0.2, None, None)


HITS = [hit(1, "INV-2025-0137.pdf", 1, "INVOICE | Invoice no.: INV-2025-0137\nSubtotal 1,200.00"),
        hit(2, "INV-2025-0137.pdf", 2, "Invoice INV-2025-0137 (continued)\nTOTAL DUE 1,440.00 USD"),
        hit(3, "PO-2025-0401.pdf", 1, "PURCHASE ORDER | PO number: PO-2025-0401", "ocr")]


def text_block(text, citations=None):
    return SimpleNamespace(type="text", text=text, citations=citations)


def citation(index, cited):
    return SimpleNamespace(type="char_location", document_index=index, cited_text=cited, document_title="x")


def test_document_blocks_carry_title_context_and_citations():
    blocks = document_blocks(HITS)
    assert [b["title"] for b in blocks] == ["INV-2025-0137.pdf, page 1", "INV-2025-0137.pdf, page 2", "PO-2025-0401.pdf, page 1"]
    assert all(b["citations"] == {"enabled": True} for b in blocks)
    assert blocks[2]["context"].endswith("text from OCR.")
    assert blocks[0]["source"] == {"type": "text", "media_type": "text/plain", "data": HITS[0].text}


def test_parse_response_numbers_citations_and_maps_pages():
    content = [
        text_block("The total due on invoice INV-2025-0137 is "),
        text_block("1,440.00 USD", [citation(1, "TOTAL DUE 1,440.00  USD")]),
        text_block(", with a subtotal of "),
        text_block("1,200.00", [citation(0, "Subtotal 1,200.00"), citation(1, "TOTAL DUE 1,440.00  USD")]),
        text_block("."),
    ]
    text, cites, raw = parse_response(content, HITS)
    assert text == "The total due on invoice INV-2025-0137 is 1,440.00 USD[1], with a subtotal of 1,200.00[2][1]."
    assert raw == "The total due on invoice INV-2025-0137 is 1,440.00 USD, with a subtotal of 1,200.00."
    assert [(c.n, c.file, c.page, c.quote) for c in cites] == [
        (1, "INV-2025-0137.pdf", 2, "TOTAL DUE 1,440.00 USD"),
        (2, "INV-2025-0137.pdf", 1, "Subtotal 1,200.00"),
    ]
    assert cites[0].chunk_id == 2


def test_parse_response_ignores_out_of_range_and_non_text_blocks():
    content = [SimpleNamespace(type="thinking", thinking=""), text_block("x", [citation(99, "nope")])]
    text, cites, _ = parse_response(content, HITS)
    assert text == "x" and cites == []


def test_refusal_detection_and_display():
    assert is_refusal(f"  {REFUSAL_TOKEN} The documents do not list a phone number.")
    assert not is_refusal("The total is 1,440.00 USD.")
    assert display_text(f"{REFUSAL_TOKEN}: The documents do not list a phone number.") == \
        "Not in the documents. The documents do not list a phone number."
    assert display_text(REFUSAL_TOKEN) == "Not in the documents."


class FakeRetriever:
    def __init__(self, hits):
        self.hits = hits

    def search(self, question, k=None):
        return Retrieval(question, self.hits, [("INV-2025-0137", 3.0)], 10.0, 90.0)


class FakeMessages:
    def __init__(self, response):
        self.response, self.calls = response, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_client(response):
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(response)))


def response_with(content, model="claude-opus-5-5", stop="end_turn", inp=3000, out=60, cache_read=0):
    return SimpleNamespace(content=content, model=model, stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out,
                                                 cache_read_input_tokens=cache_read, cache_creation_input_tokens=0))


def test_ask_builds_request_and_answer():
    resp = response_with([text_block("1,440.00 USD", [citation(1, "TOTAL DUE 1,440.00 USD")])])
    client = fake_client(resp)
    a = Answerer(FakeRetriever(HITS), client=client, model="claude-opus-5-5", effort="medium", fallbacks=True)
    result = a.ask("What is the total due on invoice INV-2025-0137?")
    call = client.beta.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": "medium"}
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert len(call["messages"][0]["content"]) == len(HITS) + 1
    assert call["messages"][0]["content"][-1]["text"].startswith("Question: ")
    assert result.answer == "1,440.00 USD[1]" and not result.refused
    assert result.citations[0].page == 2
    assert result.cost_usd == pytest.approx(cost_usd("claude-opus-5-5", 3000, 60))
    assert result.retrieval_ms == 100.0 and result.model == "claude-opus-5-5"


def test_ask_without_fallbacks_sends_no_beta():
    client = fake_client(response_with([text_block("x")]))
    Answerer(FakeRetriever(HITS), client=client, fallbacks=False).ask("q")
    call = client.beta.messages.calls[0]
    assert "betas" not in call and "fallbacks" not in call


def test_ask_marks_refusals_and_fallback_model():
    client = fake_client(response_with([text_block(f"{REFUSAL_TOKEN} No phone number is printed.")], model="claude-opus-4-8"))
    result = Answerer(FakeRetriever(HITS), client=client, fallbacks=True).ask("phone?")
    assert result.refused and result.answer.startswith("Not in the documents.")
    assert result.model == "claude-opus-4-8" and result.requested_model != result.model
    assert result.cost_usd is None, "unknown model: no price rather than a wrong price"


def test_safety_refusal_stop_reason():
    client = fake_client(response_with([], stop="refusal"))
    result = Answerer(FakeRetriever(HITS), client=client).ask("q")
    assert result.refused and "declined" in result.answer


def test_no_hits_means_no_model_call():
    client = fake_client(response_with([text_block("x")]))
    result = Answerer(FakeRetriever([]), client=client).ask("q")
    assert result.refused and client.beta.messages.calls == [] and result.cost_usd == 0.0


def test_missing_key_is_a_clear_error(monkeypatch):
    from ragdemo import answer as mod

    monkeypatch.setattr(mod, "settings", lambda: SimpleNamespace(anthropic_api_key=None, ragdemo_model="m", ragdemo_effort="e", ragdemo_fallbacks=True))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        Answerer(FakeRetriever(HITS)).ask("q")


def test_pricing():
    assert cost_usd("claude-opus-5-5", 1_000_000, 0) == 4.0
    assert cost_usd("claude-opus-5-5", 1_000_000, 100_000, cache_read_tokens=500_000) == pytest.approx(0.5 * 4 + 0.5 * 0.2 + 0.1 * 20)
    assert cost_usd("claude-haiku-5-5", 10_000, 1_000) == pytest.approx(0.001 + 0.0005)
    assert cost_usd("unknown", 1, 1) is None
