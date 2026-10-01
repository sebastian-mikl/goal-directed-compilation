"""Shared S21 types — kept dataclass-free for trivial JSONL serialisation."""
from __future__ import annotations

from typing import Any, Literal

# Sentinel returned by an adapter that declined / produced no parseable result.
REFUSAL = "__REFUSAL__"

# Per-domain: the register's real id-typed column -> the generic alias row-list/
# ordered-list comparisons key on. adapters/base.py::df_to_result() renames TO the
# alias; comparator.py recognises any of the alias values as "this gold record is a
# row-list, not a grouped-aggregate". A new domain needs exactly one new entry here
# -- nothing else in either file changes.
ID_COLUMN_ALIASES = {"Employee ID": "employee_id"}

# Status of one adapter call.
Status = Literal["completed", "refused", "crashed", "timeout"]

# Outcome class per (system, query, seed), set by the comparator.
OutcomeClass = Literal[
    "correct",                # answered and matched gold (or refused on out_of_range)
    "silent_wrong",           # confident answer that doesn't match gold
    "wrong_flagged",          # answered but flagged uncertainty; flag was right
    "refused_should_answer",  # refused on an in-range query
    "crash",                  # adapter raised
    "timeout",                # bound (max_iters / max_retries / wall) hit
]

# Lightweight failure taxonomy attached to non-correct in-range outcomes.
FailureTag = Literal[
    "under_declaration",     # filter too restrictive -> too few rows
    "over_interpretation",   # added a filter not requested -> too many or wrong rows
    "arithmetic",            # right shape, wrong number
    "hallucinated_column",   # referenced a column that doesn't exist
    "gave_up",               # produced nothing / refused
    "other",
]


def empty_run_result() -> dict[str, Any]:
    """A canonical empty RunResult, copied + filled by each adapter."""
    return {
        "result": None,           # scalar | list-of-records | REFUSAL
        "llm_calls": 0,
        "input_tokens": 0,        # RAW logical input = uncached + cache_write + cache_read
        "uncached_input_tokens": 0,
        "cache_write_tokens": 0,  # cache_creation_input_tokens
        "cache_read_tokens": 0,   # cache_read_input_tokens
        "output_tokens": 0,
        "per_call_tokens": [],    # list[{in(raw), out, uncached, cache_write, cache_read}]
        "wall_time_s": 0.0,
        "artifact": None,         # goal/plan/code/SQL/trace
        "status": "completed",
        "error": None,
    }
