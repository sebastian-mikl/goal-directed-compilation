"""P22 — aggregate fns the executor does not implement. Confirmed to fail with
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

from gdc.primitives import AGG_FN_ALIASES, SUPPORTED_AGG_FNS, AggregateTypeError  # noqa: E402


@pytest.mark.parametrize("fn", ["garbage", "median", "stdev", "product"])
def test_unknown_fn_is_unreachable_at_plan_time(fn):
    """(a) aggregate_pre refuses the fn. `median` is included deliberately: it
    used to work by accident (pandas happens to have it) but is not declared
    and the LLM is never told it exists — undeclared means refused."""
    goal = Goal.make(columns={"status": "str", "t": "float"},
                     grain={"grouped_by": ["status"]}, where=[],
                     derive=[{"name": "t", "expr": f"{fn}(cost)"}])
    assert isinstance(_plan(goal), Unreachable)


@pytest.mark.parametrize("gb", ["status", ["status", "name"], []],
                         ids=["single-key", "multi-key", "global"])
def test_prim_aggregate_raises_typed_on_every_path(gb):
    """(b) All three code paths in prim_aggregate (single key, multi key,
    global) used to reach getattr(..., fn) and crash with AttributeError."""
    with pytest.raises(AggregateTypeError):
        run_primitive("aggregate", _frame(),
                      {"group_by": gb, "value_col": "cost", "agg": "garbage"})


def test_every_declared_fn_and_alias_actually_runs():
    """The lock: every declared fn runs on every path, every alias maps INTO the
    declared set, and every spelling the LLM is offered maps into it too."""
    f = _frame()
    for fn in SUPPORTED_AGG_FNS:
        vc = "name" if fn in ("count", "nunique") else "cost"
        for gb in ("status", ["status", "name"], []):
            out = run_primitive("aggregate", f, {"group_by": gb, "value_col": vc, "agg": fn})
            assert len(out) >= 1, (fn, gb)
    for alias, target in AGG_FN_ALIASES.items():
        assert target in SUPPORTED_AGG_FNS, alias
    from gdc.llm import ALLOWED_AGG_FNS
    for fn in ALLOWED_AGG_FNS:
        assert AGG_FN_ALIASES.get(fn, fn) in SUPPORTED_AGG_FNS, fn


def test_dispatch_and_pre_use_the_same_alias_table():
    from gdc.integrate import _AGG_FN_MAP
    assert _AGG_FN_MAP is AGG_FN_ALIASES
    st = State.from_base(SCHEMA)
    assert OPERATORS["aggregate"].pre(st, {"by": ["status"], "col": "cost", "fn": "avg", "out_col": "m"})
    assert OPERATORS["aggregate"].pre(st, {"by": ["status"], "col": "name", "fn": "count_distinct", "out_col": "n"})
    assert not OPERATORS["aggregate"].pre(st, {"by": ["status"], "col": "cost", "fn": "garbage", "out_col": "t"})


def test_propagates_through_apply_one_as_a_typed_refusal():
    from evidence.eval_run.tools_decl import _apply_one
    with pytest.raises(FilterTypeError):
        _apply_one("aggregate", {"input": "f0", "group_by": ["status"],
                                 "value_col": "cost", "agg": "garbage"}, {"f0": _frame()})


def test_supported_fns_still_work_end_to_end():
    goal = Goal.make(columns={"status": "str", "m": "float"},
                     grain={"grouped_by": ["status"]}, where=[],
                     derive=[{"name": "m", "expr": "avg(cost)"}])
    frame, p = run_goal_plan(goal, OrderSpec.make(), _frame(), base_schema=SCHEMA)
    assert [o.kind for o in p] == ["aggregate"]
    assert dict(zip(frame["status"], frame["m"])) == {"approved": 5.0, "pending": 15.0}
