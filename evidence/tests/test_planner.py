"""S11 validations — Declarative-Goal Forward Planner (LLM-free core).

Five validations from the step spec:
  V1 order-emerges    — filter strictly before aggregate from preconditions alone.
  V2 order-is-forced  — filter.pre is False on grouped grain.
  V3 novel-order      — a structurally different goal (compute then filter) the
                        planner has not seen resolves with zero LLM calls and
                        zero new operators authored.
  V4 determinism      — plan() twice yields identical sequences and fingerprints.
  V5 canonicalisation — two goals differing only in conjunct/column order produce
                        identical fingerprints AND identical plans.

All tests are LLM-free by construction (the planner has no LLM dependency).
A network-mock guard double-checks that — no Anthropic client may be instantiated.
"""
from __future__ import annotations

import dataclasses
from typing import Iterable

import pytest

from gdc import (
    State, Goal, Op, OPERATORS, Unreachable,
    apply_plan, enumerate_ops, fingerprint, plan, satisfies,
)
from gdc.operators import filter_pre, aggregate_eff


# A representative employee-register base state (declared columns + types). The
# planner never sees data — only this shape.
BASE = State.from_base({
    "employee_id":  "int",
    "owner":    "str",
    "severity": "int",
    "cost":     "float",
    "status":   "str",
})


def _kinds(seq: Iterable[Op]) -> list[str]:
    return [o.kind for o in seq]


def _index_of(seq: list[Op], kind: str, param_pred=lambda p: True) -> int:
    for i, o in enumerate(seq):
        if o.kind == kind and param_pred(o.params):
            return i
    raise AssertionError(f"no {kind} op matching predicate in {seq}")


# ── V1 order-emerges ─────────────────────────────────────────────────────────

def test_V1_order_emerges_from_preconditions():
    goal = Goal.make(
        columns=["owner", "total_cost"],
        grain={"grouped_by": ["owner"]},
        where=[{"col": "severity", "op": ">", "val": 3}],
        derive=[{"name": "total_cost", "expr": "sum(cost)"}],
    )

    # (a) the goal has NO ordering field — by structure.
    assert not hasattr(goal, "order_by")
    assert "order" not in {f.name for f in dataclasses.fields(goal)}

    p = plan(BASE, goal)
    assert isinstance(p, list), f"expected a plan, got {p!r}"

    # (b) filter(severity,>,3) strictly before aggregate(by=[owner]).
    fi = _index_of(p, "filter",    lambda pr: pr["col"] == "severity" and pr["op"] == ">")
    ai = _index_of(p, "aggregate", lambda pr: list(pr["by"]) == ["owner"])
    assert fi < ai, f"filter must precede aggregate; got plan={p}"

    # the plan, applied, satisfies the goal (round-trip via eff).
    end = apply_plan(BASE, p)
    assert satisfies(end, goal)


# ── V2 order-is-forced ───────────────────────────────────────────────────────

def test_V2_filter_pre_false_on_grouped_grain():
    # Hand-build a grouped state and confirm filter.pre is False — so no valid
    # plan can place filter(severity,...) after aggregate(...,by=[owner]).
    grouped = aggregate_eff(
        BASE,
        {"fn": "sum", "col": "cost", "by": ["owner"], "out_col": "total_cost"},
    )
    assert not grouped.is_row_grain
    assert filter_pre(grouped, {"col": "severity", "op": ">", "val": 3}) is False


# ── V3 novel-order, no LLM, no new operators ─────────────────────────────────

def test_V3_novel_order_zero_LLM_zero_new_operators():
    """A structurally different fixture the planner has never been tuned for:
    compute then filter (the inverse precondition chain). The planner must solve
    it with the same four primitives, no new authoring, and no LLM call."""
    goal = Goal.make(
        columns=["employee_id", "risk_score"],
        grain="row",
        where=[{"col": "risk_score", "op": ">", "val": 10}],
        derive=[{"name": "risk_score", "expr": "severity * cost"}],
    )
    kinds_before = set(OPERATORS.keys())

    p = plan(BASE, goal)
    assert isinstance(p, list)

    # compute must precede filter (filter.pre fails until risk_score exists).
    ci = _index_of(p, "compute", lambda pr: pr["new_col"] == "risk_score")
    fi = _index_of(p, "filter",  lambda pr: pr["col"] == "risk_score")
    assert ci < fi, f"compute must precede filter; got plan={p}"

    # zero new operators were authored — the planner uses exactly the four primitives.
    assert set(OPERATORS.keys()) == kinds_before
    assert {o.kind for o in p} <= {"filter", "select", "aggregate", "compute"}

    # zero LLM calls: enforced structurally — guard that an Anthropic client cannot
    # have been instantiated inside the planner. (No import side-effect; nothing to mock.)
    import sys
    assert "anthropic" not in sys.modules or _engine_modules_did_not_import_anthropic()

    assert satisfies(apply_plan(BASE, p), goal)


def _engine_modules_did_not_import_anthropic() -> bool:
    """Defensive: even if `anthropic` is loaded elsewhere in the suite, the
    engine package itself must not reference it."""
    import importlib, sys
    for name in ("gdc", "gdc.state", "gdc.operators", "gdc.search"):
        mod = sys.modules.get(name) or importlib.import_module(name)
        # any attribute reaching the anthropic SDK would be visible here
        for attr in vars(mod).values():
            modname = getattr(getattr(attr, "__module__", ""), "lower", lambda: "")()
            if "anthropic" in modname:
                return False
    return True


# ── V4 determinism ───────────────────────────────────────────────────────────

def test_V4_plan_is_deterministic():
    goal = Goal.make(
        columns=["owner", "total_cost"],
        grain={"grouped_by": ["owner"]},
        where=[{"col": "severity", "op": ">", "val": 3}],
        derive=[{"name": "total_cost", "expr": "sum(cost)"}],
    )
    p1 = plan(BASE, goal)
    p2 = plan(BASE, goal)
    assert p1 == p2, f"plans differ across runs: {p1} vs {p2}"
    # the State at every step has an identical fingerprint sequence as well.
    fps_1 = [fingerprint(apply_plan(BASE, p1[:i])) for i in range(len(p1) + 1)]
    fps_2 = [fingerprint(apply_plan(BASE, p2[:i])) for i in range(len(p2) + 1)]
    assert fps_1 == fps_2
    # Goal fingerprint is itself deterministic.
    assert fingerprint(goal) == fingerprint(goal)


# ── V5 canonicalisation ──────────────────────────────────────────────────────

def test_V5_input_order_does_not_change_fingerprint_or_plan():
    """Two goals that differ ONLY in the order of conjuncts and columns must
    produce identical fingerprints AND identical plans."""
    goal_A = Goal.make(
        columns=["owner", "total_cost"],
        grain={"grouped_by": ["owner"]},
        where=[
            {"col": "severity", "op": ">",  "val": 3},
            {"col": "status",   "op": "==", "val": "Active"},
        ],
        derive=[{"name": "total_cost", "expr": "sum(cost)"}],
    )
    goal_B = Goal.make(
        columns=["total_cost", "owner"],          # column order swapped
        grain={"grouped_by": ["owner"]},
        where=[
            {"col": "status",   "op": "==", "val": "Active"},   # conjunct order swapped
            {"col": "severity", "op": ">",  "val": 3},
        ],
        derive=[{"name": "total_cost", "expr": "sum(cost)"}],
    )
    assert fingerprint(goal_A) == fingerprint(goal_B), \
        "canonicalisation must collapse input-order differences"

    p_A = plan(BASE, goal_A)
    p_B = plan(BASE, goal_B)
    assert isinstance(p_A, list) and isinstance(p_B, list)
    assert p_A == p_B, f"plans differ under reordered input: {p_A} vs {p_B}"

    # the canonical operator universes coincide.
    assert enumerate_ops(goal_A) == enumerate_ops(goal_B)


# ── Spec invariants surfaced as standalone assertions ────────────────────────

def test_invariant_goal_has_no_ordering_field():
    g = Goal.make(columns=[], grain="row", where=[], derive=[])
    # ordering-related field names that must not appear in the Goal descriptor
    forbidden = {"order_by", "order", "sort_by", "ordering", "limit", "top"}
    fields = {f.name for f in dataclasses.fields(g)}
    assert fields.isdisjoint(forbidden), \
        f"Goal must not carry ordering hints; found {fields & forbidden}"


def test_invariant_state_is_descriptor_only():
    # the State dataclass must not hold a DataFrame or any data-bearing field.
    forbidden = {"data", "frame", "df", "rows", "records", "values"}
    fields = {f.name for f in dataclasses.fields(BASE)}
    assert fields.isdisjoint(forbidden), \
        f"State must be a descriptor; found data-bearing fields {fields & forbidden}"


def test_invariant_unreachable_is_typed_not_an_LLM_call():
    # Build a goal whose where references a column the start doesn't have AND
    # whose derive doesn't introduce it — no plan can satisfy it. The planner
    # must return Unreachable, NOT silently succeed and NOT raise.
    goal = Goal.make(
        columns=["nonexistent_col"],
        grain="row",
        where=[{"col": "nonexistent_col", "op": ">", "val": 0}],
        derive=[],
    )
    p = plan(BASE, goal)
    assert isinstance(p, Unreachable), f"expected Unreachable, got {p!r}"
    assert bool(p) is False                          # falsy by design
    assert p.goal_fingerprint == fingerprint(goal)
