"""Money math. Integers only.

Unit: pico-USD (1e-12 USD). Prices are micro-USD per million tokens, so
    cost_pico = tokens * price_micro_per_mtok          (exact, no division, no float)
because  tokens/1e6 * price_micro * 1e6 pico/micro  ==  tokens * price_micro.

Events store exact pico-USD. A rollup sums those exact integers and rounds ONCE (half up) to
micro-USD, so there is no per-event rounding drift.
"""
from __future__ import annotations

from dataclasses import dataclass

from app import config

PICO_PER_MICRO = 1_000_000
MICRO_PER_USD = 1_000_000


class InvalidTokenCounts(ValueError):
    """Raised for negative, non-integer, oversized or inconsistent token counts."""


@dataclass(frozen=True)
class TokenUsage:
    """Token convention (follows Gemini's usage metadata):

    input_tokens         total prompt tokens, INCLUDING cached ones
    cached_input_tokens  the cached part of input_tokens (must be <= input_tokens)
    output_tokens        visible output
    reasoning_tokens     hidden thinking tokens, billed as output
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def validate(self) -> None:
        for name in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):  # bool is an int in Python: reject it
                raise InvalidTokenCounts(f"{name} must be an integer")
            if value < 0:
                raise InvalidTokenCounts(f"{name} must not be negative")
            if value > config.MAX_TOKENS_PER_REQUEST:
                raise InvalidTokenCounts(f"{name} must be at most {config.MAX_TOKENS_PER_REQUEST}")
        if self.cached_input_tokens > self.input_tokens:
            raise InvalidTokenCounts("cached_input_tokens cannot exceed input_tokens (cached is a part of input)")

    @property
    def fresh_input_tokens(self) -> int:
        return self.input_tokens - self.cached_input_tokens

    @property
    def billable_output_tokens(self) -> int:
        return self.output_tokens + self.reasoning_tokens  # reasoning bills as output

    @property
    def total_tokens(self) -> int:
        # cached tokens are already inside input_tokens: adding them again would double count
        return self.input_tokens + self.output_tokens + self.reasoning_tokens


def token_cost_pico(u: TokenUsage) -> int:
    u.validate()
    return (
        u.fresh_input_tokens * config.INPUT_MICRO_USD_PER_MTOK
        + u.cached_input_tokens * config.CACHED_INPUT_MICRO_USD_PER_MTOK
        + u.billable_output_tokens * config.OUTPUT_MICRO_USD_PER_MTOK
    )


def api_call_cost_pico(calls: int) -> int:
    return calls * config.API_CALL_MICRO_USD * PICO_PER_MICRO


def pico_to_micro(pico: int) -> int:
    """Round half up, once, at the very end (never per event)."""
    return (pico + PICO_PER_MICRO // 2) // PICO_PER_MICRO


def format_usd(micro_usd: int) -> str:
    """Display only. 1_234_500 -> '$1.234500'."""
    return f"${micro_usd // MICRO_PER_USD}.{micro_usd % MICRO_PER_USD:06d}"
