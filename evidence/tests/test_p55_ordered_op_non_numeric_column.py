"""P55: an ordered op on a column with no
numeric content is a typed refusal, as it already was for a non-numeric VALUE.

Was: `to_numeric(col, errors="coerce")` alone, so `date_col > 5` became all-NaN,
every comparison False, and a confident empty answer. The criterion is S36's,
factored into gdc.primitives.has_numeric_content and shared with aggregate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gdc import Goal, OrderSpec
from gdc.integrate import aliased_view, run_goal_plan
from gdc.primitives import (AggregateTypeError, FilterTypeError, has_numeric_content,
                            run_primitive)
from gdc.search import Unreachable


def frame():
    return pd.DataFrame({
        "d":  ["2024-01-01", "2024-02-01", "2024-03-01"],   # ISO dates: no numeric content
        "s":  ["ann", "bob", "cev"],
        "ns": ["1", "9", "4"],                                # numeric strings: numeric content
        "x":  [1.0, np.nan, 4.0],
        "i":  [10, 20, 30],
    })


def sel(col, op, val, f=None):
    return run_primitive("select", frame() if f is None else f,
                         {"predicates": [{"col": col, "op": op, "value": val}]})


@pytest.mark.parametrize("op", ["<", "<=", ">", ">="])
def test_ordered_op_on_a_column_without_numeric_content_is_refused(op):
    with pytest.raises(FilterTypeError, match="non-numeric column cannot be coerced"):
        sel("d", op, 5)
    with pytest.raises(FilterTypeError, match="non-numeric column cannot be coerced"):
        sel("s", op, 1)


def test_numeric_strings_still_compare_numerically():
    assert sel("ns", ">", 3)["ns"].tolist() == ["9", "4"]
    assert sel("ns", "<=", 4)["ns"].tolist() == ["1", "4"]


def test_numeric_columns_are_unchanged_and_nan_never_compares():
    assert sel("x", ">", 0)["x"].tolist() == [1.0, 4.0]
    assert sel("i", "<", 25)["i"].tolist() == [10, 20]


def test_a_non_numeric_value_is_still_refused_first():
    with pytest.raises(FilterTypeError, match="value is not numeric"):
        sel("d", ">", "2024-01-15")


def test_empty_and_all_null_columns_are_not_refused():
    """Nothing to approximate: an empty frame filters to an empty frame."""
    assert len(sel("d", ">", 5, frame().iloc[0:0])) == 0
    f = frame(); f["d"] = None
    assert len(sel("d", ">", 5, f)) == 0


def test_the_criterion_is_the_one_aggregate_uses():
    f = frame()
    assert not has_numeric_content(f["d"]) and has_numeric_content(f["ns"]) and has_numeric_content(f["x"])
    with pytest.raises(AggregateTypeError, match="non-numeric column cannot be coerced"):
        run_primitive("aggregate", f, {"group_by": [], "value_col": "d", "agg": "sum"})
    with pytest.raises(FilterTypeError, match="non-numeric column cannot be coerced"):
        sel("d", ">", 5, f)


def test_end_to_end_the_planner_plans_and_the_executor_refuses_typed():
    """The planner cannot see content (a str-typed column may hold numeric
    strings), so the Goal plans; the refusal is the executor's, typed, where it
    used to be a silent empty frame. P60 (plan-time mirrors) is untouched."""
    _, adf, schema = aliased_view(frame())
    g = Goal.make(columns={"s": "str"}, grain="row",
                  where=[{"col": "d", "op": ">", "val": 5}], derive=[])
    with pytest.raises(FilterTypeError):
        run_goal_plan(g, OrderSpec.make(), adf, base_schema=schema)
    g2 = Goal.make(columns={"s": "str"}, grain="row",
                   where=[{"col": "ns", "op": ">", "val": 3}], derive=[])
    f, plan = run_goal_plan(g2, OrderSpec.make(), adf, base_schema=schema)
    assert not isinstance(plan, Unreachable) and f["s"].tolist() == ["bob", "cev"]
