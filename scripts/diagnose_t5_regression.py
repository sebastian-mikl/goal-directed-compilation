#!/usr/bin/env python
"""Diagnose the T5 regression seen in the post-Fix-1 T4/T5 Haiku re-run.

Context: results_haiku_post_fix1.jsonl (produced by
scripts/rerun_t4_t5_haiku_post_fix1.py) showed T4 going 65.3% -> 100% accuracy
with 0 regressions, but T5_high_complexity moving 89.3% -> 86.7%, with
silent_wrong dropping 2.7% -> 0.0% and the comparison table reporting 0
flips-to-ok and 3 regressions (correct -> not-correct). Those summary counters
cannot by themselves say which rows moved where; this script finds the exact
(qid, seed) rows behind them.

For the "network" adapter specifically (evidence/eval_run/adapters/network.py),
every non-crash "didn't produce a value" path is a STRUCTURAL pre-execution
refusal: ContractError(goal/order), the S35 dead-derive goal_lint, an
Unreachable plan, a FilterTypeError, or an explicit EmissionDecline/ParseError
-- never a wrong VALUE. Since T5's silent_wrong count went to exactly 0, the
regression is most likely rows that flipped from an answer (right or wrong)
into one of those structural refusal gates rather than new wrong-value answers:
Fix 1's longer RULES block plausibly shifted goal construction on some of T5's
multi-rule/multi-derive queries just enough to trip a gate that did not fire
before. This script confirms or refutes that by printing the actual reason each
row's status changed.

Pure local JSONL diffing -- no API calls, reuses find_canonical_before_file /
load_before_rows / load_after_rows from rerun_t4_t5_haiku_post_fix1.py rather
than re-deriving the before/after loading logic.

Run this AFTER you have results_haiku_post_fix1.jsonl from the live re-run:

    python scripts/diagnose_t5_regression.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.rerun_t4_t5_haiku_post_fix1 import (
    find_canonical_before_file,
    load_before_rows,
    load_after_rows,
    OUT_RESULTS_PATH,
)

TIER = "T5_high_complexity"

# artifact keys that name exactly which structural gate fired, in the order
# network.py's run() can set them (see adapters/network.py) -- printed
# whenever present and non-empty/non-boolean.
ARTIFACT_REASON_KEYS = (
    "goal_reason", "order_reason", "goal_lint_reason",
    "unreachable_reason", "filter_type_error", "decline_reason",
)


def _truncate(x, n=140) -> str:
    s = repr(x) if not isinstance(x, str) else x
    return s if len(s) <= n else s[: n - 3] + "..."


def describe(r: dict | None) -> str:
    """One-line status summary: outcome_class + status + the specific
    structural-gate reason (for network) or comparator_reason (for a value
    mismatch), whichever applies."""
    if r is None:
        return "(missing from this file)"
    bits = [f"outcome_class={r['outcome_class']}", f"status={r['status']}"]
    art = r.get("artifact") or {}
    fired = [f"{k}={_truncate(art[k])}" for k in ARTIFACT_REASON_KEYS
             if art.get(k) not in (None, "", True, False)]
    if fired:
        bits.extend(fired)
    elif r.get("error"):
        bits.append(f"error={_truncate(r['error'])}")
    if r["outcome_class"] == "silent_wrong":
        bits.append(f"comparator_reason={_truncate(r.get('comparator_reason'))}")
        bits.append(f"failure_tag={r.get('failure_tag')}")
    return "  ".join(bits)


def main() -> int:
    if not OUT_RESULTS_PATH.exists():
        print(f"ERROR: {OUT_RESULTS_PATH} not found in this environment.")
        print("This script diffs the live post-Fix-1 re-run's own output against the")
        print("canonical before-file -- it needs results_haiku_post_fix1.jsonl, produced by")
        print("running scripts/rerun_t4_t5_haiku_post_fix1.py (needs ANTHROPIC_API_KEY).")
        print("Run this script in the same environment/checkout where that file landed.")
        return 2

    before_path = find_canonical_before_file()
    before = load_before_rows(before_path)
    after = load_after_rows(OUT_RESULTS_PATH)
    print()

    t5_before = {k: v for k, v in before.items() if v["tier"] == TIER}
    changed = []
    for (qid, seed), old_r in sorted(t5_before.items()):
        new_r = after.get((qid, seed))
        if new_r is not None and old_r["outcome_class"] != new_r["outcome_class"]:
            changed.append((qid, seed, old_r, new_r))

    n_regressed = sum(1 for *_, o, n in changed if o["outcome_class"] == "correct" and n["outcome_class"] != "correct")
    n_flipped_in = sum(1 for *_ , o, n in changed if n["outcome_class"] == "correct" and o["outcome_class"] != "correct")
    print(f"{TIER}: {len(changed)} rows changed outcome_class out of {len(t5_before)} total "
          f"({n_regressed} regressed away from correct, {n_flipped_in} flipped into correct).\n")

    for qid, seed, old_r, new_r in changed:
        if old_r["outcome_class"] == "correct":
            direction = "REGRESSED (was correct)"
        elif new_r["outcome_class"] == "correct":
            direction = "FLIPPED TO CORRECT"
        else:
            direction = "SHIFTED (non-correct -> different non-correct)"
        print(f"[{direction}] {qid} seed={seed}  (complexity_ops={old_r.get('complexity_ops')})")
        print(f"    query : {old_r['query']}")
        print(f"    before: {describe(old_r)}")
        print(f"    after : {describe(new_r)}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
