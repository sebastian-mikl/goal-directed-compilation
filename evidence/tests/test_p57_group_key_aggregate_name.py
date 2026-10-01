"""P57: aggregating a group key returns the
key under its own name.

`count(k) by k` — how many rows per key — made prim_aggregate keep the key
under `{key}__group` so the aggregate could sit under value_col without a
duplicate label. The dispatch post then renamed value_col to out_col and
stopped: the frame said `k__group` while the State, the Goal and every
downstream consumer said `k`. An OrderSpec on the key was refused; a later
projection would have dropped it. The post now restores the key's name once
the aggregate is under out_col (which is never a key, P23). The primitive is
unchanged — the baseline adapters call it directly and the eval comparator
strips the suffix for them.
"""
from __future__ import annotations

import pandas as pd
import pytest

from gdc import Goal, OrderSpec
from gdc.integrate import OrderApplicationError, aliased_view, op_to_primitive, run_goal_plan
from gdc.operators import _make_op
from gdc.primitives import run_primitive
from gdc.search import Unreachable


def register():
    return pd.DataFrame({"item_id": ["A", "A", "B", "C", "C", "C"],
                         "decision": ["r", "a", "r", "r", "a", "r"],
                         "amount": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]})


def run(goal, order=None):
    _, adf, schema = aliased_view(register())
    return run_goal_plan(goal, order or OrderSpec.make(), adf, base_schema=schema)


def test_count_of_the_group_key_returns_the_key_under_its_own_name():
    g = Goal.make(columns={"item_id": "str", "n": "int"}, grain={"grouped_by": ["item_id"]},
                  where=[], derive=[{"name": "n", "expr": "count(item_id)"}])
    f, plan = run(g)
    assert [o.kind for o in plan] == ["aggregate"]
    assert list(f.columns) == ["item_id", "n"]
    assert dict(zip(f["item_id"], f["n"])) == {"A": 2, "B": 1, "C": 3}


def test_two_keys_with_the_value_column_among_them():
    g = Goal.make(columns={"item_id": "str", "decision": "str", "n": "int"},
                  grain={"grouped_by": ["item_id", "decision"]}, where=[],
                  derive=[{"name": "n", "expr": "count(decision)"}])
    f, plan = run(g)
    assert list(f.columns) == ["item_id", "decision", "n"]
    assert len(f) == 5


def test_nunique_of_the_key():
    g = Goal.make(columns={"decision": "str", "k": "int"}, grain={"grouped_by": ["decision"]},
                  where=[], derive=[{"name": "k", "expr": "count_distinct(decision)"}])
    f, _ = run(g)
    assert list(f.columns) == ["decision", "k"] and f["k"].tolist() == [1, 1]


def test_an_order_on_the_key_now_applies():
    """Was: OrderApplicationError — the key was not in the plan's output under its name."""
    g = Goal.make(columns={"item_id": "str", "n": "int"}, grain={"grouped_by": ["item_id"]},
                  where=[], derive=[{"name": "n", "expr": "count(item_id)"}])
    f, plan = run(g, OrderSpec.make([("item_id", "desc")]))
    assert not isinstance(plan, Unreachable)
    assert f["item_id"].tolist() == ["C", "B", "A"]


def test_the_frame_carries_exactly_the_states_columns():
    from gdc.operators import OPERATORS
    from gdc.state import State
    st = State.from_base({"item_id": "str", "decision": "str", "amount": "float"})
    params = {"fn": "count", "col": "item_id", "by": ["item_id"], "out_col": "n"}
    claimed = [c for c, _ in OPERATORS["aggregate"].eff(st, params).columns]
    d = op_to_primitive(_make_op("aggregate", params))
    frame = d.post(run_primitive(d.kind, register(), d.params))
    assert list(frame.columns) == claimed == ["item_id", "n"]


def test_the_primitive_alone_still_uses_the_stand_in():
    """The boundary: the executor's own output keeps the stand-in; only the
    planner's dispatch restores the name. The baseline adapters and the
    comparator's `__group` normalisation are untouched."""
    out = run_primitive("aggregate", register(), {"group_by": "item_id", "value_col": "item_id", "agg": "count"})
    assert list(out.columns) == ["item_id__group", "item_id"]


def test_the_ordinary_aggregate_is_unchanged():
    g = Goal.make(columns={"item_id": "str", "t": "float"}, grain={"grouped_by": ["item_id"]},
                  where=[], derive=[{"name": "t", "expr": "sum(amount)"}])
    f, _ = run(g)
    assert list(f.columns) == ["item_id", "t"] and f["t"].tolist() == [3.0, 3.0, 15.0]


def test_p23_collision_guard_still_fires():
    d = op_to_primitive(_make_op("aggregate", {"fn": "count", "col": "amount", "by": ["item_id"], "out_col": "item_id"}))
    from gdc.primitives import AggregateTypeError
    with pytest.raises(AggregateTypeError, match="collides with group key"):
        d.post(run_primitive(d.kind, register(), d.params))


# ── back-port shim ────────────────────────────────────────────────────────────
# gdc.integrate.op_to_primitive returns a (kind, params, post) triple; the
# follow-up branch's returns a Dispatch with the same three fields. Wrap the
# triple so the assertions above read unchanged.
from collections import namedtuple as _namedtuple            # noqa: E402
from gdc.integrate import op_to_primitive as _pr_op_to_primitive  # noqa: E402
_Dispatch = _namedtuple("_Dispatch", "kind params post")


def op_to_primitive(op):                                       # noqa: F811
    return _Dispatch(*_pr_op_to_primitive(op))
