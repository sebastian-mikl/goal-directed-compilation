"""P15g — verify_aggregate must not pass on its own failure. NOT wiring verify
into the live path; making it honest for the callers that do use it. Confirmed
to fail with the fix reverted."""
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

from gdc.primitives import verify_primitive  # noqa: E402


def test_verifier_refuses_when_its_recompute_crashes():
    """The verifier's reference groupby raises (an fn pandas has no method for);
    before P15g the handler returned (True, '... schema ok')."""
    src = _frame()
    out = pd.DataFrame({"status": ["approved", "pending"], "cost": [5.0, 30.0]})
    ok, why = verify_primitive("aggregate", {"group_by": "status", "value_col": "cost",
                                             "agg": "garbage"}, src, out)
    assert ok is False
    assert "cannot verify" in why


def test_correct_aggregate_still_verifies():
    src = _frame()
    out = run_primitive("aggregate", src, {"group_by": "status", "value_col": "cost", "agg": "sum"})
    ok, why = verify_primitive("aggregate", {"group_by": "status", "value_col": "cost",
                                             "agg": "sum"}, src, out)
    assert ok is True, why
