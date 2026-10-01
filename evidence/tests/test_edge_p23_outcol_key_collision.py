"""P23 — an aggregate's out_col colliding with a group key. Confirmed to fail
with the fix reverted."""
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

from gdc.primitives import AggregateTypeError  # noqa: E402


def test_count_of_the_key_by_the_key_WORKS():
    """The case the diagnostic named — `count(status) by status` — was a FALSE
    finding: the D2 harness's own effect checker crashed, not the engine. The
    primitive handles it via the `{key}__group` stand-in, and since P57 the
    planner's dispatch restores the
    key's own name once the aggregate is under out_col, so the frame carries
    the columns the Goal declared. Locked here because seven recorded corpus
    goals have exactly this shape and P23's fix must not touch it."""
    goal = Goal.make(columns={"status": "str", "n": "float"},
                     grain={"grouped_by": ["status"]}, where=[],
                     derive=[{"name": "n", "expr": "count(status)"}])
    frame, p = run_goal_plan(goal, OrderSpec.make(), _frame(), base_schema=SCHEMA)
    assert [o.kind for o in p] == ["aggregate"]
    assert not frame.columns.duplicated().any()
    assert list(frame.columns) == ["status", "n"]
    assert dict(zip(frame["status"], frame["n"])) == {"approved": 1, "pending": 2}
    goal2 = Goal.make(columns={"status": "str", "name": "str", "n": "float"},
                      grain={"grouped_by": ["status", "name"]}, where=[],
                      derive=[{"name": "n", "expr": "count(name)"}])
    frame2, _ = run_goal_plan(goal2, OrderSpec.make(), _frame(), base_schema=SCHEMA)
    assert not frame2.columns.duplicated().any() and len(frame2) == 3


@pytest.mark.parametrize("by", [["status"], ["status", "name"]], ids=["single", "multi"])
def test_out_col_equal_to_a_key_is_unreachable_at_plan_time(by):
    """(a) The REAL defect: out_col naming a group key. Refused at plan time —
    there is no output that holds both the key and the aggregate in one column."""
    goal = Goal.make(columns={c: "str" for c in by}, grain={"grouped_by": by}, where=[],
                     derive=[{"name": by[0], "expr": "sum(cost)"}])
    assert isinstance(_plan(goal), Unreachable)


@pytest.mark.parametrize("by", [("status",), ("status", "name")], ids=["single", "multi"])
def test_post_rename_raises_typed_instead_of_duplicate_labels(by):
    """(b) The executor's own guard, reached by constructing the planner Op
    directly. Before P23 this returned a frame whose columns were
    ['status', 'status'] — no error, and to_dict() silently dropped one."""
    op = _make_op("aggregate", {"by": by, "col": "cost", "fn": "sum", "out_col": by[0]})
    d = op_to_primitive(op)
    raw = run_primitive(d.kind, _frame(), d.params)
    with pytest.raises(AggregateTypeError):
        d.post(raw)


def test_ordinary_rename_still_works():
    """No over-refusal: out_col distinct from the keys renames as before."""
    op = _make_op("aggregate", {"by": ("status",), "col": "cost", "fn": "sum", "out_col": "total"})
    d = op_to_primitive(op)
    out = d.post(run_primitive(d.kind, _frame(), d.params))
    assert list(out.columns) == ["status", "total"]
    assert dict(zip(out["status"], out["total"])) == {"approved": 5.0, "pending": 30.0}


# ── back-port shim ────────────────────────────────────────────────────────────
# gdc.integrate.op_to_primitive returns a (kind, params, post) triple; the
# follow-up branch's returns a Dispatch with the same three fields. Wrap the
# triple so the assertions above read unchanged.
from collections import namedtuple as _namedtuple            # noqa: E402
from gdc.integrate import op_to_primitive as _pr_op_to_primitive  # noqa: E402
_Dispatch = _namedtuple("_Dispatch", "kind params post")


def op_to_primitive(op):                                       # noqa: F811
    return _Dispatch(*_pr_op_to_primitive(op))
