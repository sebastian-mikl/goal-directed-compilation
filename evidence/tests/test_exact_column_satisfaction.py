"""S42 / Spider Fix 3 — satisfies() exact-column-match (deterministic; no API).

Under the original subset semantics (goal.columns ⊆ state.columns) a
filter-only plan already "satisfied" any narrower goal, so the terminal
projection never had to fire and every unrequested register column leaked
into the output. Spider world_04 surfaced it live (1-column goal answered
with the full 15-column row); the Phase 0 scan found the same leak,
comparator-masked, across every domain. This suite pins the new semantics:

  - row grain: state.columns == goal.columns exactly;
  - grouped grain: state.columns == goal.columns ∪ grouped_by (grain keys are
    definitionally output — naming a column in grouped_by requests it);
  - ordering is applied BEFORE the terminal projection when the projection
    drops a derive-only sort key (validate_order legitimizes those keys, so
    they must be sortable-by without leaking into the output);
  - an empty effective output set is honestly Unreachable, never a
    whole-register dump.
"""
from __future__ import annotations

import pandas as pd
import pytest

from gdc.state import State, Goal, satisfies, effective_output_columns
from gdc.search import plan, Unreachable, apply_plan
from gdc.operators import enumerate_ops, _make_op
from gdc.integrate import (execute_plan_ordered, run_goal_plan,
                               OrderApplicationError, aliased_view)
from gdc.order import OrderSpec


BASE = {"name": "str", "region": "str", "cost": "float", "emv": "float"}


def _start():
    return State.from_base(BASE)


def _goal(columns, grain="row", where=(), derive=()):
    return Goal.make(columns=columns, grain=grain,
                     where=list(where), derive=list(derive))


def _frame():
    return pd.DataFrame({
        "name":   ["a", "b", "c", "d"],
        "region": ["N", "N", "S", "S"],
        "cost":   [10.0, 40.0, 20.0, 30.0],
        "emv":    [1.0, 2.0, 3.0, 4.0],
    })


# ── satisfies(): exact column equality ───────────────────────────────────────

def test_row_grain_extra_columns_no_longer_satisfy():
    state = _start()                                     # all 4 base columns
    goal = _goal(columns=["name"])
    assert not satisfies(state, goal), "subset semantics regression: wide state satisfied narrow goal"


def test_row_grain_exact_columns_satisfy():
    state = State(columns=(("name", "str"),), grouped_by=None, where=(), derive=())
    assert satisfies(state, _goal(columns=["name"]))


def test_row_grain_missing_column_still_fails():
    state = State(columns=(("name", "str"),), grouped_by=None, where=(), derive=())
    assert not satisfies(state, _goal(columns=["name", "cost"]))


def test_grouped_grain_keys_count_as_requested():
    """Metric-only goal.columns: the grain key is implicitly requested, so the
    canonical aggregate output (keys + metric) satisfies exactly."""
    goal = _goal(columns=["avg_cost"], grain={"grouped_by": ["region"]},
                 derive=[{"name": "avg_cost", "expr": "avg(cost)"}])
    assert effective_output_columns(goal) == {"region", "avg_cost"}
    agg_state = State(columns=(("region", "unknown"), ("avg_cost", "unknown")),
                      grouped_by=("region",), where=(),
                      derive=(("avg_cost", "avg(cost)"),))
    assert satisfies(agg_state, goal)


def test_grouped_grain_intermediate_column_blocks_satisfaction():
    """A post-aggregate compute intermediate present in the state must force a
    narrowing select — it may not leak into the output."""
    goal = _goal(columns=["region", "final"], grain={"grouped_by": ["region"]},
                 derive=[{"name": "pre", "expr": "avg(cost)"},
                         {"name": "final", "expr": "pre * 2"}])
    wide = State(columns=(("region", "unknown"), ("pre", "unknown"), ("final", "unknown")),
                 grouped_by=("region",), where=(),
                 derive=(("pre", "avg(cost)"), ("final", "pre * 2")))
    assert not satisfies(wide, goal)
    narrowed = State(columns=(("region", "unknown"), ("final", "unknown")),
                     grouped_by=("region",), where=(),
                     derive=(("pre", "avg(cost)"), ("final", "pre * 2")))
    assert satisfies(narrowed, goal)


# ── plan(): the terminal select now actually fires ───────────────────────────

def test_filter_only_goal_requires_terminal_select():
    goal = _goal(columns=["name"], where=[{"col": "region", "op": "==", "val": "N"}])
    p = plan(_start(), goal)
    assert not isinstance(p, Unreachable)
    assert [o.kind for o in p] == ["filter", "select"]
    terminal = apply_plan(_start(), p)
    assert terminal.column_names == {"name"}
    assert satisfies(terminal, goal)


def test_world_04_shape_narrow_goal_wide_register():
    """world_04: 1-column goal over a wide register — the confirmed live leak."""
    goal = _goal(columns=["region"], where=[{"col": "name", "op": "==", "val": "a"}])
    p = plan(_start(), goal)
    assert not isinstance(p, Unreachable)
    terminal = apply_plan(_start(), p)
    assert terminal.column_names == {"region"}, "leak: unrequested columns in terminal state"


def test_grouped_goal_aggregate_alone_satisfies_when_exact():
    goal = _goal(columns=["region", "avg_cost"], grain={"grouped_by": ["region"]},
                 derive=[{"name": "avg_cost", "expr": "avg(cost)"}])
    p = plan(_start(), goal)
    assert not isinstance(p, Unreachable)
    assert [o.kind for o in p] == ["aggregate"], "no select needed: aggregate output is already exact"


def test_empty_effective_output_is_unreachable():
    """The Phase 0 scan found live emissions with columns=[] (whole-register
    dumps under subset semantics). They must now refuse, not dump."""
    goal = _goal(columns=[])
    res = plan(_start(), goal)
    assert isinstance(res, Unreachable)


def test_enumerate_ops_select_covers_grouped_keys():
    goal = _goal(columns=["avg_cost"], grain={"grouped_by": ["region"]},
                 derive=[{"name": "avg_cost", "expr": "avg(cost)"}])
    selects = [o for o in enumerate_ops(goal) if o.kind == "select"]
    assert len(selects) == 1
    assert sorted(selects[0].params["cols"]) == ["avg_cost", "region"]


# ── ordering vs terminal projection ──────────────────────────────────────────

def test_order_by_declared_output_column_unchanged():
    goal = _goal(columns=["name", "cost"])
    order = OrderSpec.make(order_by=[{"col": "cost", "dir": "desc"}], limit=2)
    p = plan(_start(), goal)
    out = execute_plan_ordered(p, _frame(), order)
    assert list(out.columns) == sorted(["name", "cost"]) or set(out.columns) == {"name", "cost"}
    assert list(out["name"]) == ["b", "d"]               # top-2 by cost desc


def test_order_by_derive_only_key_sorts_then_projects():
    """columns=[name], derive gap=cost-emv, order by gap desc, limit 2:
    the sort key is projected OUT of the output but must still order it."""
    goal = _goal(columns=["name"],
                 derive=[{"name": "gap", "expr": "cost - emv"}])
    order = OrderSpec.make(order_by=[{"col": "gap", "dir": "desc"}], limit=2)
    p = plan(_start(), goal)
    assert not isinstance(p, Unreachable)
    out = execute_plan_ordered(p, _frame(), order)
    assert set(out.columns) == {"name"}, "sort key leaked into output"
    # gaps: a=9, b=38, c=17, d=26 -> desc top-2 = b, d
    assert list(out["name"]) == ["b", "d"]


def test_grouped_metric_only_order_key():
    """'Which region has the highest average cost?' — columns=[region] only,
    metric consumed by order+limit. Output = keys only, ordered by the metric."""
    goal = _goal(columns=["region"], grain={"grouped_by": ["region"]},
                 derive=[{"name": "avg_cost", "expr": "avg(cost)"}])
    order = OrderSpec.make(order_by=[{"col": "avg_cost", "dir": "desc"}], limit=1)
    p = plan(_start(), goal)
    assert not isinstance(p, Unreachable)
    frame = pd.DataFrame({"name": ["a", "b", "c", "d"],
                          "region": ["N", "N", "S", "S"],
                          "cost": [10.0, 20.0, 40.0, 50.0],   # N avg 15, S avg 45
                          "emv": [1.0, 2.0, 3.0, 4.0]})
    out = execute_plan_ordered(p, frame, order)
    assert set(out.columns) == {"region"}
    assert list(out["region"]) == ["S"]


def test_order_application_error_is_typed_not_a_crash():
    """A plan whose shape offers no sound ordering point refuses with the typed
    error (constructed synthetically — no satisfying A* plan produces this)."""
    p = [_make_op("compute", {"new_col": "gap", "expr": "cost - emv"}),
         _make_op("select", {"cols": ["name"]}),
         _make_op("filter", {"col": "name", "op": "!=", "val": "zz"})]
    order = OrderSpec.make(order_by=[{"col": "gap", "dir": "desc"}])
    with pytest.raises(OrderApplicationError):
        execute_plan_ordered(p, _frame(), order)


# ── end-to-end through run_goal_plan (the adapter's own path) ────────────────

def test_run_goal_plan_world04_end_to_end():
    reg = pd.DataFrame({"Name": ["Anguilla", "Albania"],
                        "Continent": ["North America", "Europe"],
                        "Population": [8000, 3000000]})
    amap, adf, schema = aliased_view(reg)
    goal = _goal(columns=["continent"],
                 where=[{"col": "name", "op": "==", "val": "Anguilla"}])
    frame, p = run_goal_plan(goal, OrderSpec.make(), adf, base_schema=schema)
    assert frame is not None
    assert list(frame.columns) == ["continent"]
    assert list(frame["continent"]) == ["North America"]
