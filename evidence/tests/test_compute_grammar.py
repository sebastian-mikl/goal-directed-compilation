"""P15b — ONE compute grammar shared by the planner and the executor, plus a runtime
check that every executed step matches the State the planner derived for it.

Before: gdc.operators.compute_pre accepted any expression whose identifier tokens
were columns; gdc.primitives.safe_eval_expr evaluated exactly one `colA OP colB`. A
chained sum (`a + b + c`) or a single-column reference was therefore PLANNED but not
EXECUTED: prim_compute failed open (later, under P15a, refused), and the declared
column never appeared.

After: gdc.primitives.arith_parse is the single grammar (+ - * /, parentheses, unary
minus, numeric literals, column names; non-identifier column names tokenised
longest-first). compute_pre, validate_goal and safe_eval_expr all read it; an operand
with no numeric content raises ComputeExprError (P56). gdc.integrate.execute_plan
checks after every op that the frame's columns equal the planned State's columns
(and grouped keys are unique), raising PlanExecutionDivergence at the step that
diverged. Synthetic frames and goals, and the public employee domain for the
adapter tests.
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from gdc import primitives as P
from gdc.primitives import ComputeExprError, FilterTypeError, arith_parse, safe_eval_expr
from gdc.contract import validate_goal
from gdc.integrate import PlanExecutionDivergence, execute_plan, execute_plan_ordered
from gdc.operators import _make_op, apply, compute_pre
from gdc.order import OrderSpec
from gdc.search import Unreachable, plan
from gdc.state import Goal, State

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "evaluation" / "domains" / "employee"


# ── the grammar ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("expr,refs", [
    ("a + b", {"a", "b"}),
    ("a + b + c", {"a", "b", "c"}),
    ("(a + b) / c", {"a", "b", "c"}),
    ("-a * 2", {"a"}),
    ("a / b * 100", {"a", "b"}),
    ("a", {"a"}),
    ("1.5", set()),
])
def test_grammar_accepts_arithmetic(expr, refs):
    p = arith_parse(expr)
    assert p is not None and set(p.refs) == refs


@pytest.mark.parametrize("expr", [
    "sum(a)", "a / sum(a)", "f0.a", "*", "a > 1", "'x'", "a + True", "a ** 2",
    "a // b", "a % b", "a if b else c", "[a]", "", "   ", "a b", "lambda: 1",
    "__import__('os').system('x')", "__gdc_col_0__", "count(*)", "None", "1j",
    "1" + "0" * 400,
])
def test_grammar_rejects_everything_else(expr):
    assert arith_parse(expr, ["a", "b", "c"]) is None


def test_non_identifier_columns_tokenise_longest_first():
    """Raw column names with spaces, parentheses or operator characters are single
    operands; the longest known name wins at identifier boundaries."""
    cols = ["Pay", "Base Pay (USD)", "Bonus (USD)", "Q1", "Q1 - Planned (USD)"]
    assert set(arith_parse("Pay - Base Pay (USD)", cols).refs) == {"Pay", "Base Pay (USD)"}
    assert set(arith_parse("Base Pay (USD) - Bonus (USD)", cols).refs) == \
        {"Base Pay (USD)", "Bonus (USD)"}
    assert set(arith_parse("Q1 - Planned (USD) - Pay", cols).refs) == \
        {"Q1 - Planned (USD)", "Pay"}
    assert set(arith_parse("Q1 - Pay", cols).refs) == {"Q1", "Pay"}
    # without the column list a parenthesised name looks like a call: rejected
    assert arith_parse("Pay - Base Pay (USD)") is None


# ── the executor evaluates exactly the grammar ────────────────────────────────

def _frame():
    return pd.DataFrame({"a": [1, 2, 3], "b": [10.0, 20.0, 30.0], "c": [2, 4, 5],
                         "txt": ["x", "y", "z"], "Base Pay (USD)": [100, 200, 300]})


def test_executor_chained_and_precedence():
    df = _frame()
    assert list(safe_eval_expr("a + b + c", df)) == [13.0, 26.0, 38.0]
    assert list(safe_eval_expr("a + b * c", df)) == [21.0, 82.0, 153.0]
    assert list(safe_eval_expr("(a + b) / c", df)) == [5.5, 5.5, 6.6]
    assert list(safe_eval_expr("-a", df)) == [-1, -2, -3]
    assert list(safe_eval_expr("Base Pay (USD) - b", df)) == [90.0, 180.0, 270.0]
    assert list(safe_eval_expr("7", df)) == [7.0, 7.0, 7.0]       # constant broadcasts


def test_executor_division_by_zero_is_inf_not_an_exception():
    """A column divided by zero was already inf under pandas; a constant divided by
    zero follows the same rule instead of raising ZeroDivisionError."""
    df = _frame()
    assert list(safe_eval_expr("a / 0", df)) == [float("inf")] * 3
    assert list(safe_eval_expr("1 / 0", df)) == [float("inf")] * 3


def test_executor_binary_unchanged_from_one_operator_evaluator():
    """Every `colA OP colB` evaluates exactly as the old evaluator did (same values,
    same dtype), so no existing compute changes."""
    df = _frame()
    old = {"+": lambda x, y: x + y, "-": lambda x, y: x - y,
           "*": lambda x, y: x * y, "/": lambda x, y: x / y}
    for op, fn in old.items():
        for l, r in [("a", "b"), ("b", "c"), ("c", "a"), ("a", "a")]:
            want = fn(pd.to_numeric(df[l], errors="coerce"), pd.to_numeric(df[r], errors="coerce"))
            got = safe_eval_expr(f"{l} {op} {r}", df)
            pd.testing.assert_series_equal(got, want, check_names=False)


def test_executor_out_of_grammar_or_missing_column_is_none():
    df = _frame()
    assert safe_eval_expr("sum(a)", df) is None
    assert safe_eval_expr("a + missing", df) is None


def test_prim_compute_refuses_what_the_grammar_does_not_cover():
    """P15a's fail-closed compute is kept: None from the evaluator is a typed refusal."""
    with pytest.raises(ComputeExprError, match="outside the compute grammar"):
        P.run_primitive("compute", _frame(), {"new_col": "z", "expr": "a ** 2"})
    with pytest.raises(ComputeExprError):
        P.run_primitive("compute", _frame(), {"new_col": "z", "expr": "a + missing"})


def test_non_numeric_operand_refuses_typed():
    """A column with no numeric content is refused (the S36 rule aggregate applies),
    never turned into an all-NaN column. A partly numeric column is not refused."""
    df = _frame()
    with pytest.raises(ComputeExprError):
        safe_eval_expr("txt", df)
    with pytest.raises(ComputeExprError):
        P.run_primitive("compute", df, {"new_col": "z", "expr": "a + txt"})
    assert issubclass(ComputeExprError, FilterTypeError)      # existing adapter mappings apply
    mixed = pd.DataFrame({"m": ["1", "two", "3"]})
    assert safe_eval_expr("m * 2", mixed).isna().tolist() == [False, True, False]


def test_verify_compute_uses_the_same_grammar():
    src = _frame()
    params = {"new_col": "t", "expr": "a + b + c"}
    out = P.run_primitive("compute", src, params)
    assert P.verify_primitive("compute", params, src, out)[0]
    ok, reason = P.verify_primitive("compute", {"new_col": "t", "expr": "txt"}, src,
                                    src.assign(t=1))
    assert not ok and "numeric" in reason


# ── planner and executor agree ────────────────────────────────────────────────

@pytest.mark.parametrize("expr", [
    "a + b", "a + b + c", "(a + b) / c", "a * 100", "a", "-a", "sum(a)",
    "a / sum(a)", "f0.a", "a + missing", "*", "a > b", "a ** 2",
])
def test_compute_pre_accepts_exactly_what_the_executor_evaluates(expr):
    df = _frame()[["a", "b", "c"]]
    st = State.from_base({c: "float" for c in df.columns})
    planned = compute_pre(st, {"new_col": "z", "expr": expr})
    executable = safe_eval_expr(expr, df) is not None
    assert planned == executable, (expr, planned, executable)


def test_validate_goal_refuses_out_of_grammar_compute():
    bad = Goal.make(columns=["r"], grain="row", where=[],
                    derive=[{"name": "r", "expr": "x ** 2"}])
    v = validate_goal(bad)
    assert not v.ok and "outside the compute grammar" in v.reason
    grouped = Goal.make(columns=["k", "m"], grain={"grouped_by": ["k"]}, where=[],
                        derive=[{"name": "t", "expr": "x + y + z"}, {"name": "m", "expr": "avg(t)"}])
    assert validate_goal(grouped).ok
    # a non-identifier name the goal itself introduces still tokenises as one operand
    named = Goal.make(columns=["total pay", "double pay"], grain="row", where=[],
                      derive=[{"name": "total pay", "expr": "x + y"},
                              {"name": "double pay", "expr": "total pay * 2"}])
    assert validate_goal(named).ok


def test_chained_derive_plans_and_executes():
    """The shape the old gap broke: a three-term derive, filtered on, then averaged
    per group and ranked. Plans, executes, and every value is right."""
    df = pd.DataFrame({"g": list("aabbc"), "x": [1, 2, 3, 4, 5], "y": [10, 20, 30, 40, 50],
                       "z": [100, 200, 300, 400, 500]})
    goal = Goal.make(columns=["g", "avg_t"], grain={"grouped_by": ["g"]},
                     where=[{"col": "t", "op": ">", "val": 150}],
                     derive=[{"name": "t", "expr": "x + y + z"},
                             {"name": "avg_t", "expr": "avg(t)"}])
    p = plan(State.from_base({c: "float" for c in df.columns}), goal)
    assert not isinstance(p, Unreachable)
    out = execute_plan_ordered(p, df, OrderSpec.make(order_by=[{"col": "avg_t", "dir": "desc"}],
                                                     limit=2))
    assert out["g"].tolist() == ["c", "b"]
    assert out["avg_t"].tolist() == [555.0, 388.5]       # c: 555; b: (333 + 444) / 2


# ── runtime planner/executor agreement ────────────────────────────────────────

def _op(kind, **params):
    return _make_op(kind, params)


def _fail_open(monkeypatch, kind, fn):
    """Swap one registered primitive for a deliberately wrong one."""
    monkeypatch.setitem(P.REGISTRY, kind, dataclasses.replace(P.REGISTRY[kind], fn=fn))


def test_divergence_raised_at_the_failing_compute_step(monkeypatch):
    """A compute that returns its input unchanged is refused AT that step, naming the
    planned column that is missing."""
    _fail_open(monkeypatch, "compute", lambda f, p: f.copy())
    df = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
    plan_ops = [_op("compute", new_col="s", expr="x + y"), _op("select", cols=["s"])]
    with pytest.raises(PlanExecutionDivergence, match=r"step 0 \(compute\).*missing \['s'\]"):
        execute_plan(plan_ops, df)


def test_divergence_on_a_projection_that_leaks_columns(monkeypatch):
    """A projection that returns every column is refused, naming the leaked ones."""
    _fail_open(monkeypatch, "project", lambda f, p: f.copy())
    df = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
    with pytest.raises(PlanExecutionDivergence, match=r"step 1 \(select\).*unexpected \['y'\]"):
        execute_plan([_op("filter", col="x", op=">", val=0), _op("select", cols=["x"])], df)


def test_divergence_on_an_aggregate_that_does_not_group(monkeypatch):
    """Planned columns present but keys not unique: the grouping did not happen."""
    _fail_open(monkeypatch, "aggregate",
               lambda f, p: f[[p["group_by"], p["value_col"]]].copy())
    df = pd.DataFrame({"k": list("aab"), "v": [1, 2, 3]})
    with pytest.raises(PlanExecutionDivergence, match="group keys are not unique"):
        execute_plan([_op("aggregate", fn="sum", col="v", by=["k"], out_col="v")], df)


def test_divergence_on_a_global_aggregate_with_many_rows(monkeypatch):
    _fail_open(monkeypatch, "aggregate", lambda f, p: f[[p["value_col"]]].copy())
    df = pd.DataFrame({"k": list("aab"), "v": [1, 2, 3]})
    with pytest.raises(PlanExecutionDivergence, match="global aggregate grain"):
        execute_plan([_op("aggregate", fn="sum", col="v", by=[], out_col="v")], df)


def test_a_select_keeping_some_group_keys_is_not_a_divergence():
    """Grouped by (k, j), then a select keeps k only (select_pre admits it): rows
    repeat on k, which is what the State says, so nothing diverged. Was: the
    uniqueness check ran on the kept key and raised. With every key kept, the
    check still runs."""
    from gdc.operators import select_pre
    df = pd.DataFrame({"k": list("aabb"), "j": list("xyxx"), "v": [1.0, 2.0, 3.0, 4.0]})
    agg = _op("aggregate", fn="avg", col="v", by=("k", "j"), out_col="m")
    keep_k = _op("select", cols=("k", "m"))
    assert select_pre(apply(agg, State.from_base({c: "unknown" for c in df.columns})),
                      keep_k.params)
    out = execute_plan([agg, keep_k], df)
    assert sorted(out["k"].tolist()) == ["a", "a", "b"] and len(out) == 3
    assert len(execute_plan([agg, _op("select", cols=("k", "j", "m"))], df)) == 3


def test_divergence_checked_on_the_terminal_select_after_ordering(monkeypatch):
    """execute_plan_ordered's case 2 (sort the wide frame, then project) checks the
    projection too."""
    _fail_open(monkeypatch, "project", lambda f, p: f.copy())
    df = pd.DataFrame({"k": list("abc"), "v": [3, 1, 2]})
    goal = Goal.make(columns=["k"], grain="row", where=[],
                     derive=[{"name": "w", "expr": "v * 2"}])
    p = plan(State.from_base({"k": "str", "v": "float"}), goal)
    with pytest.raises(PlanExecutionDivergence, match=r"\(select\)"):
        execute_plan_ordered(p, df, OrderSpec.make(order_by=[("w", "desc")]))


def test_key_aggregated_on_itself_is_not_a_divergence():
    """count(k) by k: prim_aggregate keeps the key under 'k__group' and the
    executor's post-rename restores it (P57); either way the column IS the
    planned key."""
    df = pd.DataFrame({"k": list("aab"), "v": [1, 2, 3]})
    agg = _op("aggregate", fn="count", col="k", by=["k"], out_col="n")
    out = execute_plan([agg, _op("select", cols=["k", "n"])], df)
    assert out.to_dict("list") == {"k": ["a", "b"], "n": [2, 1]}


def test_no_divergence_on_ordinary_plans():
    df = pd.DataFrame({"k": list("aabc"), "v": [1.0, 2.0, 3.0, 4.0], "w": [5, 6, 7, 8]})
    plans = [
        [_op("filter", col="v", op=">", val=1.0), _op("select", cols=["k", "v"])],
        [_op("compute", new_col="s", expr="v + w"), _op("aggregate", fn="sum", col="s", by=["k"],
                                                       out_col="tot")],
        [_op("aggregate", fn="avg", col="v", by=[], out_col="m")],
        [_op("aggregate", fn="sum", col="v", by=["k", "w"], out_col="t")],
        [_op("compute", new_col="v", expr="v * 2")],          # overwrites an existing column
    ]
    for p in plans:
        execute_plan(p, df)


# ── the live network adapter maps both typed errors to refusals ──────────────

pytestmark_domain = pytest.mark.skipif(
    not (DOMAIN / "employees.pkl").exists(), reason="employee domain data absent")


def _text_block(t):
    return type("T", (), {"type": "text", "text": t})()


class _Client:
    def __init__(self, *responses):
        q = list(responses)

        class _Msgs:
            def create(self, **kw):
                return type("R", (), {"content": q.pop(0), "usage": None})()
        self.messages = _Msgs()


def _run_network(emission):
    from evidence.eval_run.adapters.network import NetworkAdapter
    from evidence.eval_run.briefing import load_briefing
    reg = pd.read_pickle(DOMAIN / "employees.pkl")
    brief = load_briefing(DOMAIN / "schema.json", DOMAIN / "employees.pkl")
    ad = NetworkAdapter(name="network", client=_Client([_text_block(json.dumps(emission))]),
                        register=reg, briefing=brief, model="m",
                        schema_path=DOMAIN / "schema.json")
    return ad.run("synthetic", seed=0), reg


@pytestmark_domain
def test_network_adapter_executes_a_chained_compute():
    rec, reg = _run_network(
        {"goal": {"columns": ["department", "m"], "grain": {"grouped_by": ["department"]},
                  "where": [],
                  "derive": [{"name": "t", "expr": "annual_salary + bonus_target + 1"},
                             {"name": "m", "expr": "avg(t)"}]},
         "order": {"order_by": [{"col": "m", "dir": "desc"}], "limit": 3}})
    assert rec["status"] == "completed", rec.get("error")
    want = ((reg["Annual Salary"] + reg["Bonus Target"] + 1)
            .groupby(reg["Department"]).mean().sort_values(ascending=False).head(3))
    got = [float(r["m"]) for r in rec["result"]]
    assert got == pytest.approx(list(want))


@pytestmark_domain
def test_network_adapter_refuses_arithmetic_on_text():
    rec, _ = _run_network({"goal": {"columns": ["name", "x"], "grain": "row", "where": [],
                                    "derive": [{"name": "x", "expr": "department"}]},
                           "order": {"order_by": [], "limit": None}})
    assert rec["status"] == "refused"
    assert rec["error"].startswith("ComputeExprError")


@pytestmark_domain
def test_network_adapter_refuses_out_of_grammar_derive_up_front():
    rec, _ = _run_network({"goal": {"columns": ["r"], "grain": "row", "where": [],
                                    "derive": [{"name": "r", "expr": "annual_salary ** 2"}]},
                           "order": {"order_by": [], "limit": None}})
    assert rec["status"] == "refused"
    assert rec["error"].startswith("ContractError(goal)") and "compute grammar" in rec["error"]


@pytestmark_domain
def test_network_adapter_maps_a_divergence_to_a_typed_refusal(monkeypatch):
    _fail_open(monkeypatch, "compute", lambda f, p: f.copy())
    rec, _ = _run_network({"goal": {"columns": ["name", "x"], "grain": "row", "where": [],
                                    "derive": [{"name": "x", "expr": "annual_salary * 2"}]},
                           "order": {"order_by": [], "limit": None}})
    assert rec["status"] == "refused"
    assert rec["error"].startswith("PlanExecutionDivergence: step "), rec["error"]
    assert "plan_execution_divergence" in rec["artifact"]


@pytestmark_domain
def test_replay_path_maps_a_divergence_and_a_compute_refusal_like_the_adapter(monkeypatch):
    """scripts/replay_goals.run_deterministic is the pipeline's mirror of
    NetworkAdapter.run; it must refuse, not crash, on the same errors, and label
    a typed refusal by its class as the adapter does (P15f)."""
    from scripts.replay_goals import DomainContext, run_deterministic
    ctx = DomainContext("employee")
    goal = Goal.make(columns=["name", "x"], grain="row", where=[],
                     derive=[{"name": "x", "expr": "annual_salary + department"}])
    status, _, err = run_deterministic(ctx, goal, OrderSpec.make())
    assert status == "refused" and err.startswith("ComputeExprError: "), err
    _fail_open(monkeypatch, "compute", lambda f, p: f.copy())
    goal = Goal.make(columns=["name", "x"], grain="row", where=[],
                     derive=[{"name": "x", "expr": "annual_salary * 2"}])
    status, _, err = run_deterministic(ctx, goal, OrderSpec.make())
    assert status == "refused" and err.startswith("PlanExecutionDivergence: "), err


# ── authored from semantics only ──────────────────────────────────────────────

def test_no_query_ids_in_the_new_code():
    for rel in ["gdc/primitives.py", "gdc/operators.py", "gdc/contract.py",
                "gdc/integrate.py"]:
        src = (ROOT / rel).read_text()
        assert not re.search(r"\b(t[1-5]_\d{2}|oor_\d{2}|hv_\d{2}|wta_\d{2}|world_\d{2}|bike_\d{2})\b",
                             src), rel
