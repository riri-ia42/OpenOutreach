"""Tarifs Claude API en USD par million de tokens (2026-05).

Cache read = 10% du prix input. Cache write = 125% du prix input.
"""
from __future__ import annotations


# (input, output) USD par million de tokens
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Claude 4 family
    "claude-opus-4-7":       (15.0, 75.0),
    "claude-opus-4-6":       (15.0, 75.0),
    "claude-opus-4-5":       (15.0, 75.0),
    "claude-sonnet-4-6":     (3.0, 15.0),
    "claude-sonnet-4-5":     (3.0, 15.0),
    "claude-haiku-4-5":      (1.0, 5.0),
    # Claude 3 family fallback
    "claude-3-5-sonnet":     (3.0, 15.0),
    "claude-3-5-haiku":      (0.8, 4.0),
    "claude-3-opus":         (15.0, 75.0),
}

DEFAULT_PRICING = (3.0, 15.0)  # fallback = Sonnet


def get_pricing(model: str) -> tuple[float, float]:
    """Retourne (input_usd_per_million, output_usd_per_million) pour un modele.

    Match par prefix pour gerer les suffixes type "-20251001"."""
    if not model:
        return DEFAULT_PRICING
    model = model.lower()
    # Strip vendor prefix / date suffix
    base = model.split("@")[0].split("[")[0]
    # Tentative match exact
    if base in MODEL_PRICING:
        return MODEL_PRICING[base]
    # Match par prefix (ex "claude-opus-4-7-20251001" -> "claude-opus-4-7")
    for key, price in MODEL_PRICING.items():
        if base.startswith(key):
            return price
    return DEFAULT_PRICING


def compute_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Calcule le cout en USD."""
    in_price, out_price = get_pricing(model)
    cost = (
        input_tokens / 1_000_000 * in_price
        + output_tokens / 1_000_000 * out_price
        + cache_creation_tokens / 1_000_000 * in_price * 1.25
        + cache_read_tokens / 1_000_000 * in_price * 0.10
    )
    return round(cost, 6)
