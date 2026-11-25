import pytest

from app import config, pricing
from app.pricing import InvalidTokenCounts, TokenUsage


def test_hand_calculated_total():
    """input 10,000 (4,000 of them cached), output 2,000, reasoning 3,000.

    fresh input   6,000 x 3.00 USD/M  = 0.018000 USD
    cached input  4,000 x 0.30 USD/M  = 0.001200 USD
    output+reason 5,000 x 15.00 USD/M = 0.075000 USD
    total                              = 0.094200 USD = 94,200 micro-USD
    """
    u = TokenUsage(input_tokens=10_000, cached_input_tokens=4_000, output_tokens=2_000, reasoning_tokens=3_000)
    assert pricing.pico_to_micro(pricing.token_cost_pico(u)) == 94_200


def test_cached_tokens_are_cheaper_than_fresh():
    fresh = TokenUsage(input_tokens=1_000_000)
    cached = TokenUsage(input_tokens=1_000_000, cached_input_tokens=1_000_000)
    assert pricing.pico_to_micro(pricing.token_cost_pico(fresh)) == 3_000_000
    assert pricing.pico_to_micro(pricing.token_cost_pico(cached)) == 300_000


def test_reasoning_tokens_cost_exactly_like_output():
    as_output = TokenUsage(output_tokens=1_000)
    as_reasoning = TokenUsage(reasoning_tokens=1_000)
    assert pricing.token_cost_pico(as_output) == pricing.token_cost_pico(as_reasoning)
    assert pricing.token_cost_pico(as_reasoning) > 0, "reasoning must never be free"


def test_categories_are_not_just_added_together():
    u = TokenUsage(input_tokens=1_000, cached_input_tokens=400, output_tokens=100, reasoning_tokens=50)
    assert u.total_tokens == 1_150              # cached is inside input, not on top of it
    naive_sum_cost = u.total_tokens * config.INPUT_MICRO_USD_PER_MTOK
    assert pricing.token_cost_pico(u) != naive_sum_cost


def test_no_rounding_drift_on_tiny_events():
    """1,000 events of 1 fresh input token. Rounding each event to micro-USD would give 3 x 1000 = 3000 either
    way here, so use a price-sensitive case: cached tokens at 0.30 USD/M cost 0.3 micro-USD each."""
    one = TokenUsage(input_tokens=1, cached_input_tokens=1)
    per_event_rounded = pricing.pico_to_micro(pricing.token_cost_pico(one)) * 1_000   # 0 x 1000 = 0 (wrong)
    exact_total = pricing.pico_to_micro(pricing.token_cost_pico(one) * 1_000)         # 300 (right)
    assert per_event_rounded == 0
    assert exact_total == 300


def test_api_call_cost():
    assert pricing.pico_to_micro(pricing.api_call_cost_pico(1)) == config.API_CALL_MICRO_USD


def test_rounding_is_half_up():
    assert pricing.pico_to_micro(499_999) == 0
    assert pricing.pico_to_micro(500_000) == 1
    assert pricing.pico_to_micro(1_500_000) == 2


def test_everything_is_an_int():
    u = TokenUsage(input_tokens=123, cached_input_tokens=23, output_tokens=7, reasoning_tokens=9)
    assert isinstance(pricing.token_cost_pico(u), int)
    assert isinstance(pricing.pico_to_micro(pricing.token_cost_pico(u)), int)


@pytest.mark.parametrize(
    "bad",
    [
        TokenUsage(input_tokens=-1),
        TokenUsage(input_tokens=10, cached_input_tokens=11),
        TokenUsage(output_tokens=config.MAX_TOKENS_PER_REQUEST + 1),
        TokenUsage(output_tokens=1.5),
        TokenUsage(output_tokens=True),
    ],
)
def test_invalid_token_counts_rejected(bad):
    with pytest.raises(InvalidTokenCounts):
        pricing.token_cost_pico(bad)


def test_format_usd():
    assert pricing.format_usd(94_200) == "$0.094200"
    assert pricing.format_usd(1_234_500) == "$1.234500"
