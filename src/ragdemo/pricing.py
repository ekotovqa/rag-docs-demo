"""List prices per million tokens, Anthropic API, November 2026. Edit here when the price list changes."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.10, 2.50),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, 0.125),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float | None:
    """None when the model is not in the table: better no number than a wrong one."""
    price = PRICES.get(model)
    if price is None:
        return None
    uncached = max(input_tokens - cache_read_tokens - cache_write_tokens, 0)
    return (uncached * price.input + cache_read_tokens * price.cache_read
            + cache_write_tokens * price.cache_write + output_tokens * price.output) / 1_000_000
