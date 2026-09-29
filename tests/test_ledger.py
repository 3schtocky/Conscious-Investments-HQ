import pytest

from hq.engine.ledger import usage_cost


def test_sonnet_plain_usage():
    # 1M in @ $2 + 100k out @ $10 = $3.00
    assert usage_cost("claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 100_000}) == \
        pytest.approx(3.00)


def test_haiku_is_half_sonnet():
    u = {"input_tokens": 200_000, "output_tokens": 20_000}
    assert usage_cost("claude-haiku-4-5", u) == pytest.approx(usage_cost("claude-sonnet-5-5", u) / 2)


def test_cache_read_and_ttl_split_writes():
    u = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 1_000_000,
        "cache_creation_input_tokens": 2_000_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000,
                           "ephemeral_1h_input_tokens": 1_000_000},
    }
    # read 0.20 + 5m write 2.50 + 1h write 4.00
    assert usage_cost("claude-sonnet-5-5", u) == pytest.approx(6.70)


def test_cache_write_without_breakdown_uses_5m_rate():
    u = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 1_000_000}
    assert usage_cost("claude-haiku-4-5", u) == pytest.approx(1.25)


def test_unknown_model_raises():
    with pytest.raises(KeyError, match="No pricing"):
        usage_cost("claude-unknown", {"input_tokens": 1})
