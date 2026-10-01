"""P26 — a filter predicate naming a column the frame does not have. Same class
as P21, one field over: prim_select used to `continue` past the predicate and
return every row. Raise at the primitive; filter_pre already mirrors it at plan
time. NOT wiring verify_primitive in. Confirmed to fail with the fix reverted."""
from __future__ import annotations

import pandas as pd
import pytest

from gdc.contract import Goal
from gdc.integrate import run_goal_plan
from gdc.order import OrderSpec
from gdc.primitives import FilterColumnError, FilterTypeError, run_primitive
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


def test_absent_column_is_unreachable_at_plan_time():
    """(a) filter_pre checks the column, so from a Goal the outcome is
    Unreachable — this was already true and is locked here as the mirror."""
    goal = Goal.make(columns={"item_id": "str"}, grain="row",
                     where=[{"col": "region", "op": "==", "val": "eu"}], derive=[])
    assert isinstance(_plan(goal), Unreachable)


@pytest.mark.parametrize("op", ["==", "!=", "in", "contains", "<"])
def test_prim_select_raises_typed_not_every_row(op):
    """(b) The primitive's own guard. Before P26 this returned all 3 rows,
    whatever the op — the predicate was dropped before the op was read."""
    val = ["x"] if op == "in" else (1 if op == "<" else "x")
    with pytest.raises(FilterColumnError) as ei:
        run_primitive("select", _frame(),
                      {"predicates": [{"col": "region", "op": op, "value": val}]})
    assert "region" in str(ei.value) and "every row" in str(ei.value)


def test_predicate_with_no_column_refuses():
    """A predicate that names no column at all is malformed, not ignorable."""
    with pytest.raises(FilterColumnError):
        run_primitive("select", _frame(),
                      {"predicates": [{"op": "==", "value": "x"}]})


def test_absent_column_refuses_even_when_another_predicate_is_fine():
    """A conjunction is refused as a whole: one unsatisfiable conjunct must not
    be dropped while the others apply, which would answer a different
    question."""
    with pytest.raises(FilterColumnError):
        run_primitive("select", _frame(),
                      {"predicates": [{"col": "status", "op": "==", "value": "pending"},
                                      {"col": "region", "op": "==", "value": "eu"}]})


def test_propagates_through_apply_one_as_a_typed_refusal():
    """Cross-system: _apply_one re-raises the FilterTypeError family and swallows
    everything else into a string. A bare Exception here would be swallowed."""
    from evidence.eval_run.tools_decl import _apply_one
    assert issubclass(FilterColumnError, FilterTypeError)
    with pytest.raises(FilterTypeError):
        _apply_one("filter", {"input": "f0",
                              "predicates": [{"col": "region", "op": "==", "value": "eu"}]},
                   {"f0": _frame()})


def test_present_columns_still_filter_end_to_end():
    """No over-refusal: a filter on a present column plans and executes as
    before, and the col_map baseline path is untouched."""
    goal = Goal.make(columns={"item_id": "str", "cost": "float"}, grain="row",
                     where=[{"col": "status", "op": "==", "val": "pending"}], derive=[])
    frame, p = run_goal_plan(goal, OrderSpec.make(), _frame(), base_schema=SCHEMA)
    assert [o.kind for o in p] == ["filter", "select"]
    assert frame["item_id"].tolist() == ["R-1", "R-2"]
    out = run_primitive("select", _frame(),
                        {"predicates": [{"col": "name", "op": "in", "value": ["ann", "cev"]}]})
    assert out["item_id"].tolist() == ["R-1", "R-3"]
