#!/usr/bin/env python
"""Score the boundary-tier decline-affordance experiment and build the baseline
analogues of Appendix A.4 / A.5, compared against GDC. No API, pure scoring.

Reads the run output (results_boundary_affordance_{dom}.jsonl,
results_falsedecline_{dom}.jsonl). When the run output is absent it falls back to
the EXISTING recorded baseline boundary rows (results_spider_{dom}.jsonl) — the
pre-affordance, default-temperature baseline — clearly labeled as such, so this
doubles as the "before" table until the live run exists.

Scoring uses the same comparator s47 the paper uses. On these tiers score() encodes:
  out_of_scope_register  (gold expected_behavior=refuse, value=None):
      correct               = the system correctly DECLINED (returned refusal/empty)
      silent_wrong          = the system produced a confident answer (hallucination)
  out_of_contract_aggregate (gold expected_behavior=answer, REAL value):
      correct               = right answer (a general SQL/pandas system CAN do this)
      silent_wrong          = wrong answer
      refused_should_answer = the system DECLINED an answerable-per-gold query
        (defensible/"correct behavior" for the four-primitive systems planner/react,
         a FALSE under-answer for the general systems code_agent/text_to_sql — read
         per system; this is the flagged asymmetry, not a scoring bug).

GDC's A.4/A.5 numbers (from reports/paper_numbers.csv, temp 0) are printed alongside.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evidence.eval_run.comparator import score, COMPARATOR_VERSION      # noqa: E402
assert COMPARATOR_VERSION == "s48"

SPIDER = ["wta_1", "world_1", "bike_1"]
BASELINES = ["planner", "react", "code_agent", "text_to_sql"]
CONSTRAINED = {"planner", "react"}          # four-primitive: decline on out_of_contract is defensible
GENERAL = {"code_agent", "text_to_sql"}     # full SQL/pandas: out_of_contract is answerable
OOSR = "out_of_scope_register"
OOCA = "out_of_contract_aggregate"
GOLD = {d: json.loads((ROOT / "evidence/gold" / f"gold_spider_{d}.json").read_text()) for d in SPIDER}


def _load(fn):
    p = ROOT / "results" / fn
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _rows(dom, prefix, fallback):
    """Prefer the affordance-run output; fall back to recorded spider rows."""
    real = _load(f"{prefix}_{dom}.jsonl")
    if real:
        return real, f"{prefix}_{dom}.jsonl (affordance run)"
    return _load(fallback), f"{fallback} (RECORDED pre-affordance — placeholder)"


def outcomes(dom, rows, tier, systems):
    """{system: Counter(outcome)} for one tier."""
    out = {s: Counter() for s in systems}
    for r in rows:
        s = r.get("system")
        if s not in out:
            continue
        g = GOLD[dom].get(r["qid"])
        if g is None or g.get("tier") != tier:
            continue
        oc = score(r.get("raw_result"), g, status=r.get("status", "completed"))[0]
        out[s][oc] += 1
    return out


def pct(k, n):
    return f"{100*k/n:4.1f}%" if n else "  -  "


def main():
    using_real = (ROOT / f"results/results_boundary_affordance_{SPIDER[0]}.jsonl").exists()
    print("=" * 96)
    print("BASELINE BOUNDARY-TIER BEHAVIOR WITH DECLINE AFFORDANCE — analogue of Appendix A.4 / A.5")
    print(f"  source: {'affordance run output' if using_real else 'RECORDED pre-affordance rows (PLACEHOLDER — live run pending)'}")
    print("=" * 96)

    # pooled across domains, per system, per tier
    for tier in (OOSR, OOCA):
        pooled = defaultdict(Counter)
        for d in SPIDER:
            rows, _ = _rows(d, "results_boundary_affordance", f"results_spider_{d}.jsonl")
            oc = outcomes(d, rows, tier, BASELINES)
            for s in BASELINES:
                pooled[s] += oc[s]
        print(f"\n### {tier} (pooled over wta_1/world_1/bike_1) ###")
        if tier == OOSR:
            print("  a correct DECLINE is the target for all four (data genuinely absent).")
            print(f"  {'system':<12}{'declined(correct)':>18}{'answered-wrong(silent)':>24}{'crash/timeout':>16}")
            for s in BASELINES:
                c = pooled[s]
                n = sum(c.values())
                dec = c.get('correct', 0)
                sil = c.get('silent_wrong', 0)
                ct = c.get('crash', 0) + c.get('timeout', 0)
                print(f"  {s:<12}{f'{dec}/{n} ({pct(dec,n)})':>18}{f'{sil} ({pct(sil,n)})':>24}{ct:>16}")
            print("  GDC (A.4, temp 0): correct-decline pooled 34.3% (mechanism off) / 51.7% (on).")
        else:
            print("  answerable by full SQL/pandas; a DECLINE is correct-behavior for planner/react (4-primitive),")
            print("  a FALSE under-answer for code_agent/text_to_sql (general). Read per system.")
            print(f"  {'system':<12}{'correct(answer)':>16}{'silent(wrong)':>15}{'declined':>10}{'crash/timeout':>16}   note")
            for s in BASELINES:
                c = pooled[s]
                n = sum(c.values())
                cor = c.get('correct', 0)
                sil = c.get('silent_wrong', 0)
                dec = c.get('refused_should_answer', 0)
                ct = c.get('crash', 0) + c.get('timeout', 0)
                note = "decline=defensible" if s in CONSTRAINED else "decline=FALSE under-answer"
                print(f"  {s:<12}{f'{cor} ({pct(cor,n)})':>16}{f'{sil} ({pct(sil,n)})':>15}{f'{dec}':>10}{ct:>16}   {note}")
            print("  GDC (A.5, temp 0): 0% correct by design; refuses structurally (world_1 100%, bike_1 75%, wta_1 33%).")

    # false-decline check
    print("\n" + "=" * 96)
    print("FALSE-DECLINE CHECK — decline rate on the IN-CONTRACT answer tier (should be ~0)")
    print("=" * 96)
    print("  react/planner: from the affordance run; code_agent/text_to_sql: from the temp-0 parity in-contract data")
    print("  (their affordance is always on). A high decline rate here = the affordance broke in-contract answering.")
    fd_sys = ["planner", "react"]
    pooled = defaultdict(Counter)
    for d in SPIDER:
        rows = _load(f"results_falsedecline_{d}.jsonl")
        src = "affordance run" if rows else "NO DATA (run pending)"
        gold = GOLD[d]
        for r in rows:
            s = r.get("system")
            if s not in fd_sys:
                continue
            g = gold.get(r["qid"])
            if g is None or g.get("tier") in (OOSR, OOCA):
                continue
            oc = score(r.get("raw_result"), g, status=r.get("status", "completed"))[0]
            pooled[s][oc] += 1
    for s in fd_sys:
        c = pooled[s]
        n = sum(c.values())
        dec = c.get('refused_should_answer', 0)
        print(f"  {s:<12} in-contract cells={n:>3}  wrongly-declined={dec} ({pct(dec,n)})"
              + ("" if n else "   <- run pending"))
    print("  (code_agent / text_to_sql in-contract decline rate is already ~0 in the parity data — high accuracy.)")
    print("=" * 96)
    if not using_real:
        print("\nNOTE: numbers above are the RECORDED pre-affordance placeholder. Re-run after")
        print("      python scripts/run_boundary_affordance.py produces the affordance output.")


if __name__ == "__main__":
    sys.exit(main() or 0)
