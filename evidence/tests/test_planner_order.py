"""S13 Part A — ordering-as-projection validations.

A-fp  : ordering does not enter Goal identity (top-5 / top-10 / all share a fingerprint).
A-apply: apply_order sorts asc/desc and limits correctly.
A-reject: validate_order rejects an order_by column absent from the goal, and a negative limit.
(A-regression — V1–V5 + A0–A4 unchanged — is covered by the existing planner test files
 still passing; this part is additive and the planner core is untouched.)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gdc.probe import DEMO_SCHEMA
from gdc import Goal, fingerprint, plan, satisfies, apply_plan, State
from gdc import OrderSpec, apply_order, validate_order


def _grouped_total_cost_goal() -> Goal:
    # the SAME relation underlies "top 5 by total_cost", "top 10", and "all":
    # owner -> sum(cost). Ordering/limit are NOT part of it.
    return Goal.make(
        columns=["owner", "total_cost"],
        grain={"grouped_by": ["owner"]},
        where=[],
        derive=[{"name": "total_cost", "expr": "sum(cost)"}],
    )


# ── A-fp: ordering lives in OrderSpec, never in Goal identity ────────────────

def test_A_fp_ordering_does_not_enter_goal_fingerprint():
    goal = _grouped_total_cost_goal()
    top5 = OrderSpec.make(order_by=[{"col": "total_cost", "dir": "desc"}], limit=5)
    top10 = OrderSpec.make(order_by=[{"col": "total_cost", "dir": "desc"}], limit=10)
    allrows = OrderSpec.make()                              # no ordering, no limit

    # three different presentations, ONE goal identity
    fp = fingerprint(goal)
    assert fingerprint(_grouped_total_cost_goal()) == fp
    # the OrderSpec carries the only difference; the Goal is byte-identical
    assert top5 != top10 != allrows
    assert "order_by" not in {f for f in goal.__dataclass_fields__}
    assert "limit" not in {f for f in goal.__dataclass_fields__}
    # and the goal is reachable + the relation is the same regardless of OrderSpec
    p = plan(State.from_base(DEMO_SCHEMA), goal)
    assert isinstance(p, list) and satisfies(apply_plan(State.from_base(DEMO_SCHEMA), p), goal)


# ── A-apply: stable multi-key sort + limit ──────────────────────────────────

def test_A_apply_sorts_and_limits():
    df = pd.DataFrame({"owner": list("abcdefghij"),
                       "total_cost": [3, 1, 4, 1, 5, 9, 2, 6, 5, 3]})
    top3 = apply_order(df, OrderSpec.make(order_by=[{"col": "total_cost", "dir": "desc"}], limit=3))
    assert list(top3["total_cost"]) == [9, 6, 5]            # desc, top-3 of 10
    asc = apply_order(df, OrderSpec.make(order_by=[{"col": "total_cost", "dir": "asc"}]))
    assert list(asc["total_cost"]) == sorted(df["total_cost"])
    # multi-key: total_cost desc, then owner asc as tiebreak (stable)
    multi = apply_order(df, OrderSpec.make(
        order_by=[{"col": "total_cost", "dir": "desc"}, {"col": "owner", "dir": "asc"}]))
    fives = multi[multi["total_cost"] == 5]
    assert list(fives["owner"]) == ["e", "i"]              # tie broken by owner asc
    # limit=None keeps all rows
    assert len(apply_order(df, OrderSpec.make())) == len(df)


# ── A-reject: contract on the OrderSpec ─────────────────────────────────────

def test_A_reject_bad_column_and_negative_limit():
    goal = _grouped_total_cost_goal()                       # output cols: owner, total_cost
    v_ok = validate_order(OrderSpec.make(order_by=[{"col": "total_cost", "dir": "desc"}], limit=5), goal)
    assert v_ok.ok

    v_col = validate_order(OrderSpec.make(order_by=[{"col": "severity", "dir": "desc"}]), goal)
    assert not v_col.ok and "not in goal output" in v_col.reason

    v_lim = validate_order(OrderSpec.make(order_by=[{"col": "total_cost", "dir": "asc"}], limit=-1), goal)
    assert not v_lim.ok and "negative limit" in v_lim.reason

    v_dir = validate_order(OrderSpec(order_by=(("total_cost", "sideways"),), limit=None), goal)
    assert not v_dir.ok and "direction" in v_dir.reason
