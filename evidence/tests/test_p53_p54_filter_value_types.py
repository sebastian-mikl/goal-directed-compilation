"""P53/P54: the filter's VALUE side.

One class, found three times this session: P33 (`seq == "max(seq)"` answered 0
rows), P46 (`amount == 400` on a float column answered 0 rows) and the
operator-consistency check's OC-1/OC-2 (`contains` was a regex; the equality,
membership and contains branches compared string forms of any value).

Now declared once in gdc.primitives — SCALAR_OPS / LIST_OPS / value_shape_ok /
values_numeric_ok — and read by prim_select on the frame's dtype and by
filter_pre on the State's declared type. `contains` is a literal,
case-insensitive substring match.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gdc import Goal, OrderSpec
from gdc.integrate import aliased_view, run_goal_plan
from gdc.operators import filter_pre
from gdc.primitives import (FilterTypeError, LIST_OPS, SCALAR_OPS, run_primitive,
                            value_shape_ok, values_numeric_ok)
from gdc.search import Unreachable
from gdc.state import State


def frame():
    return pd.DataFrame({
        "k":  ["A", "A", "B", "C"],
        "s":  ["ann", "bob", "a.b", "C++ (UK)"],
        "x":  [1.0, 4.0, np.nan, 4.0],
        "i":  [10, 20, 30, 40],
        "ns": ["1", "4", "9", "4"],
        "b":  [True, False, True, False],
    })


def sel(col, op, val, f=None):
    return run_primitive("select", frame() if f is None else f,
                         {"predicates": [{"col": col, "op": op, "value": val}]})


# ── A. contains is a literal substring, case-insensitive ─────────────────────

def test_contains_metacharacters_are_literal():
    assert sel("s", "contains", ".")["s"].tolist() == ["a.b"]
    assert sel("s", "contains", "a+")["s"].tolist() == []
    assert sel("s", "contains", "C++")["s"].tolist() == ["C++ (UK)"]
    assert sel("s", "contains", "(UK)")["s"].tolist() == ["C++ (UK)"]
    assert sel("s", "contains", "an|ce")["s"].tolist() == []


def test_contains_invalid_pattern_does_not_crash():
    """Was: ArrowInvalid, outside the typed family — the adapters recorded `crashed`."""
    assert sel("s", "contains", "(")["s"].tolist() == ["C++ (UK)"]
    assert sel("s", "contains", "[")["s"].tolist() == []


def test_contains_stays_case_insensitive():
    assert sel("s", "contains", "ANN")["s"].tolist() == ["ann"]
    assert sel("s", "contains", "c++")["s"].tolist() == ["C++ (UK)"]


# ── B. value shape: scalar ops take one value, `in` takes a list ─────────────

@pytest.mark.parametrize("op", sorted(SCALAR_OPS))
def test_non_scalar_under_a_scalar_op_is_refused(op):
    with pytest.raises(FilterTypeError, match="takes one scalar value"):
        sel("k", op, ["A", "B"])
    assert not filter_pre(State.from_base({"k": "str"}), {"col": "k", "op": op, "val": ["A", "B"]})


@pytest.mark.parametrize("op", sorted(LIST_OPS))
def test_scalar_under_a_list_op_is_refused(op):
    with pytest.raises(FilterTypeError, match="takes a list of values"):
        sel("k", op, "A")
    assert not filter_pre(State.from_base({"k": "str"}), {"col": "k", "op": op, "val": "A"})


# ── B2. a null value under == / != is a null test ───────────────────────────

def nulls():
    return pd.DataFrame({"x": [1.0, np.nan, 3.0, np.nan], "t": ["a", None, "b", "None"],
                         "n": [1, 2, 3, 4]})


@pytest.mark.parametrize("col", ["x", "t", "n"])
def test_eq_none_keeps_the_null_rows_and_ne_none_the_rest(col):
    f = nulls()
    eq, ne = sel(col, "==", None, f), sel(col, "!=", None, f)
    assert eq.index.tolist() == f.index[f[col].isna()].tolist()
    assert ne.index.tolist() == f.index[f[col].notna()].tolist()
    assert value_shape_ok("==", None) and value_shape_ok("!=", None)


def test_the_string_none_is_an_ordinary_value_not_a_null_test():
    f = nulls()
    assert sel("t", "==", "None", f)["n"].tolist() == [4]
    assert sel("t", "!=", "None", f)["n"].tolist() == [1, 2, 3]   # P59: a null row is kept under !=


def test_ne_none_on_a_column_without_nulls_keeps_every_row():
    """The recorded `!= None` plans (planner and react) filter a column with no
    nulls: the null test keeps every row, as the earlier string comparison did,
    instead of the P54 back-port's refusal."""
    f = frame()
    assert len(sel("k", "!=", None, f)) == len(f) and len(sel("i", "!=", None, f)) == len(f)
    assert sel("x", "!=", None, f)["k"].tolist() == ["A", "A", "C"]   # was: the NaN row kept


def test_planner_admits_a_null_test_on_any_declared_type():
    for t in ("float", "str", "unknown"):
        st = State.from_base({"x": t})
        assert filter_pre(st, {"col": "x", "op": "==", "val": None})
        assert filter_pre(st, {"col": "x", "op": "!=", "val": None})


def test_none_is_refused_under_every_other_op():
    for op in ("in", "contains", "<", ">"):
        assert not value_shape_ok(op, None)
        with pytest.raises(FilterTypeError):
            sel("x", op, None)


# ── C. a numeric column compares numerically, and only with numbers ──────────

def test_p33_spelling_is_refused_typed_by_the_executor():
    with pytest.raises(FilterTypeError, match="value is not numeric"):
        sel("x", "==", "max(x)")
    with pytest.raises(FilterTypeError, match="value is not numeric"):
        sel("x", "!=", "max(x)")
    with pytest.raises(FilterTypeError, match="value is not numeric"):
        sel("x", "in", ["4", "max(x)"])


def test_p33_spelling_is_refused_at_plan_time_by_the_planner():
    st = State.from_base({"seq": "float", "item_id": "str"})
    assert not filter_pre(st, {"col": "seq", "op": "==", "val": "max(seq)"})
    assert not filter_pre(st, {"col": "seq", "op": "in", "val": ["max(seq)"]})
    # and end to end: the Goal is Unreachable rather than a confident empty answer
    log = pd.DataFrame({"seq": [1, 2, 3], "item_id": ["a", "b", "a"]})
    _, adf, schema = aliased_view(log)
    g = Goal.make(columns={"item_id": "str"}, grain="row",
                  where=[{"col": "seq", "op": "==", "val": "max(seq)"}], derive=[])
    _, plan = run_goal_plan(g, OrderSpec.make(), adf, base_schema=schema)
    assert isinstance(plan, Unreachable)


def test_p46_numeric_equality_matches_across_int_and_float_forms():
    """Was: "4.0" != "4" — `x == 4` on a float column returned no rows."""
    assert sel("x", "==", 4)["k"].tolist() == ["A", "C"]
    assert sel("x", "==", "4")["k"].tolist() == ["A", "C"]
    assert sel("x", "==", 4.0)["k"].tolist() == ["A", "C"]
    assert sel("i", "==", 20.0)["k"].tolist() == ["A"]
    assert sel("x", "in", [4, 1])["k"].tolist() == ["A", "A", "C"]
    assert sel("x", "in", ["4"])["k"].tolist() == ["A", "C"]


def test_p46_end_to_end_through_the_planner():
    reg = pd.DataFrame({"item_id": ["R-1", "R-2", "R-3"], "amount": [400.0, 120.0, 400.0]})
    _, adf, schema = aliased_view(reg)
    g = Goal.make(columns={"item_id": "str"}, grain="row",
                  where=[{"col": "amount", "op": "==", "val": 400}], derive=[])
    f, plan = run_goal_plan(g, OrderSpec.make(), adf, base_schema=schema)
    assert not isinstance(plan, Unreachable)
    assert f["item_id"].tolist() == ["R-1", "R-3"]


def test_nan_is_never_equal_and_stays_under_not_equal():
    """The NaN policy is P59's and is not changed here: NaN == v is False, NaN != v keeps the row."""
    assert sel("x", "==", 4)["x"].isna().sum() == 0
    assert sel("x", "!=", 4)["x"].isna().sum() == 1


# ── D. non-numeric columns keep the string comparison; bool columns too ──────

def test_string_column_compares_string_forms():
    assert sel("ns", "==", 4)["ns"].tolist() == ["4", "4"]
    assert sel("ns", "in", [4, 9])["ns"].tolist() == ["4", "9", "4"]
    assert sel("k", "==", 1)["k"].tolist() == []          # no row, no refusal: a str column takes any scalar


def test_bool_column_is_not_treated_as_numeric():
    assert sel("b", "==", "True")["k"].tolist() == ["A", "B"]
    assert sel("b", "==", True)["k"].tolist() == ["A", "B"]


def test_derived_columns_are_left_to_the_executor():
    """A derived column is typed "unknown" in the State; the planner cannot judge the value."""
    st = State.from_base({"x": "float"})
    st = State(columns=st.columns + (("y", "unknown"),), grouped_by=None, where=(), derive=(("y", "x + x"),))
    assert filter_pre(st, {"col": "y", "op": "==", "val": "abc"})


# ── E. the two sides read one declaration ────────────────────────────────────

def test_planner_verdict_implies_executor_verdict_on_the_value_axis():
    """For every (op, value) the planner admits on a float column, the executor
    does not refuse on the value's shape or type; for every one it refuses, the
    executor refuses too. Ordered ops with a non-numeric value are P60's late
    refusal and are excluded from the implication."""
    f = frame()
    st = State.from_base({"x": "float"})
    values = [1, 4.0, "4", "ann", "max(x)", ["4"], ["a"], "A", None, True]
    for op in ("==", "!=", "in", "contains"):
        for val in values:
            pre = filter_pre(st, {"col": "x", "op": op, "val": val})
            try:
                sel("x", op, val, f)
                executed = True
            except FilterTypeError:
                executed = False
            if op == "contains" and pre:
                continue                      # contains is a string op; the executor takes any scalar
            assert pre == executed, (op, val, pre, executed)
