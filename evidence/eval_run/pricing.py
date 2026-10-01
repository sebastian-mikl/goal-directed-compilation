"""Pinned per-token pricing table. Pricing is PER MILLION TOKENS, in USD.

These values are recorded in provenance.json at run start so the dollar cost of
every cell can be replayed offline. The default model is the Network's Call-1
model (gdc/llm.py -> MODEL_CALL1 -> claude-sonnet-4-5). Add new entries when
the run config changes — never silently fall back to "free".
"""
from __future__ import annotations

# {model_id: {"input": $/Mtok, "output": $/Mtok}}
PRICING_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00},
}

# Prompt-caching multipliers, applied to the model's base INPUT rate (S21a).
# A cache write (cache_creation_input_tokens) is billed ~1.25x the base input rate;
# a cache read (cache_read_input_tokens) is billed ~0.1x. Recorded in provenance.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

# Minimum cacheable prefix size (tokens). Below this, caching silently does not
# engage — the harness logs a warning if a system's static prefix is under it.
# Sonnet/Opus = 1024; Haiku = 2048.
MIN_CACHEABLE_TOKENS: dict[str, int] = {
    "claude-sonnet-4-5": 1024,
    "claude-haiku-4-5":  2048,
}


def min_cacheable_tokens(model: str) -> int:
    return MIN_CACHEABLE_TOKENS.get(model, 1024)


def cache_multipliers() -> dict[str, float]:
    return {"cache_write": CACHE_WRITE_MULTIPLIER, "cache_read": CACHE_READ_MULTIPLIER}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """CACHE-BLIND $ — treats all input as full-rate. `input_tokens` is the RAW
    logical input (uncached + cache_write + cache_read). This is the apples-to-
    apples cost figure used for the architecture-level comparison."""
    p = PRICING_USD_PER_MTOK.get(model)
    if p is None:
        raise KeyError(f"unpriced model {model!r} — add to PRICING_USD_PER_MTOK")
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000.0


def cost_usd_cached(model: str, uncached_input: int, cache_write: int,
                    cache_read: int, output_tokens: int) -> float:
    """CACHE-AWARE $ — the real bill: uncached input at full rate, cache writes at
    1.25x, cache reads at 0.1x, output at the output rate."""
    p = PRICING_USD_PER_MTOK.get(model)
    if p is None:
        raise KeyError(f"unpriced model {model!r} — add to PRICING_USD_PER_MTOK")
    inp = p["input"]
    return (uncached_input * inp
            + cache_write * inp * CACHE_WRITE_MULTIPLIER
            + cache_read * inp * CACHE_READ_MULTIPLIER
            + output_tokens * p["output"]) / 1_000_000.0
