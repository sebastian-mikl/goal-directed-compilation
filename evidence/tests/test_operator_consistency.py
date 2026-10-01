"""Standing operator-consistency guarantee (brought to zero findings by the
P53-P58 fixes). Back-ported with
scripts/operator_consistency.py, trimmed to this tree's four operators.

Runs scripts/operator_consistency.py's check — every input the planner's _pre
accepts, dispatched through op_to_primitive and run exactly as execute_plan
runs it, classified against an independent oracle and against the State's own
shape claim; and the planned sequences end to end — and holds the RECORDED
state:

  * FINDINGS (property violations: an untyped crash, a silently different
    result, a frame whose shape differs from the State's claim, an unexplained
    over-refusal) — recorded as the empty set since P53-P58;
  * OBSERVATIONS (inside the property: typed refusals at execution for inputs
    _pre accepted, P60) — recorded by id.

Two things flip this test, both deliberately:

  * a NEW finding or observation id appears: a planner/executor disagreement
    was introduced — fix it or, if it is a decision, record it here with its
    P-number;
  * a recorded id DISAPPEARS: something was fixed — remove it from the set in
    the same change, so the fix is visible in this file's history.

Pure local, one ~6 s enumeration per session.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import operator_consistency as OC  # noqa: E402

# Property violations. Empty since the P53-P58 batch (2026-09-12): OC-1..OC-6 fixed.
RECORDED_FINDINGS: set[str] = set()

# Typed refusals at execution for inputs _pre accepted (P60, untouched).
#   - decidable before execution: the value's type for an ordered op, the empty
#     projection from the State;
#   - the S36 numeric-CONTENT criterion, which no declared type decides: a
#     str-typed column may hold numeric strings, so only the executor can tell;
#   - a numeric column compared with a non-number AFTER a compute overwrote it:
#     compute_eff types the overwritten column "unknown", so filter_pre leaves the
#     P54 type rule to the executor (the follow-up branch refuses a filter on an overwritten
#     column at plan time, P32; this tree has no P32).
# "compute:late-refusal:expression outside the grammar", recorded on the follow-up branch, is
# gone: compute_pre and the executor read one grammar (P15b, test_compute_grammar.py).
RECORDED_OBSERVATIONS = {
    "filter:late-refusal:ordered op with a non-numeric value",
    "select:late-refusal:empty projection",
    "filter:late-refusal:ordered op on a column with no numeric content",       # P55's refusal, content-based
    "compute:late-refusal:arithmetic on a column with no numeric content",      # P56's refusal, content-based
    "aggregate:late-refusal:numeric aggregate on a column with no numeric content",   # S36's refusal, content-based
    "filter:late-refusal:equality of a numeric column with a non-numeric value",      # overwritten column, no P32
    "filter:late-refusal:membership of a numeric column with a non-numeric value",    # overwritten column, no P32
}


_CACHE: dict = {}


def _run():
    """One enumeration per test session (~6 s); the tests read the same result."""
    if "r" not in _CACHE:
        tallies, findings = OC.check()
        _CACHE["r"] = (tallies, findings + OC.check_sequences())
    return _CACHE["r"]


def test_no_property_violation_across_the_five_operators():
    _, findings = _run()
    got = {f.id for f in findings if f.is_finding}
    new = got - RECORDED_FINDINGS
    fixed = RECORDED_FINDINGS - got
    assert not new, f"NEW planner/executor disagreement(s): {sorted(new)} — see scripts/operator_consistency.py --verbose"
    assert not fixed, f"recorded finding(s) no longer reproduce — fixed? record the fix by removing: {sorted(fixed)}"


def test_observations_are_exactly_the_recorded_set():
    _, findings = _run()
    got = {f.id for f in findings if not f.is_finding}
    assert got == RECORDED_OBSERVATIONS, {"new": sorted(got - RECORDED_OBSERVATIONS),
                                          "gone": sorted(RECORDED_OBSERVATIONS - got)}


def test_no_unexplained_over_refusal():
    """Every input _pre refuses that the executor handles correctly is refused by a
    named rule (P21, P22, P23, grain, idempotence, P54)."""
    tallies, _ = _run()
    unexplained = {k: v.get("refused/executor-OK-unexplained", 0) for k, v in tallies.items()}
    assert not any(unexplained.values()), unexplained


def test_the_compute_grammar_is_no_longer_a_late_refusal():
    """P15b: what compute_pre admits, the executor evaluates."""
    _, findings = _run()
    assert not [f.id for f in findings if f.kind == "compute" and "grammar" in f.id]


def test_no_untyped_crash_on_any_accepted_input():
    _, findings = _run()
    assert not [f.id for f in findings if f.category == "UNTYPED-CRASH"]


def test_every_operator_has_zero_findings():
    _, findings = _run()
    per_kind = {k: [f.id for f in findings if f.is_finding and f.kind == k] for k in OC.PARAMS}
    assert not any(per_kind.values()), per_kind
