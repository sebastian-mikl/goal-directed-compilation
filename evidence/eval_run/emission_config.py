"""S33 — configurable Call-1 emission model (the downsizing probe).

The network and planner each emit their goal/plan in a single Call-1; this knob lets
that emission call run on a smaller model (e.g. Haiku) for a {network,planner} x
{Sonnet,Haiku} comparison, with NOTHING else changed (same prompts, same engine, same
comparator). The other three systems (react, code_agent, text_to_sql) are not part of
the probe and always use the run's default model.

Resolution order: explicit override (run.py --emission-model) > EMISSION_MODEL env >
the run's default model. The default (knob unset) is byte-identical to prior behaviour.
"""
from __future__ import annotations

import os

from .pricing import PRICING_USD_PER_MTOK

EMISSION_MODEL_ENV = "EMISSION_MODEL"

# The single-shot Call-1 emission systems whose model this knob controls.
EMISSION_SYSTEMS: tuple[str, ...] = ("network", "planner")


def resolve_emission_model(default_model: str, *, override: str | None = None) -> str:
    """Resolve the Call-1 emission model id. Never silently fall back to an unpriced
    model — mirrors the pricing table's no-free-fallback rule, so a typo fails loudly
    instead of producing a $0 run."""
    model = override or os.environ.get(EMISSION_MODEL_ENV) or default_model
    if model not in PRICING_USD_PER_MTOK:
        raise KeyError(
            f"emission model {model!r} is not in PRICING_USD_PER_MTOK — add its pricing "
            f"before running (known: {sorted(PRICING_USD_PER_MTOK)})")
    return model
