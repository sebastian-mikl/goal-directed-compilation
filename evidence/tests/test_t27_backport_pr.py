"""T27 back-port: what the per-item test files (test_edge_p*, test_p5*) do not
cover.

- P15a shipped without a test file: prim_compute refuses an
  expression it cannot evaluate instead of returning the frame unchanged.
  With the shared compute grammar (P15b, test_compute_grammar.py) "cannot
  evaluate" means outside the grammar, a missing column, or a text operand.
- The back-port's own risks: the import order between gdc.primitives and
  gdc.state, this tree's `write` primitive surviving the port, and the plan-time
  type rule (P54) on a bool column.
- Two shapes the replay moved, locked so a later change cannot move them
  silently: an identity alias of a text column refuses typed, and `median` is
  not plannable (P22); validate_goal refuses it first, naming the function
  (test_unsupported_aggregate.py). Both cost the cells whose Goal used them.

All frames are synthetic. No corpus query ids, no corpus text.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from gdc import Goal, OrderSpec
from gdc.integrate import OrderApplicationError, aliased_view, run_goal_plan
from gdc.search import Unreachable
from gdc.primitives import (REGISTRY, ComputeExprError, FilterTypeError,
                            run_primitive)

ROOT = Path(__file__).resolve().parents[2]


def _frame():
    return pd.DataFrame({
        "g": ["x", "x", "y", "y", "z"],
        "a": [1.0, 2.0, 3.0, 4.0, 5.0],
        "b": [10.0, 20.0, 30.0, 40.0, 50.0],
        "c": [100.0, 200.0, 300.0, 400.0, 500.0],
        "uid": [1, 2, 3, 3, 4],
    })


def _compute(expr, new_col="n"):
    return run_primitive("compute", _frame(), {"new_col": new_col, "expr": expr})


# ── P15a: prim_compute refuses rather than failing open ──────────────────────

@pytest.mark.parametrize("expr", ["", "abs(a)", "a ** 2", "a > b", "a + missing", "'x'"])
def test_p15a_unevaluable_expr_refuses_typed(expr):
    """Was: the frame came back unchanged, with `n` never created. Out of the
    compute grammar, or naming a column the frame does not have."""
    with pytest.raises(ComputeExprError):
        _compute(expr)


def test_p15a_refusal_is_a_member_of_the_typed_family():
    assert issubclass(ComputeExprError, FilterTypeError)


def test_p15a_overwrite_with_an_unevaluable_expr_refuses():
    """Was: an existing column silently kept its old value."""
    with pytest.raises(ComputeExprError):
        _compute("a ** 2", new_col="a")


def test_p15a_single_binary_op_still_computes():
    out = _compute("a + b")
    assert out["n"].tolist() == [11.0, 22.0, 33.0, 44.0, 55.0]
    assert _compute("b / a")["n"].tolist() == [10.0] * 5


def test_p15a_propagates_through_apply_one_as_a_typed_refusal():
    from evidence.eval_run.tools_decl import _apply_one
    frames = {"register": _frame()}
    with pytest.raises(ComputeExprError):
        _apply_one("compute", {"input": "register", "new_col": "n",
                               "expr": "a + missing"}, frames)


def test_p15a_chained_derive_now_executes():
    """A three-operand derive feeding a grouped average, filtered and ordered on
    derived columns. Was: compute failed open, the derived column never
    existed, and the refusal surfaced later as an OrderApplicationError naming
    the order key; under P15a alone it refused at the compute step. With the
    shared grammar (P15b) it is evaluable, so it answers."""
    _, adf, schema = aliased_view(_frame())
    g = Goal.make(columns={"g": "str", "avg_t": "unknown"},
                  grain={"grouped_by": ["g"]},
                  where=[{"col": "t", "op": ">", "val": 200}],
                  derive=[{"name": "t", "expr": "a + b + c"},
                          {"name": "avg_t", "expr": "avg(t)"}])
    order = OrderSpec.make(order_by=[("avg_t", "desc")], limit=2)
    out, _ = run_goal_plan(g, order, adf, base_schema=schema)
    assert out["g"].tolist() == ["z", "y"]
    assert out["avg_t"].tolist() == [555.0, 388.5]


def test_p15a_text_operand_refuses_at_the_compute_step_not_as_order_error():
    """The step that cannot run is the step that refuses: a chained derive with a
    text operand plans (it is in the grammar) and the compute refuses (P56),
    never a later OrderApplicationError naming the order key."""
    df = _frame().assign(lbl=["p", "q", "r", "s", "t"])
    _, adf, schema = aliased_view(df)
    g = Goal.make(columns={"g": "str", "avg_t": "unknown"},
                  grain={"grouped_by": ["g"]}, where=[],
                  derive=[{"name": "t", "expr": "a + b + lbl"},
                          {"name": "avg_t", "expr": "avg(t)"}])
    with pytest.raises(ComputeExprError, match="lbl"):  # OrderApplicationError would propagate
        run_goal_plan(g, OrderSpec.make(order_by=[("avg_t", "desc")]), adf,
                      base_schema=schema)


# ── identity-alias derive: refused by the numeric-operand rule, not by scope ──

def test_identity_alias_derive_refuses_typed():
    """`h := src` is in the compute grammar (a bare column is a numeric copy),
    so it plans, and the executor refuses it because `src` has no numeric
    content (P56). It is NOT detection of whether `src` is the right column for
    the question: if the grammar learns renames of text columns, this Goal
    answers again."""
    df = pd.DataFrame({"src": ["L", "R", "L", "R"], "pid": [1, 2, 3, 3]})
    _, adf, schema = aliased_view(df)
    g = Goal.make(columns={"h": "unknown", "n": "unknown"},
                  grain={"grouped_by": ["h"]}, where=[],
                  derive=[{"name": "h", "expr": "src"},
                          {"name": "n", "expr": "count_distinct(pid)"}])
    with pytest.raises(ComputeExprError):
        run_goal_plan(g, OrderSpec.make(), adf, base_schema=schema)


# ── P22: median is not a declared fn ─────────────────────────────────────────

def test_median_is_unreachable_at_plan_time():
    """P22 declares {sum, mean, count, min, max, nunique}; Call-1 is offered
    sum/avg/count/min/max/count_distinct. median used to execute through
    pandas by accident; aggregate_pre now refuses it, so plan() exhausts for a
    caller that skips the contract. On the live path validate_goal refuses the
    Goal first, typed and naming 'median' (test_unsupported_aggregate.py). A
    Goal that used it loses its answer: a median that should be first-class
    needs its own declaration in the prompt, the contract and
    SUPPORTED_AGG_FNS together."""
    _, adf, schema = aliased_view(_frame())
    g = Goal.make(columns={"g": "str", "m": "unknown"},
                  grain={"grouped_by": ["g"]}, where=[],
                  derive=[{"name": "m", "expr": "median(a)"}])
    _, plan = run_goal_plan(g, OrderSpec.make(), adf, base_schema=schema)
    assert isinstance(plan, Unreachable)


# ── back-port: bool columns (P54's plan-time type rule) ──────────────────────

def test_bool_columns_are_declared_bool_not_float():
    _, _, schema = aliased_view(pd.DataFrame({"f": [True, False], "x": [1.0, 2.0],
                                              "i": [1, 2], "s": ["a", "b"]}))
    assert schema == {"f": "bool", "x": "float", "i": "float", "s": "str"}


@pytest.mark.parametrize("val, want", [(True, ["a", "c"]), ("True", ["a", "c"]),
                                       (False, ["b"])])
def test_bool_column_equality_plans_and_executes(val, want):
    """pandas counts bool as numeric, so a "float" declaration made filter_pre
    refuse `flag == True` while prim_select (string form for bool) runs it."""
    df = pd.DataFrame({"name": ["a", "b", "c"], "flag": [True, False, True]})
    _, adf, schema = aliased_view(df)
    g = Goal.make(columns={"name": "str"}, grain="row",
                  where=[{"col": "flag", "op": "==", "val": val}], derive=[])
    out, plan = run_goal_plan(g, OrderSpec.make(), adf, base_schema=schema)
    assert not isinstance(plan, Unreachable)
    assert out["name"].tolist() == want


def test_numeric_column_against_a_name_is_unreachable():
    """P54 at plan time: a numeric column compared with a non-numeric value.
    Was: string forms compared, a confident empty answer."""
    df = pd.DataFrame({"name": ["p", "q"], "ref": [7, 9], "region": ["r1", "r2"]})
    _, adf, schema = aliased_view(df)
    g = Goal.make(columns={"region": "str"}, grain="row",
                  where=[{"col": "ref", "op": "==", "val": "q"}], derive=[])
    _, plan = run_goal_plan(g, OrderSpec.make(), adf, base_schema=schema)
    assert isinstance(plan, Unreachable)


# ── back-port: this tree's own surface survives, import order is safe ────────

def test_write_primitive_is_still_registered(tmp_path):
    """The follow-up branch removed `write`; this tree keeps it. The port must not drop it."""
    assert "write" in REGISTRY
    out = run_primitive("write", _frame(), {"path": str(tmp_path / "w.csv")})
    assert out.shape == _frame().shape and (tmp_path / "w.csv").exists()


@pytest.mark.parametrize("first", ["gdc.primitives", "gdc", "gdc.integrate"])
def test_import_order_is_safe_in_a_fresh_interpreter(first):
    """prim_select imports gdc.state lazily: gdc/__init__ ->
    schema_bridge -> integrate imports gdc.primitives, so a module-level import
    there is circular when gdc.primitives is imported first."""
    code = (
        f"import importlib; importlib.import_module({first!r});"
        "import pandas as pd; from gdc.primitives import run_primitive;"
        "f = pd.DataFrame({'x': [1, 2]});"
        "o = run_primitive('select', f, {'predicates': [{'col': 'x', 'op': 'eq', 'value': 2}]});"
        "assert o['x'].tolist() == [2], o"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                       text=True, env={"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
                                       "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
