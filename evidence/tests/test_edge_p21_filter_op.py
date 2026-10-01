"""P21 — filter ops the executor does not implement. Raise at the primitive + a
`_pre` that mirrors it; NOT wiring verify_primitive in. Confirmed to fail with
the fix reverted."""
from __future__ import annotations

import pandas as pd
import pytest

from gdc.contract import Goal
from gdc.integrate import op_to_primitive, run_goal_plan
from gdc.operators import OPERATORS, _make_op
from gdc.order import OrderSpec
from gdc.primitives import FilterTypeError, run_primitive
from gdc.search import Unreachable, plan
from gdc.state import State

SCHEMA = {"item_id": "str", "status": "str", "name": "str", "cost": "float",
          "qty": "float"}


def _frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"item_id": "R-1", "status": "pending",  "name": "ann", "cost": 10.0, "qty": 3.0},
        {"item_id": "R-2", "status": "pending",  "name": "bob", "cost": 20.0, "qty": 2.0},
        {"item_id": "R-3", "status": "approved", "name": "cev", "cost": 5.0,  "qty": 1.0},
    ])


def _plan(goal):
    return plan(State.from_base(SCHEMA), goal)

from gdc.primitives import SUPPORTED_FILTER_OPS, FilterOpError  # noqa: E402


@pytest.mark.parametrize("op", ["startswith", "like", "is", "matches", "between"])
def test_unsupported_op_is_unreachable_at_plan_time(op):
    """(a) filter_pre refuses the op, so the Goal exhausts the operator universe
    instead of planning a filter the executor would silently no-op."""
    goal = Goal.make(columns={"item_id": "str"}, grain="row",
                     where=[{"col": "name", "op": op, "val": "a"}], derive=[])
    assert isinstance(_plan(goal), Unreachable)


@pytest.mark.parametrize("op", ["startswith", "like", "is"])
def test_prim_select_raises_typed_not_every_row(op):
    """(b) The primitive's own guard. Before P21 this returned all 3 rows."""
    with pytest.raises(FilterOpError):
        run_primitive("select", _frame(),
                      {"predicates": [{"col": "name", "op": op, "value": "a"}]})


def test_every_declared_op_actually_runs():
    """The lock between SUPPORTED_FILTER_OPS and prim_select's chain: every
    declared op must reach a branch, and the declared set must be exactly the
    canonical spellings the planner produces."""
    f = _frame()
    sample = {"==": "ann", "!=": "ann", "in": ["ann"], "contains": "a",
              "<": 15, "<=": 15, ">": 15, ">=": 15}
    assert set(sample) == set(SUPPORTED_FILTER_OPS)
    for op, val in sample.items():
        col = "cost" if op in ("<", "<=", ">", ">=") else "name"
        out = run_primitive("select", f, {"predicates": [{"col": col, "op": op, "value": val}]})
        assert len(out) < len(f) or op == "!=", op          # each really filtered
    from gdc.state import OP_CANON
    assert set(OP_CANON.values()) == set(SUPPORTED_FILTER_OPS)


def test_primitive_and_pre_agree_on_spelling():
    """Mirroring is about spelling too: the planner canonicalises `=`/`eq` to
    `==`, so the primitive must accept those rather than refuse what filter_pre
    admitted."""
    f = _frame()
    for spelling in ("=", "eq", "EQ", "=="):
        out = run_primitive("select", f, {"predicates": [{"col": "name", "op": spelling, "value": "ann"}]})
        assert out["name"].tolist() == ["ann"], spelling
    st = State.from_base(SCHEMA)
    assert OPERATORS["filter"].pre(st, {"col": "name", "op": "eq", "val": "ann"})
    assert not OPERATORS["filter"].pre(st, {"col": "name", "op": "like", "val": "a"})


def test_propagates_through_apply_one_as_a_typed_refusal():
    """Cross-system: _apply_one re-raises the FilterTypeError family and swallows
    everything else into a string. A bare Exception here would be swallowed."""
    from evidence.eval_run.tools_decl import _apply_one
    assert issubclass(FilterOpError, FilterTypeError)
    with pytest.raises(FilterTypeError):
        _apply_one("filter", {"input": "f0",
                              "predicates": [{"col": "name", "op": "like", "value": "a"}]},
                   {"f0": _frame()})


def test_supported_ops_still_work_end_to_end():
    """No over-refusal: a plain supported filter plans and executes as before."""
    goal = Goal.make(columns={"item_id": "str", "cost": "float"}, grain="row",
                     where=[{"col": "status", "op": "==", "val": "pending"}], derive=[])
    frame, p = run_goal_plan(goal, OrderSpec.make(), _frame(), base_schema=SCHEMA)
    assert [o.kind for o in p] == ["filter", "select"]
    assert frame["item_id"].tolist() == ["R-1", "R-2"]
