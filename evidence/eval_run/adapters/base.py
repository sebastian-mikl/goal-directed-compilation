"""Adapter base class + shared post-processing.

Every adapter normalises its raw result into ONE of:
  - REFUSAL (sentinel)               — adapter declined / produced nothing
  - a scalar (int/float)             — for gold result_kind == "scalar"
  - a list-of-records [dict, …]      — for table / ordered_list

The register's real id column is RENAMED to its generic alias (ID_COLUMN_ALIASES)
on row-returning frames so the comparator's key matches gold's, regardless of which
domain is in play. Extra columns are tolerated — projecting to only the columns the
comparator needs happens INSIDE the comparator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..tools_code import index_to_columns
from ..types import ID_COLUMN_ALIASES, REFUSAL, empty_run_result


@dataclass
class Adapter:
    name: str
    client: Any
    register: pd.DataFrame
    briefing: dict                       # {description, column_descriptions, business_rules, columns}
    model: str
    temperature: float | None = None     # API default if None
    max_tokens: int = 1500
    max_iters: int = 15                  # react bound
    max_retries: int = 3                 # code_agent bound
    timeout_s: float | None = None       # text_to_sql bound (wall-clock, per query)

    def run(self, query: str, seed: int) -> dict:
        """Execute one (query, seed) cell.

        `seed` is a repetition-index label, not a determinism control: the
        Anthropic API has no seed parameter, and temperature=0 (set for the
        network/planner_upfront Call-1 emission) reduces but does not
        guarantee reproducibility even when set. Repeated cells are
        independent samples, not controlled replays.
        """
        raise NotImplementedError

    # ── static cacheable prefix (built ONCE per system, reused byte-for-byte) ──

    def _build_system(self) -> str:
        """The static system prompt (briefing + instructions). Identical across
        every query and seed — overridden by each adapter."""
        raise NotImplementedError

    @property
    def system_text(self) -> str:
        cached = self.__dict__.get("_system_text")
        if cached is None:
            cached = self._build_system()
            self.__dict__["_system_text"] = cached
        return cached

    def cache_prefix_text(self) -> str:
        """The static SYSTEM text. The network adapter overrides this with the
        prefix llm1 actually emits."""
        return self.system_text

    def cache_tools(self):
        """Tool definitions that precede `system` in the cached prefix (the API
        cache order is tools -> system -> messages, so a breakpoint on the last
        system block caches the tools too). None if the adapter sends no tools.
        Used only by the harness's minimum-cacheable-size estimate."""
        return None

    # ── four-category token accounting (S21a) ─────────────────────────────────

    def _account(self, rec: dict, resp=None) -> None:
        """Fold one call's usage into the RunResult ledger. Reads the four token
        categories from `resp` when given (the adapter holds the resp), else from
        the client wrapper's last_ledger() (the network adapter's llm1 swallows
        the resp). Updates per-call + cumulative buckets and the raw logical input."""
        led = (ledger_from_resp(resp) if resp is not None
               else _ledger_from_client(self.client))
        raw = led["uncached"] + led["cache_write"] + led["cache_read"]
        rec["uncached_input_tokens"] += led["uncached"]
        rec["cache_write_tokens"]    += led["cache_write"]
        rec["cache_read_tokens"]     += led["cache_read"]
        rec["input_tokens"]          += raw
        rec["output_tokens"]         += led["output"]
        rec["per_call_tokens"].append({
            "in": raw, "out": led["output"], "uncached": led["uncached"],
            "cache_write": led["cache_write"], "cache_read": led["cache_read"]})

    def _temp_kwargs(self) -> dict:
        """messages.create kwargs for temperature. Returns {} when temperature is
        None — the API-default behavior of the recorded baseline runs, byte-identical
        to omitting the argument — and {'temperature': t} only when a temperature is
        explicitly set (e.g. the temperature-confound probe runs the baselines at 0).
        Wiring this here lets a caller run react/code_agent/text_to_sql at a chosen
        temperature without changing any default-temperature result."""
        return {} if self.temperature is None else {"temperature": self.temperature}


# ── shared helpers ──────────────────────────────────────────────────────────

_DECLINE_PATTERNS = re.compile(
    r"\b(cannot_answer|can(?:not|'t)|unable to|don'?t (?:have|know)|won'?t|"
    r"not possible|i refuse|i decline|out of (?:scope|range)|"
    r"not (?:answerable|supported)|impossible)\b",
    re.IGNORECASE,
)
# `cannot_answer` explicitly catches the CANNOT_ANSWER decline sentinel the
# DECLINE_AFFORDANCE experiment (refuse_config S48) tells the baselines to emit —
# the underscore is a word char, so \bcannot\b alone does NOT match "CANNOT_ANSWER".
# Additive and provably non-regressing: is_decline_text is used only at run time
# inside the baseline adapters (never by the offline comparator), and no existing
# recorded emission contains the token, so no recorded score changes.


def is_decline_text(text: str | None) -> bool:
    return bool(text and _DECLINE_PATTERNS.search(text))


def df_to_result(obj) -> Any:
    """Normalise a DataFrame / Series / scalar / None to the comparator's input shape.

    Renames the register's real id column to its generic alias if present
    (ID_COLUMN_ALIASES — e.g. Employee ID -> employee_id).
    Tolerates extra columns (the comparator projects to the columns it needs).
    Returns REFUSAL for None / empty / NaN scalar.

    For Series inputs (e.g. a groupby().mean() with the group key in the index):
    reset_index() so the index becomes a key COLUMN before records conversion —
    otherwise to_dict("records") silently drops the key and the comparator sees
    values without their grouping key. DataFrames get the same treatment when
    their index carries labels (named, MultiIndex, or non-integer: groupby().agg(),
    pd.DataFrame({'Count': s.value_counts()}), set_index()); a positional integer
    index is left to be dropped, as before (tools_code.index_to_columns).
    """
    if obj is None:
        return REFUSAL
    if isinstance(obj, pd.Series):
        obj = obj.reset_index()
    elif isinstance(obj, pd.DataFrame):
        obj = index_to_columns(obj)
    if isinstance(obj, pd.DataFrame):
        if obj.empty and obj.shape[1] == 0:
            return REFUSAL
        f = obj.rename(columns=ID_COLUMN_ALIASES)
        # 1x1 frame -> scalar
        if f.shape == (1, 1):
            return _coerce_scalar(f.iat[0, 0])
        if f.shape[0] == 1 and f.shape[1] >= 1:
            # one row, multi-col -> a list-of-records w/ 1 row (kept as table)
            return f.to_dict("records")
        return f.to_dict("records")
    # plain scalar
    return _coerce_scalar(obj)


def _coerce_scalar(x):
    try:
        import numpy as np
        if isinstance(x, (np.floating, np.integer)): return float(x)
    except Exception:
        pass
    if isinstance(x, (int, float)) and not isinstance(x, bool): return float(x)
    if x is None: return REFUSAL
    if isinstance(x, str):
        s = x.strip()
        try:
            return float(s.replace(",", ""))
        except Exception:
            return s
    return x


def ledger_from_resp(resp) -> dict:
    """Extract the four token categories from an Anthropic response usage object.
    `input_tokens` on the usage is the UNCACHED input (per-query content); the two
    cache fields default to 0 when caching did not engage or the field is absent."""
    u = getattr(resp, "usage", None)
    if u is None:
        return {"uncached": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    return {
        "uncached":    int(getattr(u, "input_tokens", 0) or 0),
        "cache_write": int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        "cache_read":  int(getattr(u, "cache_read_input_tokens", 0) or 0),
        "output":      int(getattr(u, "output_tokens", 0) or 0),
    }


def _ledger_from_client(client) -> dict:
    fn = getattr(client, "last_ledger", None)
    if fn is None:
        return {"uncached": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    return fn()


def estimate_tokens(text: str) -> int:
    """Cheap, API-free token estimate (~4 chars/token) for the minimum-cacheable
    size warning. The authoritative signal is the live cache_creation count."""
    return len(text or "") // 4


def usage_to_tokens(resp) -> tuple[int, int]:
    """Back-compat: (raw_input, output). raw_input = uncached + cache_write + cache_read."""
    led = ledger_from_resp(resp)
    return led["uncached"] + led["cache_write"] + led["cache_read"], led["output"]


def new_result() -> dict:
    return empty_run_result()


# ── transport/API error classification (S24 crash-safe resume) ───────────────

def is_transport_error(exc: BaseException) -> bool:
    """True if `exc` is an Anthropic API / transport error — a TRANSIENT failure
    (rate limit, connection drop, auth, insufficient credits, overload, timeout)
    that must NOT be persisted as a completed cell. The harness re-raises these so
    the run aborts cleanly WITHOUT writing the in-flight cell; on resume the cell
    retries instead of being skipped as "done".

    A non-transport exception (a bug in adapter/executor logic given a valid
    response) is a reproducible cell OUTCOME — it stays status=crashed and is
    recorded, so one buggy cell never blocks the whole run.

    Detected by the anthropic exception hierarchy when importable, with a
    module/name heuristic fallback (robust to SDK class-shape changes)."""
    try:
        import anthropic
        # APIError is the base of APIStatusError / APIConnectionError /
        # RateLimitError / AuthenticationError / BadRequestError / ...
        if isinstance(exc, anthropic.APIError):
            return True
    except Exception:                                     # noqa: BLE001
        pass
    mod = (type(exc).__module__ or "")
    name = type(exc).__name__
    return mod.split(".")[0] == "anthropic" or name in {
        "APIError", "APIStatusError", "APIConnectionError", "APITimeoutError",
        "RateLimitError", "InternalServerError", "AuthenticationError",
        "PermissionDeniedError", "BadRequestError", "OverloadedError",
        "ServiceUnavailableError", "APIResponseValidationError",
    }
