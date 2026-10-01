"""# regression: the goal-emission prompt once over-declared scalar max/min
# queries ("highest Cost recorded?") as a raw row-grain column coexisting
# with a global aggregate, which the contract correctly refuses as
# Unreachable — a confident query silently failing to answer at all.

Scalar max/min over-declaration fix (network goal-emission prompt only).
sum/avg/count emit cleanly; only max/min over-declared ("highest X" was read
as argmax). Emission bug, not a capability limit (the engine does global max
correctly once the goal is well-formed).

The fix is prompt-only. These deterministic tests guard the ENGINE paths the
prompt steers toward, plus the prompt-text integrity.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

import pytest

# Risk-domain source data is withheld from the public release; these tests skip
# cleanly rather than fail when it is absent.
_RISK_DATA = [ROOT / "environment" / "register.pkl",
              ROOT / "eval_corpus.json",
              ROOT / "evidence" / "gold" / "gold.json"]
pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in _RISK_DATA),
    reason="risk domain data not included in this release")



def _base():
    from gdc.integrate import aliased_view
    reg = pd.read_pickle(ROOT / "environment" / "register.pkl")
    amap, adf, base = aliased_view(reg)
    return reg, adf, base


# ── prompt-text fix (red-first) ──────────────────────────────────────────────

def test_S31_prompt_treats_scalar_max_min_as_aggregates_not_argmax():
    """The generalized instruction must: name it a SCALAR aggregate, forbid listing the
    underlying RAW column alongside it, and keep the row-SELECTION (top-N) distinction."""
    from gdc.llm import GOAL_ORDER_INSTRUCTION
    t = GOAL_ORDER_INSTRUCTION.lower()
    assert "scalar aggregate" in t
    assert ("underlying" in t) or ("raw" in t)          # do not also list the raw column
    assert "row selection" in t                          # "N entries with highest X" is selection
    assert "do not" in t or "don't" in t


def test_S31_prompt_has_no_eval_ids_or_gold_values():
    """Integrity: the instruction is authored from engine semantics — no query ids, no
    answer values baked in."""
    from gdc.llm import GOAL_ORDER_INSTRUCTION
    t = GOAL_ORDER_INSTRUCTION
    assert not re.search(r"\b(t[1-4]_\d{2}|oor_\d{2}|hv_\d{2})\b", t), "prompt names eval query ids"
    for val in ("4836800", "211846", "300"):             # max Cost / mean / row count
        assert val not in t, f"prompt bakes in a gold value: {val}"


def test_S31_goal_prompt_version_bumped():
    """Pins the CURRENT prompt version so any prompt-text edit that forgets the
    bump fails here (the bump is what makes the provenance drift guard fire on
    resume). s31: scalar-max rules; s43: Fix 1 order-closure instruction;
    s44: Finding 7 implicit-ranking instruction; s45: Fix 2 count_distinct;
    s48: staged-filter + bare-top-N-direction rules, on top of the s47 term pin."""
    from gdc.llm import GOAL_PROMPT_VERSION
    assert GOAL_PROMPT_VERSION == "s48"


# ── engine-path regression guards (construct goals directly, no LLM) ─────────

def test_S31_clean_scalar_max_plans_and_executes_to_column_max():
    """(1) A clean scalar-max goal — one derived aggregate field, global grain — PLANS
    and executes to the column max."""
    from gdc.state import State, Goal
    from gdc.search import plan, Unreachable
    from gdc.integrate import execute_plan
    reg, adf, base = _base()
    g = Goal.make(columns=[("max_cost", "unknown")], grain={"grouped_by": []}, where=[],
                  derive=[{"name": "max_cost", "expr": "max(cost)"}])
    p = plan(State.from_base(base), g)
    assert not isinstance(p, Unreachable), getattr(p, "reason", "")
    assert int(execute_plan(p, adf).iat[0, 0]) == int(reg["Cost"].max())
    # min too
    gmin = Goal.make(columns=[("min_cost", "unknown")], grain={"grouped_by": []}, where=[],
                     derive=[{"name": "min_cost", "expr": "min(cost)"}])
    pmin = plan(State.from_base(base), gmin)
    assert not isinstance(pmin, Unreachable)
    assert int(execute_plan(pmin, adf).iat[0, 0]) == int(reg["Cost"].min())


def test_S31_overdeclared_raw_column_plus_global_max_still_refuses():
    """(2) The OVER-declared goal — raw `cost` column alongside the global max — STILL
    refuses as Unreachable. The fix is emission-side; the contract is NOT loosened."""
    from gdc.state import State, Goal
    from gdc.contract import validate_goal
    from gdc.search import plan, Unreachable
    _, _, base = _base()
    g = Goal.make(columns=[("cost", "unknown")], grain={"grouped_by": []}, where=[],
                  derive=[{"name": "max_cost", "expr": "max(cost)"}])
    assert validate_goal(g).ok                                   # passes the contract...
    assert isinstance(plan(State.from_base(base), g), Unreachable)  # ...but is unplannable


def test_S31_topk_highest_plans_as_ordered_rows_not_scalar():
    """(3) "the N entries with the highest cost" is row SELECTION — row columns +
    order_by cost desc + limit N — plans as an ordered row list, not a scalar."""
    from gdc.state import State, Goal
    from gdc.order import OrderSpec
    from gdc.search import plan, Unreachable
    from gdc.integrate import execute_plan, apply_order_via_executor
    _, adf, base = _base()
    g = Goal.make(columns=[("title", "unknown"), ("cost", "unknown")], grain="row",
                  where=[], derive=[])
    p = plan(State.from_base(base), g)
    assert not isinstance(p, Unreachable), getattr(p, "reason", "")
    o = OrderSpec.make(order_by=[{"col": "cost", "dir": "desc"}], limit=5)
    frame = apply_order_via_executor(execute_plan(p, adf), o)
    assert len(frame) == 5                                       # ordered rows, NOT collapsed to 1
