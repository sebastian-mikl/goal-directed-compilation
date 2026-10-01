#!/usr/bin/env python
"""Decisions 2 and 7: risk/employee GDC and planner = REPLAY through the current engine.

For each risk/employee network and planner cell the paper scores, compute two
outcomes under the current comparator:
  RESCORE  = recorded raw_result rescored (the previous paper-numbers path)
  REPLAY   = recorded GOAL (network) or plan reply (planner) re-executed through
             the CURRENT deterministic engine (Fix 3 exact-columns,
             count_distinct, the group-by fix, ...) -> comparator
and report every cell whose class differs (the replay flip matrix), split into
wrong->correct and correct->wrong, never netted. The cells are the builder's
(scripts/paper_inputs.py: same files, temperature-0 overlays under
PARITY_TEMP0=1, tier of record), every seed. OFFLINE (no LLM/API).
"""
from __future__ import annotations
import sys
from pathlib import Path
from collections import Counter
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evidence.eval_run.comparator import score
from scripts import paper_inputs as pi


def cells(dom, sysname):
    """The builder's rows for one domain and system (risk T5 from the T5 files)."""
    if dom == "employee":
        return [r for r in pi.employee() if r["system"] == sysname]
    return ([r for r in pi.risk_off() if r["system"] == sysname and r["tier"] != pi.T5]
            + [r for r in pi.risk_t5() if r["system"] == sysname and r["tier"] == pi.T5])


def main():
    flips_wc = []   # wrong(rescore) -> correct(replay)
    flips_cw = []   # correct(rescore) -> wrong(replay)
    flips_other = []
    tallies = {}
    for dom in (("risk", "employee") if pi.HAS_RISK else ("employee",)):
        for sysname in pi.REPLAYED:
            rescore_ct, replay_ct = Counter(), Counter()
            n = 0
            for r in cells(dom, sysname):
                g = pi.GOLD[dom].get(r["qid"])
                if g is None:
                    continue
                n += 1
                rv, _, _ = score(r.get("raw_result"), g, status=r.get("status", "completed"))
                res, status = pi.scored_input(dom, r)
                pv, _, _ = score(res, g, status=status)
                rescore_ct[rv] += 1; replay_ct[pv] += 1
                if rv != pv:
                    rec = (dom, sysname, r["qid"], r.get("seed"), r["tier"], rv, pv)
                    if rv != "correct" and pv == "correct":
                        flips_wc.append(rec)
                    elif rv == "correct" and pv != "correct":
                        flips_cw.append(rec)
                    else:
                        flips_other.append(rec)
            tallies[(dom, sysname)] = (dict(rescore_ct), dict(replay_ct), n)

    for (dom, sysname), (rc, pc, n) in tallies.items():
        print(f"\n{dom} {sysname} ({n} cells):")
        print(f"  RESCORE (recorded raw_result -> comparator): {rc}")
        print(f"  REPLAY  (recorded emission -> engine -> comparator): {pc}")

    print("\n" + "=" * 70)
    print(f"FLIP MATRIX  wrong->correct: {len(flips_wc)}   correct->wrong: {len(flips_cw)}"
          f"   other: {len(flips_other)}")
    print("=" * 70)
    print("\n  wrong(rescore) -> correct(replay):")
    for dom, sysname, qid, seed, tier, rv, pv in sorted(flips_wc):
        print(f"    {dom:8} {sysname:8} {qid:8} s{seed} [{tier}]  {rv} -> {pv}")
    print("\n  correct(rescore) -> wrong(replay)  (every one scrutinized):")
    for dom, sysname, qid, seed, tier, rv, pv in sorted(flips_cw):
        print(f"    {dom:8} {sysname:8} {qid:8} s{seed} [{tier}]  {rv} -> {pv}")
    if flips_other:
        print("\n  other class change (wrong-kind -> different-wrong-kind):")
        for dom, sysname, qid, seed, tier, rv, pv in sorted(flips_other):
            print(f"    {dom:8} {sysname:8} {qid:8} s{seed} [{tier}]  {rv} -> {pv}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
