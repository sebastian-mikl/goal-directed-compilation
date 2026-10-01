"""An aggregate function outside the declared set is a typed refusal that names the
function, on every path that reaches the shared executor.

The declared set is the executor's (gdc.primitives.SUPPORTED_AGG_FNS: sum, mean,
count, min, max, nunique) plus the Goal spellings that map onto it
(AGG_FN_ALIASES: avg, count_distinct). Nothing is added to it here.

- GDC: validate_goal refuses a Goal that applies any other function, with the
  function's name in the reason, before planning: in a derive expr, and in a
  column, derive name, where column or group key that spells the application. Was: `median(col)` passed the
  structural rules, aggregate_pre (P22) refused it, and plan() ended in the opaque
  "Unreachable: exhausted operator universe"; before P22 pandas ran it.
- planner and react: prim_aggregate (P22) raises AggregateTypeError naming the
  function, which the planner adapter records as a refusal and react receives as an
  observation.

Synthetic frames and the public employee domain. No corpus query ids or text.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from gdc.primitives import (AGG_FN_ALIASES, SUPPORTED_AGG_FNS, AggregateTypeError,
                            FilterTypeError, prim_aggregate)
from gdc.contract import (DECLARED_AGG_FNS, parse_goal, undeclared_fn,
                              undeclared_fn_in_name, validate_goal)

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "evaluation" / "domains" / "employee"
UNDECLARED = ["median", "MEDIAN", "Avg", "std", "stddev", "var", "mode", "percentile",
              "first", "last", "idxmax", "prod", "size"]


def _goal(derive, grain=None, columns=None):
    grain = {"grouped_by": ["g"]} if grain is None else grain
    return parse_goal({"columns": columns or ["g", "v"], "grain": grain, "where": [],
                       "derive": derive})


# ── the declaration ──────────────────────────────────────────────────────────

def test_declared_set_is_the_executors_set_and_its_aliases_and_nothing_else():
    assert SUPPORTED_AGG_FNS == {"sum", "mean", "count", "min", "max", "nunique"}
    assert DECLARED_AGG_FNS == SUPPORTED_AGG_FNS | set(AGG_FN_ALIASES)
    assert "median" not in DECLARED_AGG_FNS


# ── GDC: refused at the contract, naming the function ────────────────────────

@pytest.mark.parametrize("fn", UNDECLARED)
def test_validate_goal_refuses_an_undeclared_fn_by_name(fn):
    v = validate_goal(_goal([{"name": "v", "expr": f"{fn}(x)"}]))
    assert not v.ok
    assert f"function {fn!r} is not a supported aggregate" in v.reason


def test_the_function_reason_wins_over_structural_reasons():
    """An undeclared fn at row grain used to read 'aggregate requires grouped grain';
    an undeclared fn(EXPR) is not split and used to read as a compute. The
    function is the real diagnosis."""
    row = validate_goal(_goal([{"name": "v", "expr": "median(x)"}], grain="row",
                              columns=["id", "v"]))
    assert "function 'median' is not a supported aggregate" in row.reason
    expr = validate_goal(_goal([{"name": "v", "expr": "median(x - y)"}]))
    assert "function 'median' is not a supported aggregate" in expr.reason
    nested = validate_goal(_goal([{"name": "s", "expr": "sum(x)"},
                                  {"name": "v", "expr": "s / median(x)"}]))
    assert "function 'median'" in nested.reason


def test_a_non_aggregate_function_in_a_compute_is_named_too():
    v = validate_goal(_goal([{"name": "v", "expr": "abs(x - y)"}], grain="row",
                            columns=["id", "v"]))
    assert not v.ok and "function 'abs'" in v.reason


@pytest.mark.parametrize("expr", ["avg(x)", "mean(x)", "count_distinct(x)", "nunique(x)",
                                  "sum(x)", "count(x)", "min(x)", "max(x)",
                                  "count(DISTINCT x)", "avg(x - y)"])
def test_validate_goal_accepts_every_declared_spelling(expr):
    assert validate_goal(_goal([{"name": "v", "expr": expr}])).ok


def test_no_false_positive_on_derives_without_an_undeclared_call():
    for expr in ("a - b", "count_entries / sum(count_entries)", "(a + b) * c", "a"):
        assert undeclared_fn(expr) is None, expr


def test_a_derive_name_that_applies_a_declared_fn_is_a_reference_not_an_application():
    """An emitted derive NAME such as `count(*)` survives the fn(EXPR) split as
    `_count(*)_pre`; the rule must not read it as a call to `_count`. The Goal is
    still refused, by the structural rule it always hit."""
    g = _goal([{"name": "count(*)", "expr": "count(*)"}], columns=["g", "count(*)"])
    assert dict(g.derive) == {"_count(*)_pre": "*", "count(*)": "count(_count(*)_pre)"}
    v = validate_goal(g)
    assert not v.ok and "not a supported aggregate" not in v.reason
    assert v.reason == "grouped grain requires exactly one aggregate"
    # referenced by another derive, it is still a reference
    g = _goal([{"name": "count(*)", "expr": "count(x)"}, {"name": "v", "expr": "count(*) * 2"}],
              grain="row", columns=["id", "v"])
    assert "not a supported aggregate" not in validate_goal(g).reason


def test_a_label_with_a_parenthesis_is_a_name_not_an_application():
    """A derive name or column such as `cost minus target (net)` or `share (%)` is a
    label, as the register's own `Tenure (Years)` is: the fn rule passes it, and a
    derive that refers to it is not read as calling `target` or `share`."""
    g = _goal([{"name": "cost minus target (net)", "expr": "cost - target"},
               {"name": "share (%)", "expr": "cost / target * 100"},
               {"name": "v", "expr": "share (%) * 2 - cost minus target (net)"}],
              grain="row", columns=["id", "cost minus target (net)", "v"])
    v = validate_goal(g)
    assert "not a supported aggregate" not in v.reason, v.reason
    assert undeclared_fn_in_name("Tenure (Years)") is None
    assert undeclared_fn_in_name("count(*)") is None
    assert undeclared_fn_in_name("median(cost)") == "median"


# The review's bypass: a Goal that also used the application as a NAME had it
# masked, so validate_goal passed and plan() ended in an opaque Unreachable.
BYPASS_GOALS = {
    "derive name": {"columns": ["region", "median(cost)"], "grain": {"grouped_by": ["region"]},
                    "where": [], "derive": [{"name": "median(cost)", "expr": "median(cost)"}]},
    "column": {"columns": ["region", "median(cost)"], "grain": {"grouped_by": ["region"]},
               "where": [], "derive": [{"name": "m", "expr": "median(cost)"}]},
    "where column": {"columns": ["region", "m"], "grain": {"grouped_by": ["region"]},
                     "where": [{"col": "median(cost)", "op": ">", "val": 1}],
                     "derive": [{"name": "m", "expr": "median(cost)"}]},
}


@pytest.mark.parametrize("case", sorted(BYPASS_GOALS))
def test_an_application_used_as_a_name_is_still_refused_naming_the_function(case):
    v = validate_goal(parse_goal(BYPASS_GOALS[case]))
    assert not v.ok
    assert "function 'median' is not a supported aggregate" in v.reason, v.reason


@pytest.mark.parametrize("goal, kind", [
    ({"columns": ["region", "median(cost)"], "grain": {"grouped_by": ["region"]}, "where": [],
      "derive": [{"name": "s", "expr": "sum(cost)"}]}, "column 'median(cost)'"),
    ({"columns": ["region", "s"], "grain": {"grouped_by": ["region"]}, "where": [],
      "derive": [{"name": "median(cost)", "expr": "sum(cost)"}]}, "derive name 'median(cost)'"),
    ({"columns": ["region", "s"], "grain": {"grouped_by": ["region"]},
      "where": [{"col": "median(cost)", "op": ">", "val": 1}],
      "derive": [{"name": "s", "expr": "sum(cost)"}]}, "where column 'median(cost)'"),
    ({"columns": ["median(cost)", "s"], "grain": {"grouped_by": ["median(cost)"]}, "where": [],
      "derive": [{"name": "s", "expr": "sum(cost)"}]}, "'median(cost)'"),
    ({"columns": ["region", "m"], "grain": {"grouped_by": ["region"]}, "where": [],
      "derive": [{"name": "m", "expr": "MEDIAN (cost)"}, {"name": "x", "expr": "m"}]}, "derive 'm'"),
])
def test_an_undeclared_application_is_refused_wherever_the_goal_spells_it(goal, kind):
    """A name that spells an undeclared application is not a register column (the
    network adapter aliases every column to a plain identifier), so it is refused
    naming the function, not planned into an Unreachable or labelled over a sum."""
    v = validate_goal(parse_goal(goal))
    assert not v.ok and kind in v.reason and "function " in v.reason, v.reason
    assert "is not a supported aggregate" in v.reason


def test_the_bypass_goals_never_reach_the_planner_end_to_end():
    from gdc.search import plan, Unreachable
    from gdc.state import State
    for g in BYPASS_GOALS.values():
        goal = parse_goal(g)
        assert isinstance(plan(State.from_base({"region": "str", "cost": "float"}), goal),
                          Unreachable)          # what validate_goal now pre-empts
        assert not validate_goal(goal).ok


def test_every_declared_fn_splits_an_expression_argument_the_same_way():
    for fn in ("avg", "count_distinct", "nunique", "sum"):
        g = _goal([{"name": "v", "expr": f"{fn}(x - y)"}])
        assert dict(g.derive) == {"_v_pre": "x - y", "v": f"{fn}(_v_pre)"}


def test_plan_without_the_contract_still_exhausts():
    """aggregate_pre (P22) is unchanged: a caller that skips validate_goal gets
    Unreachable, never a plan the executor would refuse."""
    from gdc.search import plan, Unreachable
    from gdc.state import State
    st = State.from_base({"g": "str", "x": "float"})
    assert isinstance(plan(st, _goal([{"name": "v", "expr": "median(x)"}])), Unreachable)
    assert not isinstance(plan(st, _goal([{"name": "v", "expr": "avg(x)"}])), Unreachable)


# ── the shared executor: typed, naming the function ──────────────────────────

@pytest.mark.parametrize("gb", ["g", [], ["g", "h"]])
def test_prim_aggregate_refuses_median_typed_on_every_grain(gb):
    df = pd.DataFrame({"g": ["a", "a", "b"], "h": ["p", "q", "p"], "x": [1, 2, 3]})
    with pytest.raises(AggregateTypeError, match="'median'") as ei:
        prim_aggregate(df, {"group_by": gb, "value_col": "x", "agg": "median"})
    assert isinstance(ei.value, FilterTypeError)            # every adapter's refusal channel


# ── end to end through the adapters (stub clients, no API) ───────────────────

needs_domain = pytest.mark.skipif(
    not (DOMAIN / "employees.pkl").exists(), reason="employee domain data absent")


def _text_block(t):
    return type("T", (), {"type": "text", "text": t})()


def _tool_block(name, inp, tid="t1"):
    return type("U", (), {"type": "tool_use", "name": name, "input": inp, "id": tid})()


class _Client:
    """Canned responses, one per messages.create call."""
    def __init__(self, *responses):
        q = list(responses)

        class _Msgs:
            def create(self, **kw):
                blocks = q.pop(0) if q else [_text_block("I cannot answer this.")]
                return type("R", (), {"content": blocks, "usage": None})()
        self.messages = _Msgs()


def _employee():
    from evidence.eval_run.briefing import load_briefing
    return (pd.read_pickle(DOMAIN / "employees.pkl"),
            load_briefing(DOMAIN / "schema.json", DOMAIN / "employees.pkl"))


def _network(expr):
    from evidence.eval_run.adapters.network import NetworkAdapter
    reg, br = _employee()
    payload = {"goal": {"columns": ["department", "v"], "grain": {"grouped_by": ["department"]},
                        "where": [], "derive": [{"name": "v", "expr": expr}]},
               "order": {"order_by": [], "limit": None}}
    return NetworkAdapter(name="network", client=_Client([_text_block(json.dumps(payload))]),
                          register=reg, briefing=br, model="m",
                          schema_path=DOMAIN / "schema.json").run("q", seed=0)


@needs_domain
@pytest.mark.parametrize("expr", ["median(annual_salary)", "median(annual_salary - bonus_target)"])
def test_network_refuses_median_at_the_contract_naming_it(expr):
    from evidence.eval_run.types import REFUSAL
    r = _network(expr)
    assert r["status"] == "refused" and r["result"] == REFUSAL
    assert r["error"].startswith(
        "ContractError(goal): derive 'v': function 'median' is not a supported aggregate"), r["error"]
    assert r["artifact"]["goal_valid"] is False
    assert "plan" not in r["artifact"]                      # refused before planning
    assert "Unreachable" not in r["error"]


@needs_domain
def test_network_declared_fn_still_answers():
    r = _network("avg(annual_salary)")
    assert r["status"] == "completed", r.get("error")


@needs_domain
def test_planner_refuses_median_typed_naming_it():
    from evidence.eval_run.adapters.planner_upfront import PlannerUpfrontAdapter
    reg, br = _employee()
    plan = {"plan": [{"op": "aggregate", "input": "register", "group_by": ["Department"],
                      "value_col": "Annual Salary", "agg": "median"}], "final_frame": "f0"}
    r = PlannerUpfrontAdapter(name="planner", client=_Client([_text_block(json.dumps(plan))]),
                              register=reg, briefing=br, model="m").run("q", seed=0)
    assert r["status"] == "refused"
    assert r["error"].startswith("step 0 ('aggregate'): AggregateTypeError: aggregate fn 'median'"), \
        r["error"]


@needs_domain
def test_react_receives_median_as_a_typed_observation_not_a_crash():
    from evidence.eval_run.adapters.react import ReactAdapter
    reg, br = _employee()
    client = _Client(
        [_tool_block("aggregate", {"input": "register", "group_by": ["Department"],
                                   "value_col": "Annual Salary", "agg": "median"})],
        [_text_block("I cannot answer this.")],
    )
    r = ReactAdapter(name="react", client=client, register=reg, briefing=br, model="m",
                     max_iters=4).run("q", seed=0)
    assert r["status"] == "refused", r.get("error")
    step = r["artifact"]["trace"][0]
    assert step["tool"] == "aggregate" and "'median'" in step["summary"]


# ── integrity ────────────────────────────────────────────────────────────────

def test_no_query_ids_in_the_contract():
    src = (ROOT / "gdc" / "contract.py").read_text()
    assert not re.search(r"\b(t[1-5]_\d{2}|oor_\d{2}|hv_\d{2})\b", src)
