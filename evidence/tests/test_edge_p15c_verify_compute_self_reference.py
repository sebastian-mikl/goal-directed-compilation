"""P15c — verify_compute recomputed its reference over the OUTPUT frame, so a
compute that overwrites one of its own inputs (`cost*qty -> cost`) was refused
although the primitive's result was correct: by the time the reference was
taken, `cost` already held the product. The reference is now taken over
`source`, the frame the primitive read. NOT wiring verify_primitive in.
Confirmed to fail with the fix reverted."""
from __future__ import annotations

import pandas as pd
import pytest

from gdc.contract import Goal
from gdc.integrate import op_to_primitive
from gdc.primitives import run_primitive, verify_primitive
from gdc.search import Unreachable, plan
from gdc.state import State

SCHEMA = {"item_id": "str", "cost": "float", "qty": "float"}


def _frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"item_id": "R-1", "cost": 10.0, "qty": 3.0},
        {"item_id": "R-2", "cost": 20.0, "qty": 2.0},
        {"item_id": "R-3", "cost": 5.0,  "qty": 1.0},
    ])


SELF = {"new_col": "cost", "expr": "cost*qty"}       # overwrites its own input
FRESH = {"new_col": "total", "expr": "cost*qty"}     # a new column


def test_self_referencing_compute_is_correct_and_now_verifies():
    """The defect itself. Before P15c: (False, "... values != eval('cost*qty')")
    for a result that is right."""
    src = _frame()
    out = run_primitive("compute", src, SELF)
    assert out["cost"].tolist() == [30.0, 40.0, 5.0], "the primitive was always right"
    ok, why = verify_primitive("compute", SELF, src, out)
    assert ok, why


def test_verify_is_not_blind_after_the_fix():
    """Recomputing over source must still catch a WRONG self-referencing
    output — the fix relocates the reference, it does not weaken it."""
    src = _frame()
    out = run_primitive("compute", src, SELF)
    out.loc[out.index[0], "cost"] = 999.0
    ok, why = verify_primitive("compute", SELF, src, out)
    assert not ok and "!= eval" in why


def test_fresh_column_compute_verifies_as_before():
    src = _frame()
    out = run_primitive("compute", src, FRESH)
    ok, _ = verify_primitive("compute", FRESH, src, out)
    assert ok
    out.loc[out.index[1], "total"] = 0.0
    ok, _ = verify_primitive("compute", FRESH, src, out)
    assert not ok


def test_chained_compute_verifies_each_step_against_its_own_source():
    """A second compute reading a column the first produced: `source` for step
    2 is step 1's output, so the reference finds the column there."""
    s0 = _frame()
    s1 = run_primitive("compute", s0, FRESH)                       # total = cost*qty
    step2 = {"new_col": "half", "expr": "total/qty"}
    s2 = run_primitive("compute", s1, step2)
    assert verify_primitive("compute", FRESH, s0, s1)[0]
    assert verify_primitive("compute", step2, s1, s2)[0]


def test_no_source_refuses_rather_than_passes():
    """A verify that cannot recompute must say so, not report ok (P15g's rule)."""
    out = run_primitive("compute", _frame(), FRESH)
    ok, why = verify_primitive("compute", FRESH, None, out)
    assert not ok and "cannot recompute" in why


def test_row_count_change_is_refused():
    src = _frame()
    out = run_primitive("compute", src, FRESH).iloc[:2]
    ok, why = verify_primitive("compute", FRESH, src, out)
    assert not ok and "row count" in why


def test_mode_c_is_reachable_from_a_goal_and_verifies_end_to_end():
    """compute_pre does not refuse an existing new_col, so a Goal can declare
    `cost = cost*qty`. Plan it, run each step through the executor, and verify
    each step against the frame it read."""
    goal = Goal.make(columns={"item_id": "str", "cost": "float"}, grain="row",
                     where=[], derive=[{"name": "cost", "expr": "cost*qty"}])
    p = plan(State.from_base(SCHEMA), goal)
    assert not isinstance(p, Unreachable), p
    kinds = [o.kind for o in p]
    assert "compute" in kinds
    frame = _frame()
    for op in p:
        d = op_to_primitive(op)
        nxt = run_primitive(d.kind, frame, d.params)
        if d.post is not None:
            nxt = d.post(nxt)
        if d.kind == "compute":
            ok, why = verify_primitive("compute", d.params, frame, nxt)
            assert ok, why
        frame = nxt
    assert frame["cost"].tolist() == [30.0, 40.0, 5.0]


# ── back-port shim ────────────────────────────────────────────────────────────
# gdc.integrate.op_to_primitive returns a (kind, params, post) triple; the
# follow-up branch's returns a Dispatch with the same three fields. Wrap the
# triple so the assertions above read unchanged.
from collections import namedtuple as _namedtuple            # noqa: E402
from gdc.integrate import op_to_primitive as _pr_op_to_primitive  # noqa: E402
_Dispatch = _namedtuple("_Dispatch", "kind params post")


def op_to_primitive(op):                                       # noqa: F811
    return _Dispatch(*_pr_op_to_primitive(op))
