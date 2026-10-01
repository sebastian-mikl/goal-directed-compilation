"""P58: `project` and the aggregate's
missing-input branches refuse typed instead of returning a frame that is not
what the State says — P26's pattern ("was: `continue` — the predicate was
dropped and every row came back"), applied to the two primitives that still
had it.

Was: `project` dropped a missing column from the projection silently, and an
empty or non-list request returned every column; `prim_aggregate` returned
`frame.copy()` — unaggregated — from three branches when a key or the value
column was absent, with a comment saying verify would refuse, which it would,
except that verify is not on the live path (P15a).
"""
from __future__ import annotations

import pandas as pd
import pytest

from gdc import Goal, OrderSpec
from gdc.integrate import aliased_view, run_goal_plan
from gdc.primitives import (AggregateTypeError, FilterTypeError, ProjectColumnError,
                            run_primitive, verify_primitive)
from gdc.search import Unreachable


def frame():
    return pd.DataFrame({"k": ["A", "A", "B"], "s": ["x", "y", "x"], "v": [1.0, 2.0, 3.0]})


# ── project ──────────────────────────────────────────────────────────────────

def test_a_missing_column_is_refused_not_dropped():
    with pytest.raises(ProjectColumnError, match="not in the frame"):
        run_primitive("project", frame(), {"columns": ["k", "ghost"]})


@pytest.mark.parametrize("columns", [[], "k", None, 7])
def test_an_empty_or_non_list_projection_is_refused_not_passed_through(columns):
    with pytest.raises(ProjectColumnError, match="non-empty list"):
        run_primitive("project", frame(), {"columns": columns})


def test_the_refusal_is_a_member_of_the_typed_family():
    with pytest.raises(FilterTypeError):
        run_primitive("project", frame(), {"columns": ["ghost"]})


def test_a_present_projection_is_exact_and_ordered():
    out = run_primitive("project", frame(), {"columns": ["v", "k"]})
    assert list(out.columns) == ["v", "k"] and len(out) == 3


def test_verify_project_refuses_the_same_requests():
    ok, why = verify_primitive("project", {"columns": ["k", "ghost"]}, frame(), frame()[["k"]])
    assert not ok and "not in the frame" in why
    ok, why = verify_primitive("project", {"columns": []}, frame(), frame())
    assert not ok


# ── aggregate: the three branches ────────────────────────────────────────────

@pytest.mark.parametrize("params", [
    {"group_by": [], "value_col": "ghost", "agg": "sum"},                 # global, value column absent
    {"group_by": [], "value_col": None, "agg": "count"},                  # global, no value column
    {"group_by": ["k", "ghost"], "value_col": "v", "agg": "sum"},         # multi-key, a key absent
    {"group_by": ["k", "s"], "value_col": "ghost", "agg": "sum"},         # multi-key, value column absent
    {"group_by": "ghost", "value_col": "v", "agg": "sum"},                # single key absent
    {"group_by": "k", "value_col": "ghost", "agg": "mean"},               # single key, value column absent
    {"group_by": "k", "value_col": None, "agg": "sum"},                   # single key, no value column
], ids=["global-vc", "global-none", "multi-key", "multi-vc", "single-key", "single-vc", "single-none"])
def test_a_missing_input_is_refused_not_returned_unaggregated(params):
    with pytest.raises(AggregateTypeError, match="not in the frame"):
        run_primitive("aggregate", frame(), params)


def test_present_inputs_aggregate_as_before():
    out = run_primitive("aggregate", frame(), {"group_by": "k", "value_col": "v", "agg": "sum"})
    assert out.values.tolist() == [["A", 3.0], ["B", 3.0]]
    out = run_primitive("aggregate", frame(), {"group_by": [], "value_col": "v", "agg": "count"})
    assert out.values.tolist() == [[3]]


# ── the planner path is unchanged: the State guarantees presence ─────────────

def test_planned_goals_are_unaffected():
    _, adf, schema = aliased_view(frame())
    g = Goal.make(columns={"k": "str", "v": "float"}, grain="row",
                  where=[{"col": "s", "op": "==", "val": "x"}], derive=[])
    f, plan = run_goal_plan(g, OrderSpec.make(), adf, base_schema=schema)
    assert not isinstance(plan, Unreachable) and list(f.columns) == ["k", "v"] and len(f) == 2
    g2 = Goal.make(columns={"k": "str", "t": "float"}, grain={"grouped_by": ["k"]}, where=[],
                   derive=[{"name": "t", "expr": "sum(v)"}])
    f2, _ = run_goal_plan(g2, OrderSpec.make(), adf, base_schema=schema)
    assert f2.values.tolist() == [["A", 3.0], ["B", 3.0]]


# ── the baseline adapters see a refusal, not a narrowed frame ────────────────

def test_the_declarative_tool_path_surfaces_the_refusal_typed():
    from evidence.eval_run.tools_decl import _apply_one
    frames = {"f0": frame()}
    with pytest.raises(FilterTypeError):
        _apply_one("select", {"input": "f0", "columns": ["k", "ghost"]}, frames)
    with pytest.raises(FilterTypeError):
        _apply_one("aggregate", {"input": "f0", "group_by": ["ghost"], "value_col": "v", "agg": "sum"}, frames)
