"""P24 — prim_join fail-open.
Confirmed to fail with the fix reverted."""
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

from gdc.primitives import JoinInputError  # noqa: E402


def _right(tmp_path, cols):
    r = pd.DataFrame(cols); pth = tmp_path / "right.pkl"; r.to_pickle(pth); return str(pth)


def test_no_right_frame_raises():
    with pytest.raises(JoinInputError):
        run_primitive("join", _frame(), {"on": "item_id"})


def test_unreadable_right_frame_raises_not_swallowed(tmp_path):
    bad = tmp_path / "nope.pkl"; bad.write_text("not a pickle")
    with pytest.raises(JoinInputError) as ei:
        run_primitive("join", _frame(), {"on": "item_id", "_compose_source_0": str(bad)})
    assert ei.value.__cause__ is not None          # the real error is chained, not lost


def test_no_keys_raises(tmp_path):
    rp = _right(tmp_path, {"item_id": ["R-1"], "extra": [1]})
    with pytest.raises(JoinInputError):
        run_primitive("join", _frame(), {"_compose_source_0": rp})


def test_missing_key_raises_instead_of_joining_on_a_subset(tmp_path):
    """The worst of the three: before P24 a join on ['item_id', 'region'] with
    'region' absent from the right silently became a join on ['item_id']."""
    rp = _right(tmp_path, {"item_id": ["R-1", "R-2"], "extra": [1, 2]})
    with pytest.raises(JoinInputError) as ei:
        run_primitive("join", _frame(), {"on": ["item_id", "region"], "_compose_source_0": rp})
    assert "region" in str(ei.value)


def test_valid_join_still_works(tmp_path):
    rp = _right(tmp_path, {"item_id": ["R-1", "R-3"], "extra": [1, 3]})
    out = run_primitive("join", _frame(), {"on": "item_id", "how": "inner", "_compose_source_0": rp})
    assert out["item_id"].tolist() == ["R-1", "R-3"] and out["extra"].tolist() == [1, 3]


def test_is_typed_family_member():
    assert issubclass(JoinInputError, FilterTypeError)
