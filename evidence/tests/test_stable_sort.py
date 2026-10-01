"""The executor's sort is the stable sort gdc/order.py documents.

Was: prim_sort called pandas' sort_values with the default kind (quicksort) — not
stable on a single key, so rows tied on the sort key came out in an order that
depended on the numpy build and the platform, and a top-k cut through a tie could
return a different row on another machine. gdc/order.apply_order documented
"stable multi-key sort ... ties keep input order", but the live path never ran it.

Now gdc.primitives.stable_sort is the one sort: stable, each key in its own
direction, nulls last, ties in input order; a mixed-type column is ordered by type,
then value, instead of raising. prim_sort (network, planner and react through
tools_decl) and apply_order both run it. Synthetic frames only.
"""
from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from gdc.primitives import prim_sort, run_primitive, stable_sort
from gdc.integrate import apply_order_via_executor, execute_plan_ordered
from gdc.operators import _make_op
from gdc.order import OrderSpec, apply_order


def _ties():
    # k ties three ways at 2 and twice at 1; `id` records the input position.
    return pd.DataFrame({"k": [2, 1, 2, 3, 1, 2], "id": list("abcdef")})


# ── ties keep input order ─────────────────────────────────────────────────────

def test_tie_keeps_input_order_ascending():
    out = prim_sort(_ties(), {"by": ["k"], "ascending": [True]})
    assert out["id"].tolist() == ["b", "e", "a", "c", "f", "d"]


def test_tie_keeps_input_order_descending():
    """Descending reverses the KEY order only; tied rows keep their input order."""
    out = prim_sort(_ties(), {"by": "k"})                    # default: descending
    assert out["id"].tolist() == ["d", "a", "c", "f", "b", "e"]


def test_top_k_cut_through_a_tie_returns_the_first_tied_rows_in_input_order():
    """The case the unstable sort made platform-dependent: a limit that cuts a tie."""
    out = apply_order_via_executor(_ties(), OrderSpec.make(order_by=[("k", "desc")], limit=3))
    assert out["id"].tolist() == ["d", "a", "c"]


@pytest.mark.parametrize("asc", [True, False])
@pytest.mark.parametrize("dtype", ["int64", "float64"])
def test_tie_keeps_input_order_on_a_frame_large_enough_for_quicksort(asc, dtype):
    """Below ~16 rows numpy's quicksort falls back to an insertion sort, which
    happens to be stable; above that it is not, and tied rows came back in an
    order no input fixed."""
    n = 60
    df = pd.DataFrame({"k": pd.Series([i % 3 for i in range(n)], dtype=dtype), "pos": range(n)})
    out = prim_sort(df, {"by": ["k"], "ascending": [asc]})
    for _, grp in out.groupby("k", sort=False):
        assert grp["pos"].is_monotonic_increasing
    top = apply_order_via_executor(df, OrderSpec.make(order_by=[("k", "asc" if asc else "desc")],
                                                      limit=5))
    first = 0 if asc else 2
    assert top["pos"].tolist() == [p for p in range(n) if p % 3 == first][:5]


def test_multi_key_mixed_directions_with_ties():
    df = pd.DataFrame({"g": ["x", "y", "x", "y", "x"], "v": [1, 2, 1, 2, 3],
                       "id": [0, 1, 2, 3, 4]})
    out = prim_sort(df, {"by": ["g", "v"], "ascending": [True, False]})
    assert out["id"].tolist() == [4, 0, 2, 1, 3]


def test_nulls_last_in_both_directions():
    df = pd.DataFrame({"k": [np.nan, 2.0, 1.0, np.nan, 2.0], "id": list("abcde")})
    assert prim_sort(df, {"by": "k", "ascending": True})["id"].tolist() == list("cbead")
    assert prim_sort(df, {"by": "k", "ascending": False})["id"].tolist() == list("becad")


@pytest.mark.parametrize("dtype", ["object", "string[python]", "string[pyarrow]"])
def test_string_keys_are_stable_for_every_string_storage(dtype):
    if dtype == "string[pyarrow]":
        pytest.importorskip("pyarrow")
    df = pd.DataFrame({"k": pd.array(["b", "a", "b", "a", "c"], dtype=dtype),
                       "id": [0, 1, 2, 3, 4]})
    assert prim_sort(df, {"by": "k", "ascending": True})["id"].tolist() == [1, 3, 0, 2, 4]
    assert prim_sort(df, {"by": "k", "ascending": False})["id"].tolist() == [4, 0, 2, 1, 3]


# ── the order is a function of the input rows, not of the sort algorithm ──────

def _reference(df, cols, ascending):
    """Python's sorted() is stable by specification: one pass per key, last key
    first, nulls last in each pass."""
    asc = ascending if isinstance(ascending, list) else [ascending] * len(cols)
    rows = list(range(len(df)))
    for c, a in reversed(list(zip(cols, asc))):
        vals = df[c].tolist()
        present = [i for i in rows if not pd.isna(vals[i])]
        missing = [i for i in rows if pd.isna(vals[i])]
        # sorted(reverse=True) keeps equal elements in input order too
        rows = sorted(present, key=lambda i: vals[i], reverse=not a) + missing
    return rows


@pytest.mark.parametrize("seed", range(20))
def test_matches_a_stable_reference_on_random_frames_with_many_ties(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 40)
    df = pd.DataFrame({
        "i": [rng.randint(0, 3) for _ in range(n)],
        "f": [rng.choice([0.5, 1.5, np.nan]) for _ in range(n)],
        "s": [rng.choice(["p", "q", "r"]) for _ in range(n)],
        "pos": range(n),
    })
    cols = rng.sample(["i", "f", "s"], rng.randint(1, 3))
    asc = [rng.random() < 0.5 for _ in cols]
    got = stable_sort(df, cols, asc)["pos"].tolist()
    assert got == _reference(df, cols, asc)


def test_permuting_the_input_moves_only_tied_rows():
    """Rows that are not tied land in the same positions whatever the input order;
    tied rows follow the input. Input order is the register's row order, which is
    data, not platform."""
    df = pd.DataFrame({"k": [5, 3, 5, 1, 4], "id": list("abcde")})
    out1 = prim_sort(df, {"by": "k"})["id"].tolist()
    out2 = prim_sort(df.iloc[::-1].reset_index(drop=True), {"by": "k"})["id"].tolist()
    assert out1 == ["a", "c", "e", "b", "d"] and out2 == ["c", "a", "e", "b", "d"]


# ── a mixed-type column is ordered, not a crash ───────────────────────────────

def test_mixed_type_column_orders_numbers_then_strings_then_nulls():
    df = pd.DataFrame({"k": pd.array([3, "a", 1, None, "b"], dtype=object),
                       "id": list("abcde")})
    assert prim_sort(df, {"by": "k", "ascending": True})["id"].tolist() == list("cabed")
    assert prim_sort(df, {"by": "k", "ascending": False})["id"].tolist() == list("ebacd")


# ── one code path: apply_order, the executor, the baselines' order step ──────

def test_apply_order_and_the_live_path_agree():
    spec = OrderSpec.make(order_by=[("k", "desc"), ("id", "asc")], limit=4)
    df = _ties().sample(frac=1.0, random_state=3).reset_index(drop=True)
    assert apply_order(df, spec).equals(apply_order_via_executor(df, spec).reset_index(drop=True))
    spec_tie = OrderSpec.make(order_by=[("k", "asc")])
    assert apply_order(_ties(), spec_tie)["id"].tolist() == \
        apply_order_via_executor(_ties(), spec_tie)["id"].tolist()


def test_baselines_order_step_runs_the_same_sort():
    from evidence.eval_run.tools_decl import _apply_one
    frames = {"register": _ties()}
    _, name = _apply_one("order", {"input": "register",
                                   "order_by": [{"col": "k", "dir": "desc"}], "limit": 3},
                         frames)
    assert frames[name]["id"].tolist() == ["d", "a", "c"]


def test_gdc_ordered_plan_cuts_a_tie_deterministically():
    df = pd.DataFrame({"g": list("pqrst"), "v": [7, 9, 7, 7, 1]})
    sel = _make_op("select", {"cols": ["g"]})
    out = execute_plan_ordered([sel], df, OrderSpec.make(order_by=[("v", "desc")], limit=3))
    # derive-free order key not in the select: case 2 sorts the wide frame first
    assert out["g"].tolist() == ["q", "p", "r"]


# ── an absent sort key is a typed refusal ─────────────────────────────────────

def test_an_absent_sort_key_is_a_typed_refusal_naming_it():
    """Was: prim_sort dropped the key and sorted on the rest, or returned the frame
    unsorted, so a following limit took the wrong top-k (the P26/P58 shape)."""
    from gdc.primitives import FilterTypeError, SortColumnError
    df = _ties()
    for by in (["nope"], ["nope", "k"], "nope"):
        with pytest.raises(SortColumnError, match="'nope'") as ei:
            prim_sort(df, {"by": by, "ascending": True})
        assert isinstance(ei.value, FilterTypeError)        # every adapter's refusal channel
    assert prim_sort(df, {"by": []}).equals(df)              # no key requested: no sort
    assert prim_sort(df, {}).equals(df)


def test_baselines_order_tool_with_an_absent_key_is_refused_not_a_wrong_top_k():
    from gdc.primitives import SortColumnError
    from evidence.eval_run.tools_decl import _apply_one
    frames = {"register": _ties()}
    with pytest.raises(SortColumnError):
        _apply_one("order", {"input": "register", "limit": 2,
                             "order_by": [{"col": "nope", "dir": "desc"}]}, frames)
    _, name = _apply_one("order", {"input": "register", "order_by": [], "limit": 2}, frames)
    assert frames[name]["id"].tolist() == ["a", "b"]         # limit-only call: unchanged


def test_gdc_never_reaches_an_absent_sort_key():
    """validate_order admits only output columns, and apply_order raises on any
    other; GDC's order step therefore never hands prim_sort an absent key."""
    df = _ties()
    with pytest.raises(KeyError):
        apply_order(df, OrderSpec.make(order_by=[("nope", "asc")]))


def test_sort_without_ties_equals_the_old_result():
    df = pd.DataFrame({"k": [3.0, 1.0, 2.0, np.nan], "s": ["c", "a", "b", "d"]})
    for asc in (True, False):
        want = df.sort_values(["k"], ascending=asc).reset_index(drop=True)
        assert run_primitive("sort", df, {"by": ["k"], "ascending": asc}).equals(want)
