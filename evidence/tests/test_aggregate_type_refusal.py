"""S36 FIX 2a — non-count aggregate over a non-numeric column refuses (typed
AggregateTypeError → existing refusal mapping in every adapter), instead of
silently producing NaN. Subclassing FilterTypeError means the adapter
mappings (network/planner/react/code_agent/text_to_sql via the shared
`_apply_one`) all treat AggregateTypeError as a refusal with zero extra
wiring — fairness-neutral across systems.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from gdc.primitives import (
    AggregateTypeError, FilterTypeError, prim_aggregate)

ROOT = Path(__file__).resolve().parents[2]


# ── primitive-level rule ────────────────────────────────────────────────────

def _frame():
    return pd.DataFrame({
        "Group": ["A", "A", "B", "B"],
        "Cost":  [10.0, 20.0, 30.0, 40.0],
        "Title": ["alpha", "beta", "gamma", "delta"],
    })


@pytest.mark.parametrize("agg", ["sum", "mean", "min", "max"])
def test_2a_global_aggregate_on_text_refuses(agg):
    """Global (group_by=[]) non-count aggregate over a text column must refuse,
    not silently produce NaN."""
    with pytest.raises(AggregateTypeError):
        prim_aggregate(_frame(), {"group_by": [], "value_col": "Title", "agg": agg})


@pytest.mark.parametrize("agg", ["sum", "mean", "min", "max"])
def test_2a_grouped_aggregate_on_text_refuses(agg):
    """Grouped non-count aggregate over a text column must refuse."""
    with pytest.raises(AggregateTypeError):
        prim_aggregate(_frame(), {"group_by": "Group", "value_col": "Title", "agg": agg})


def test_2a_count_on_text_executes_normally():
    """count is exempt — counting non-null values of any column is type-agnostic."""
    out = prim_aggregate(_frame(), {"group_by": [], "value_col": "Title", "agg": "count"})
    assert int(out.iloc[0]["Title"]) == 4
    out = prim_aggregate(_frame(), {"group_by": "Group", "value_col": "Title", "agg": "count"})
    assert sorted(out["Title"].tolist()) == [2, 2]


def test_2a_numeric_aggregate_executes_normally():
    """Existing behaviour preserved for clean numeric columns."""
    out = prim_aggregate(_frame(), {"group_by": [], "value_col": "Cost", "agg": "sum"})
    assert float(out.iloc[0]["Cost"]) == 100.0
    out = prim_aggregate(_frame(), {"group_by": "Group", "value_col": "Cost", "agg": "mean"})
    assert sorted(out["Cost"].tolist()) == [15.0, 35.0]


def test_2a_numeric_with_stray_nan_still_executes():
    """A clean numeric column with isolated NaN/None must still aggregate (only
    fully-uncoercible columns refuse)."""
    f = _frame()
    f.loc[0, "Cost"] = float("nan")
    out = prim_aggregate(f, {"group_by": [], "value_col": "Cost", "agg": "sum"})
    assert float(out.iloc[0]["Cost"]) == 90.0


# ── typed-signal contract ────────────────────────────────────────────────────

def test_2a_is_a_filtertype_error_subclass():
    """The whole point of subclassing: every `except FilterTypeError:` site in the
    adapters catches AggregateTypeError too, so no adapter edits are needed."""
    assert issubclass(AggregateTypeError, FilterTypeError)


# ── adapter integration via _apply_one (shared by planner/react/code_agent) ──

@pytest.mark.parametrize("gb", [[], "Group"])
def test_2a_apply_one_reraises_aggregate_type_error(gb):
    """The shared step-executor `_apply_one` (used by planner/react/code_agent) must
    surface AggregateTypeError (subclass of FilterTypeError) so each adapter's
    existing FilterTypeError handler maps it to a clean refusal/observation — same
    path used by S26→S27 for filters. Covers BOTH grouped and global aggregates
    after S36 made the global path delegate to prim_aggregate."""
    from evidence.eval_run.tools_decl import _apply_one
    frames = {"register": _frame()}
    with pytest.raises(AggregateTypeError):
        _apply_one("aggregate",
                   {"input": "register", "group_by": gb,
                    "value_col": "Title", "agg": "mean"},
                   frames)


# ── integrity guard ─────────────────────────────────────────────────────────

def test_integrity_primitives_aggregate_rule_has_no_eval_content():
    """The rule is a general type guard, never authored from queries or gold."""
    src = (ROOT / "gdc" / "primitives.py").read_text()
    assert not re.search(r"\b(t[1-5]_\d{2}|oor_\d{2}|hv_\d{2})\b", src), \
        "query id in primitives.py"
    for val in ("4836800", "211846", "45491000"):
        assert val not in src, f"gold value {val} baked into primitives.py"
