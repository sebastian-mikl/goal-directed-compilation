"""P56: compute over a column with no
numeric content is a typed refusal, through the criterion aggregate already
applies (S36 fix 2a, gdc.primitives.has_numeric_content).

Was: both operands `to_numeric(errors="coerce")`, so `cost + region` over a text
column produced an all-NaN column with nothing raised, while `sum(region)`
refused typed one operator over.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gdc import Goal, OrderSpec
from gdc.integrate import aliased_view, run_goal_plan
from gdc.primitives import (AggregateTypeError, ComputeExprError, FilterTypeError,
                            run_primitive, verify_primitive)
from gdc.search import Unreachable


def frame():
    return pd.DataFrame({
        "x":  [1.0, np.nan, 3.0],
        "i":  [1, 2, 3],
        "s":  ["ann", "bob", "cev"],
        "ns": ["1", "2", "3"],
        "d":  ["2024-01-01", "2024-01-02", "2024-01-03"],
    })


def comp(expr, f=None, new_col="y"):
    return run_primitive("compute", frame() if f is None else f, {"new_col": new_col, "expr": expr})


@pytest.mark.parametrize("expr", ["x + s", "s + s", "d - x", "s * i", "x / d"])
def test_an_operand_without_numeric_content_is_refused(expr):
    with pytest.raises(ComputeExprError, match="non-numeric column cannot be coerced"):
        comp(expr)


def test_the_refusal_is_a_member_of_the_typed_family():
    with pytest.raises(FilterTypeError):
        comp("x + s")


def test_numeric_strings_and_numeric_columns_compute():
    assert comp("ns * i")["y"].tolist() == [1, 4, 9]
    out = comp("x + i")["y"].tolist()
    assert out[0] == 2.0 and np.isnan(out[1]) and out[2] == 6.0


def test_the_overwrite_form_is_refused_the_same_way():
    with pytest.raises(ComputeExprError):
        comp("x + s", new_col="x")


def test_the_criterion_is_the_one_aggregate_uses():
    f = frame()
    with pytest.raises(AggregateTypeError, match="non-numeric column cannot be coerced"):
        run_primitive("aggregate", f, {"group_by": [], "value_col": "s", "agg": "sum"})
    with pytest.raises(ComputeExprError, match="non-numeric column cannot be coerced"):
        comp("s + s", f)


def test_verify_refuses_rather_than_crashing_on_the_same_operands():
    f = frame()
    ok, why = verify_primitive("compute", {"new_col": "y", "expr": "x + s"}, f, f.assign(y=[np.nan] * 3))
    assert not ok and "non-numeric column" in why


def test_empty_and_all_null_operands_are_not_refused():
    assert len(comp("x + s", frame().iloc[0:0])) == 0
    f = frame(); f["s"] = None
    assert comp("x + s", f)["y"].isna().all()


def test_end_to_end_the_planner_plans_and_the_executor_refuses_typed():
    """The planner cannot see content (a str-typed column may hold numeric
    strings), so the Goal plans; the refusal is the executor's. P60 untouched."""
    _, adf, schema = aliased_view(frame())
    g = Goal.make(columns={"s": "str", "y": "float"}, grain="row", where=[],
                  derive=[{"name": "y", "expr": "x + s"}])
    with pytest.raises(ComputeExprError):
        run_goal_plan(g, OrderSpec.make(), adf, base_schema=schema)
    g2 = Goal.make(columns={"s": "str", "y": "float"}, grain="row", where=[],
                   derive=[{"name": "y", "expr": "ns * i"}])
    f, plan = run_goal_plan(g2, OrderSpec.make(), adf, base_schema=schema)
    assert not isinstance(plan, Unreachable) and f["y"].tolist() == [1, 4, 9]
