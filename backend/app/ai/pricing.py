"""Estimated cost per call, for cost tracking (spec section 69).

Paid-tier list prices in USD per 1M tokens, from https://ai.google.dev/gemini-api/docs/pricing
as read on 2026-09-24. Free-tier calls cost nothing, but are still estimated so usage
can be planned. Update this table when prices change.
"""

from decimal import Decimal

# model: (input per 1M tokens, output per 1M tokens)
PRICES_PER_MILLION: dict[str, tuple[Decimal, Decimal]] = {
    "gemini-3.8-flash": (Decimal("0.75"), Decimal("3.75")),  # rises to 1.50 / 7.50 on 2027-01-01
    "gemini-3.5-flash": (Decimal("1.50"), Decimal("9.00")),
    "gemini-3.5-flash-lite": (Decimal("0.30"), Decimal("2.50")),
    "gemini-3.1-flash-lite": (Decimal("0.25"), Decimal("1.50")),
    "gemini-3.1-pro-preview": (Decimal("2.00"), Decimal("12.00")),
    "gemini-embedding-2": (Decimal("0.20"), Decimal("0")),
}

_MILLION = Decimal(1_000_000)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    """None when the model isn't in the table, rather than a made-up number."""
    prices = PRICES_PER_MILLION.get(model.removeprefix("models/"))
    if prices is None:
        return None
    input_price, output_price = prices
    return (input_price * input_tokens + output_price * output_tokens) / _MILLION
