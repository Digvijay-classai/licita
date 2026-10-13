from licita.llm import pricing


def test_longest_prefix_and_batch_discount():
    t = pricing.load()
    assert t.find("mistral-small-2603").prefix == "mistral-small"
    full = t.cost("mistral-small-2603", 1_000_000, 1_000_000)
    assert full == t.find("mistral-small").input_per_m + t.find("mistral-small").output_per_m
    assert t.cost("mistral-small-2603", 1_000_000, 1_000_000, batch=True) == full * (1 - t.batch_discount)


def test_unknown_or_self_hosted_models_have_no_token_price():
    t = pricing.load()
    assert t.cost("mistralai/Mistral-Small-3.2-24B-Instruct-2506", 10, 10) is None
    assert t.cost("mistral-small-2603", None, 10) is None


def test_reports_carry_the_price_caveat():
    t = pricing.load()
    assert t.checked in t.caveat and ("NOT confirmed" in t.caveat) == (not t.verified)
