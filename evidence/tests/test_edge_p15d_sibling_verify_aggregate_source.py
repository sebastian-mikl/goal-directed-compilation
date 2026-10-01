"""P15d-sibling — verify_aggregate passed when the SOURCE lacked the group
column, the value column, or was absent altogether: `(True, "source
unavailable for value check (schema ok)")`. SPEC §3.4 requires the output to
equal a RECOMPUTED groupby/agg; with no source to recompute from there is
nothing to pass. Same rule as P15g one line down. NOT wiring verify into the
live path. Confirmed to fail with the fix reverted.

P15d proper (a multi-key group_by refused because the verifier assumes a
single string key) is NOT fixed here and is locked as still-open below so
that fixing it is a visible change."""
from __future__ import annotations

import pandas as pd
import pytest

from gdc.primitives import run_primitive, verify_primitive

P = {"group_by": "status", "value_col": "cost", "agg": "sum"}


def _frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"item_id": "R-1", "status": "pending",  "name": "ann", "cost": 10.0, "qty": 3.0},
        {"item_id": "R-2", "status": "pending",  "name": "bob", "cost": 20.0, "qty": 2.0},
        {"item_id": "R-3", "status": "approved", "name": "cev", "cost": 5.0,  "qty": 1.0},
    ])


def _out():
    return run_primitive("aggregate", _frame(), P)


def test_source_without_the_group_column_refuses():
    src = _frame().drop(columns=["status"])
    ok, why = verify_primitive("aggregate", P, src, _out())
    assert ok is False
    assert "group column 'status'" in why and "cannot recompute" in why


def test_source_without_the_value_column_refuses():
    src = _frame().drop(columns=["cost"])
    ok, why = verify_primitive("aggregate", P, src, _out())
    assert ok is False
    assert "value column 'cost'" in why and "cannot recompute" in why


def test_no_source_refuses():
    ok, why = verify_primitive("aggregate", P, None, _out())
    assert ok is False and "cannot recompute" in why


def test_a_plausible_output_over_the_wrong_source_is_not_waved_through():
    """The shape the old pass-through would have accepted: an output that LOOKS
    like an aggregate, checked against a source that cannot have produced it."""
    unrelated = pd.DataFrame({"x": [1, 2, 3]})
    ok, _ = verify_primitive("aggregate", P, unrelated, _out())
    assert ok is False


def test_correct_aggregate_over_its_real_source_still_verifies():
    ok, why = verify_primitive("aggregate", P, _frame(), _out())
    assert ok is True, why


def test_wrong_values_over_the_real_source_still_refuse():
    out = _out()
    out.loc[out.index[0], "cost"] = 999.0
    ok, why = verify_primitive("aggregate", P, _frame(), out)
    assert ok is False and "disagree" in why


def test_p15d_multi_key_is_still_open():
    """Documents, does not fix: a two-key group_by is refused at the OUTPUT
    check (`str(['status','name'])` is not a column) before the source is
    consulted. When P15d is fixed this test must change — deliberately."""
    P2 = {"group_by": ["status", "name"], "value_col": "cost", "agg": "sum"}
    out = run_primitive("aggregate", _frame(), P2)
    ok, why = verify_primitive("aggregate", P2, _frame(), out)
    assert ok is False and "output missing group column" in why
