"""How the typed refusal family reaches each consumer, after the series review.

- react: the FilterTypeError root (the filter's type guard, a comparison the
  register cannot make) keeps the observation react always saw for it, "This
  comparison is not expressible over the register." Every other member (an absent
  column, a compute, an aggregate, an undeclared function, an absent sort key) is
  named with its class and message, and the agent is told to fix the call or answer
  that it cannot be computed. Was: every member ended with the comparison sentence,
  so a misspelt column read as an inexpressible request.
- prim_aggregate's refusal no longer says the planner spellings `avg` and
  `count_distinct` "map onto" the supported set: on the tools path nothing maps them.
- planner_upfront: a reply that parses but is not the plan shape (a step that is not
  an object, a plan that is not a list, a reply that is not an object) is a typed
  refusal. Was: AttributeError out of run(), recorded as a crash by the harness.
- ablate_checks: only the FilterTypeError root is the type guard; its subclasses are
  reported under their own class and are not ablated.

Synthetic frames and the public employee domain. No corpus query ids or text.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from gdc.primitives import (AggregateTypeError, ComputeExprError, FilterColumnError,
                            FilterTypeError, ProjectColumnError, SortColumnError,
                            prim_aggregate)

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "evaluation" / "domains" / "employee"
needs_domain = pytest.mark.skipif(
    not (DOMAIN / "employees.pkl").exists(), reason="employee domain data absent")
COMPARISON = "This comparison is not expressible over the register."
NEUTRAL = "Fix the call, or answer that it cannot be computed."


# ── react: the observation text ──────────────────────────────────────────────

def test_the_type_guard_keeps_the_comparison_sentence():
    from evidence.eval_run.adapters.react import typed_refusal_observation
    obs = typed_refusal_observation("filter", FilterTypeError("filter 'x' > 'abc': value is not numeric"))
    assert obs.endswith(COMPARISON) and NEUTRAL not in obs


@pytest.mark.parametrize("cls", [FilterColumnError, ProjectColumnError, ComputeExprError,
                                 AggregateTypeError, SortColumnError])
def test_every_other_family_member_is_named_and_neutral(cls):
    from evidence.eval_run.adapters.react import typed_refusal_observation
    obs = typed_refusal_observation("select", cls("column(s) ['Salary'] are not in the frame."))
    assert COMPARISON not in obs
    assert obs == (f"cannot apply select: {cls.__name__}: column(s) ['Salary'] are not in "
                   f"the frame. {NEUTRAL}")


def test_the_aggregate_refusal_does_not_claim_the_planner_spellings_map():
    with pytest.raises(AggregateTypeError) as ei:
        prim_aggregate(pd.DataFrame({"g": ["a"], "x": [1]}),
                       {"group_by": "g", "value_col": "x", "agg": "avg"})
    msg = str(ei.value)
    assert "'avg' is not implemented by the executor" in msg
    assert "map onto" not in msg and "planner spellings" not in msg


def _text_block(t):
    return type("T", (), {"type": "text", "text": t})()


def _tool_block(name, inp, tid="t1"):
    return type("U", (), {"type": "tool_use", "name": name, "input": inp, "id": tid})()


class _Client:
    """Canned responses, one per messages.create call; records each call's kwargs."""
    def __init__(self, *responses):
        q = list(responses)
        self.calls = calls = []

        class _Msgs:
            def create(self, **kw):
                calls.append(kw)
                blocks = q.pop(0) if q else [_text_block("I cannot answer this.")]
                return type("R", (), {"content": blocks, "usage": None})()
        self.messages = _Msgs()


def _employee():
    from evidence.eval_run.briefing import load_briefing
    return (pd.read_pickle(DOMAIN / "employees.pkl"),
            load_briefing(DOMAIN / "schema.json", DOMAIN / "employees.pkl"))


@needs_domain
@pytest.mark.parametrize("tool, args, cls, sentence", [
    ("select", {"input": "register", "columns": ["Salary"]}, "ProjectColumnError", NEUTRAL),
    ("order", {"input": "register", "order_by": [{"col": "Salary", "dir": "desc"}], "limit": 3},
     "SortColumnError", NEUTRAL),
    ("aggregate", {"input": "register", "group_by": ["Department"], "value_col": "Annual Salary",
                   "agg": "avg"}, "AggregateTypeError", NEUTRAL),
    ("filter", {"input": "register", "predicates": [{"col": "Annual Salary", "op": "==",
                                                     "value": "high"}]}, None, COMPARISON),
])
def test_react_sees_the_observation_end_to_end(tool, args, cls, sentence):
    from evidence.eval_run.adapters.react import ReactAdapter
    reg, br = _employee()
    client = _Client([_tool_block(tool, args)], [_text_block("I cannot answer this.")])
    r = ReactAdapter(name="react", client=client, register=reg, briefing=br, model="m",
                     max_iters=4).run("q", seed=0)
    assert r["artifact"]["trace"][0]["tool"] == tool
    seen = next(c["content"] for m in client.calls[-1]["messages"] if m["role"] == "user"
                and isinstance(m["content"], list) for c in m["content"]
                if isinstance(c, dict) and c.get("type") == "tool_result")   # as sent
    assert seen.endswith(sentence), seen
    assert (f"cannot apply {tool}: {cls}: " in seen) if cls else (NEUTRAL not in seen)


# ── planner_upfront: a malformed plan is a typed refusal ─────────────────────

@needs_domain
@pytest.mark.parametrize("reply, why", [
    ('{"plan": ["filter"], "final_frame": "f0"}', "step 0: plan step is str, not an object"),
    ('{"plan": [{"op": "select", "input": "register", "columns": ["Name"]}, 7]}',
     "step 1: plan step is int, not an object"),
    ('{"plan": {"op": "filter"}, "final_frame": "f0"}', "'plan' is dict, not a list of steps"),
])
def test_planner_malformed_plan_is_a_typed_refusal_not_a_crash(reply, why):
    from evidence.eval_run.adapters.planner_upfront import PlannerUpfrontAdapter
    from evidence.eval_run.types import REFUSAL
    reg, br = _employee()
    r = PlannerUpfrontAdapter(name="planner", client=_Client([_text_block(reply)]),
                              register=reg, briefing=br, model="m").run("q", seed=0)
    assert r["status"] == "refused" and r["result"] == REFUSAL
    assert r["error"] == f"malformed plan: {why}"


# ── ablate_checks: only the root is the type guard ───────────────────────────

def _ctx():
    from gdc.integrate import aliased_view
    reg = pd.DataFrame({"t": ["p", "q", "r"], "v": [1.0, 2.0, 3.0]})
    _amap, adf, schema = aliased_view(reg)
    amap_obj = SimpleNamespace(aliases={}, to_real=lambda c: c)
    return SimpleNamespace(base_schema=schema, adf=adf, amap_obj=amap_obj)


def test_ablating_the_type_guard_relabels_only_the_type_guard():
    import scripts.ablate_checks as A
    from gdc.order import OrderSpec
    from gdc.state import Goal
    order = OrderSpec.make(order_by=[], limit=None)
    guard = Goal.make(columns=["t"], grain="row", where=[{"col": "t", "op": ">", "val": 1}],
                      derive=[])                                   # P55: plain FilterTypeError
    compute = Goal.make(columns=["h"], grain="row", where=[],
                        derive=[{"name": "h", "expr": "t"}])       # P56: ComputeExprError
    assert A.run_det(_ctx(), guard, order, "baseline")[2] == "filter_type"
    assert A.run_det(_ctx(), guard, order, "no_type_guard") == ("crashed", A.REFUSAL,
                                                                "crash_from_filter_type")
    for variant in ("baseline", "no_type_guard"):
        assert A.run_det(_ctx(), compute, order, variant) == ("refused", A.REFUSAL,
                                                              "typed:ComputeExprError")
