from ragdemo.retrieve import W_GENERIC, W_ID, W_NUMBER, W_WORD, lexical_terms


def weights(question: str) -> dict[str, float]:
    return dict(lexical_terms(question))


def test_identifiers_get_the_highest_weight():
    w = weights("What is the total amount due on invoice INV-2025-0137?")
    assert w["INV-2025-0137"] == W_ID
    assert w["total"] == W_GENERIC and w["invoice"] == W_GENERIC
    assert "what" not in w and "the" not in w


def test_identifier_shapes():
    for ident in ("NC/2025/0912", "KS-RE-25-0318", "AE-TB-2.5", "SDP0144", "2025-0101", "PO-2025-0405", "BF-1001", "LL0417"):
        assert weights(f"Tell me about {ident} please")[ident] == W_ID, ident


def test_amounts_are_numbers_not_identifiers():
    w = weights("Which invoice has a total of 1,862.00 EUR?")
    assert w["1,862.00"] == W_NUMBER
    assert "eur" in w and w["eur"] == W_WORD


def test_content_words_keep_full_weight():
    w = weights("How many hydraulic hoses were ordered from Hallstrom?")
    assert w["hydraulic"] == W_WORD and w["hoses"] == W_WORD and w["hallstrom"] == W_WORD
    assert "how" not in w and "many" not in w and "were" not in w


def test_terms_sorted_by_weight():
    terms = lexical_terms("Due date of invoice AEC-0836 and the amount 500")
    assert [t for t, _ in terms][:2] == ["AEC-0836", "500"]
