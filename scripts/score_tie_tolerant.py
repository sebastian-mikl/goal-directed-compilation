#!/usr/bin/env python
"""Tie-tolerant scoring (version s47t): s47 + data-verified ranking ties.

RULE (narrow, versioned): a produced answer scores `correct` iff s47 scores it
correct against gold OR against ANY data-verified tied alternative for that qid.
"Tied alternative" = a row that sits at the SAME ranking boundary as gold in the
underlying data (from reports/tie_alternatives.json, computed by detect_ties.py
directly from the register — never inferred from an answer looking plausible).

Conservative by construction: for a non-tie query the alternative set is empty,
so s47t == s47 exactly; a genuinely wrong answer to a non-tied query still scores
wrong. Only world_13 and bike_06 have >1 tied row (the complete set the data
yields). Everything downstream reports BOTH numbers; strict s47 stays primary.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evidence.eval_run.comparator import score, COMPARATOR_VERSION  # noqa: E402

SCORER_VERSION = "s48t"
_TIES = json.loads((ROOT / "reports" / "tie_alternatives.json").read_text())

def tie_alt_values(qid: str):
    info = _TIES.get(qid)
    return list(info["alt_values"]) if info else []

def score_tt(produced, gold_record: dict, qid: str, status: str = "completed"):
    """Return (outcome, reason, tie_used: bool). Strict s47 first; on a non-correct
    ranked answer, retry against each data-verified tied alternative gold value."""
    v, reason, _ = score(produced, gold_record, status=status)
    if v == "correct":
        return v, reason, False
    for alt in tie_alt_values(qid):
        alt_record = {**gold_record, "value": alt}
        av, _, _ = score(produced, alt_record, status=status)
        if av == "correct":
            return "correct", f"tie-tolerant (s47t): matched data-verified tied row; strict={v}", True
    return v, reason, False


# ── self-test: adversarial check + demonstration ────────────────────────────
def _adversarial():
    assert COMPARATOR_VERSION == "s48", COMPARATOR_VERSION
    import pandas as pd
    GOLD = {d: json.loads((ROOT / "evidence/gold" / f"gold_spider_{d}.json").read_text())
            for d in ["world_1", "bike_1"]}
    print(f"tie-tolerant scorer {SCORER_VERSION} over comparator {COMPARATOR_VERSION}")
    print(f"tie-affected qids (from data): {sorted(_TIES)}\n")

    fails = []
    # (1) every tied ALTERNATIVE must score correct under s47t (they are valid answers)
    for qid, info in _TIES.items():
        d = info["domain"]; grec = GOLD[d][qid]
        for alt in info["alt_values"]:
            v, _, used = score_tt(alt, grec, qid)
            tag = "correct" if v == "correct" else f"** {v} **"
            if v != "correct":
                fails.append(f"{qid}: tied alt {json.dumps(alt)[:50]} scored {v}, expected correct")
        print(f"(1) {qid}: all {info['multiplicity']} data-tied rows score correct under s47t  "
              f"[{'OK' if all(score_tt(a,grec,qid)[0]=='correct' for a in info['alt_values']) else 'FAIL'}]")

    # (2) ADVERSARIAL: a genuinely WRONG (non-tied) answer must STILL score wrong under s47t.
    print("\n(2) adversarial — a non-tied wrong answer must stay wrong under s47t:")
    # world_13: a country NOT tied at pop 0 (e.g. China, huge population) must fail.
    wrong_w = [{"Name": "China", "SurfaceArea": 9572900.0, "IndepYear": -1523}]
    vw, _, _ = score_tt(wrong_w, GOLD["world_1"]["world_13"], "world_13")
    print(f"    world_13 <- China (pop 1.2B, not tied): {vw}  [{'OK' if vw!='correct' else 'FAIL'}]")
    if vw == "correct": fails.append("world_13 accepted a non-tied wrong answer (China)")
    # bike_06: a bike NOT tied at 4 trips (e.g. 400 has 3) must fail.
    vb, _, _ = score_tt([{"bike_id": 400, "trip_count": 3}], GOLD["bike_1"]["bike_06"], "bike_06")
    print(f"    bike_06  <- bike 400 (3 trips, not tied): {vb}  [{'OK' if vb!='correct' else 'FAIL'}]")
    if vb == "correct": fails.append("bike_06 accepted a non-tied wrong answer (bike 400)")

    # (3) a NON-tie ranked query must be identical strict vs tolerant (empty alt set)
    print("\n(3) non-tie query unaffected (empty alt set => s47t == s47):")
    for qid in ("world_17", "bike_08", "wta_16"):
        d = "world_1" if qid.startswith("world") else ("bike_1" if qid.startswith("bike") else "wta_1")
        gd = json.loads((ROOT / "evidence/gold" / f"gold_spider_{d}.json").read_text())
        print(f"    {qid}: tie_alts={len(tie_alt_values(qid))}  [{'OK' if not tie_alt_values(qid) else 'FAIL'}]")
        if tie_alt_values(qid): fails.append(f"{qid} unexpectedly has tie alternatives")

    print("\n" + ("ADVERSARIAL CHECK PASSED — ties are data-verified, wrong answers still fail"
                  if not fails else "FAILURES:\n  " + "\n  ".join(fails)))
    return 0 if not fails else 1

if __name__ == "__main__":
    sys.exit(_adversarial())
