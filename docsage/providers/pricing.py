"""Per-million-token prices in USD (input, output). Used for budgets and cost reporting.

Anthropic prices are first-party list prices. Google and OpenAI prices are approximate list prices
and are labelled "estimated" in the UI. Unknown models are reported as unpriced (cost 0).
"""

from __future__ import annotations

PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # Google (estimated)
    "gemini-flash-latest": (0.30, 2.50),
    "gemini-flash-lite-latest": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-embedding-001": (0.15, 0.0),
    # OpenAI (estimated)
    "gpt-5.4": (2.50, 15.0),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5-mini": (0.25, 2.0),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}


def price_for(model: str) -> tuple[float, float] | None:
    name = model.removeprefix("models/")
    if name in PRICES:
        return PRICES[name]
    # Tolerate dated or suffixed variants, e.g. "gpt-5.4-mini-2026-03-17".
    for known, price in sorted(PRICES.items(), key=lambda kv: -len(kv[0])):
        if name.startswith(known):
            return price
    return None


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    price = price_for(model)
    if price is None:
        return 0.0
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
