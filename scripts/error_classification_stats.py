#!/usr/bin/env python
"""Summary of the error classification (review/error_classification_prereg.md): the silent-error cells
of the planner, react and the code agent (and GDC's, as the rubric's check) labelled SPECIFICATION,
PROCEDURE, SCORING or UNCLEAR by two independent passes of fresh sub-agents and a third on their
disagreements (scripts/error_classification.py, review/error_classification.csv). Offline; no API.

If review/error_classification.csv is absent the script prints one line, exits 0 and writes nothing.
A file that is present must be the whole classification: its cells are exactly the in-scope strict
silent-error cells the builder gives today (scripts/error_classification.scope), every pair's labels
agree with the raw passes (review/error_classification/pass1.jsonl, pass2.jsonl, pass3.jsonl) by the
pre-registered pair rule, and pass 3 covers exactly the disagreements; anything else stops the
script (exit 1).

Pre-registered (review/error_classification_prereg.md): pool the planner, react and the code agent at
the (system, question) level; S = SPECIFICATION / (SPECIFICATION + PROCEDURE) over final pair labels.
OUTCOME A S >= 90%, B S <= 75%, M between; INCONCLUSIVE if UNCLEAR exceeds 20% of the pooled pairs.
GDC check: passes if every GDC pair is SPECIFICATION or SCORING. Agreement: percentage and Cohen's
kappa of the two passes, at the pair level (pooled, primary; and all pairs) and at the cell level.

Outputs, in reports/stats/error_classification/:
  by_system.csv   pairs and cells per system and final label
  headline.csv    S, the outcome, the UNCLEAR share, the agreement figures, the GDC check, the
                  sensitivity S with also_procedure pairs counted as PROCEDURE
  summary.txt     all of the above in words, with the disagreements

    python scripts/error_classification_stats.py        # run_all.sh step 6
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

CSV_IN = ROOT / "review" / "error_classification.csv"
OUT = ROOT / "reports" / "stats" / "error_classification"
POOLED = ("planner", "react", "code_agent")
CHECK = "network"
LABELS = ("SPECIFICATION", "PROCEDURE", "SCORING", "UNCLEAR")
A_MIN, B_MAX, UNCLEAR_MAX = 0.90, 0.75, 0.20


def kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def check(rows: list[dict]) -> None:
    """The labelled cells are the scope's, and the CSV agrees with the raw passes."""
    import scripts.error_classification as EC
    G, cells = EC.builder()
    scope = {(c["system"], c["dom"], c["qid"], c["seed"]) for c in EC.scope(G, cells)}
    held = {(r["system"], r["domain"], r["qid"], int(r["seed"])) for r in rows if r["row"] == "cell"}
    if scope != held or sum(r["row"] == "cell" for r in rows) != len(held):
        raise SystemExit(f"{CSV_IN.name}: its cells are not the in-scope silent-error cells "
                         f"({len(held & scope)} of {len(scope)}; {len(held - scope)} extra)")
    groups, _ = EC.group_counts()
    p1, p2, p3 = EC.read_pass(1), EC.read_pass(2), EC.read_pass(3)
    dis = EC.disagreements(groups, p1, p2)
    if set(p3) != set(dis):
        raise SystemExit("pass 3 does not cover exactly the disagreements")
    pairs = {r["pair_id"]: r for r in rows if r["row"] == "pair"}
    if set(pairs) != set(groups):
        raise SystemExit(f"{CSV_IN.name}: its pairs differ from review/error_classification/cells.csv")
    for pid, gs in groups.items():
        a, b = EC.labels_of(p1[pid], gs), EC.labels_of(p2[pid], gs)
        final = EC.labels_of(p3[pid], gs) if pid in p3 else a
        lab = EC.pair_label([final[g][0] for g in gs for _ in gs[g]])[0]
        want = (EC.pair_label([a[g][0] for g in gs for _ in gs[g]])[0],
                EC.pair_label([b[g][0] for g in gs for _ in gs[g]])[0], lab)
        got = (pairs[pid]["pass1_label"], pairs[pid]["pass2_label"], pairs[pid]["final_label"])
        if want != got:
            raise SystemExit(f"{pid}: {CSV_IN.name} labels {got} differ from the raw passes {want}")
    for name, recs in (("pass1", p1), ("pass2", p2), ("pass3", p3)):
        bad = sorted(r["item"] for r in recs.values() if not r.get("isolated"))
        if bad:
            raise SystemExit(f"{name}: labellers that read other files or used other tools: {bad}")


def compute(rows: list[dict], out: Path, echo=print) -> dict:
    pairs = [r for r in rows if r["row"] == "pair"]
    cells = [r for r in rows if r["row"] == "cell"]
    by = []
    for s in POOLED + (CHECK,):
        for lab in LABELS:
            by.append({"system": s, "label": lab,
                       "pairs": sum(1 for p in pairs if p["system"] == s and p["final_label"] == lab),
                       "cells": sum(1 for c in cells if c["system"] == s and c["final_label"] == lab)})
    pool = [p for p in pairs if p["system"] in POOLED]
    n = Counter(p["final_label"] for p in pool)
    spec, proc, unc = n["SPECIFICATION"], n["PROCEDURE"], n["UNCLEAR"]
    S = spec / (spec + proc) if spec + proc else float("nan")
    both = sum(1 for p in pool if p["also_procedure"] == "True" and p["final_label"] == "SPECIFICATION")
    S_sens = (spec - both) / (spec + proc) if spec + proc else float("nan")
    unc_share = unc / len(pool)
    outcome = ("INCONCLUSIVE" if unc_share > UNCLEAR_MAX else
               "A" if S >= A_MIN else "B" if S <= B_MAX else "M")
    gdc = [p for p in pairs if p["system"] == CHECK]
    gdc_ok = all(p["final_label"] in ("SPECIFICATION", "SCORING") for p in gdc)
    agree = {
        "pairs_pooled": (sum(p["passes_agree"] == "True" for p in pool), len(pool),
                         kappa([p["pass1_label"] for p in pool], [p["pass2_label"] for p in pool])),
        "pairs_all": (sum(p["passes_agree"] == "True" for p in pairs), len(pairs),
                      kappa([p["pass1_label"] for p in pairs], [p["pass2_label"] for p in pairs])),
        "cells_all": (sum(c["pass1_label"] == c["pass2_label"] for c in cells), len(cells),
                      kappa([c["pass1_label"] for c in cells], [c["pass2_label"] for c in cells])),
    }
    head = [
        {"key": "pooled_pairs", "value": len(pool), "note": "planner, react, code agent"},
        {"key": "pooled_specification", "value": spec, "note": "pairs"},
        {"key": "pooled_procedure", "value": proc, "note": "pairs"},
        {"key": "pooled_scoring", "value": n["SCORING"], "note": "pairs"},
        {"key": "pooled_unclear", "value": unc, "note": "pairs"},
        {"key": "S_pct", "value": f"{100 * S:.4f}", "note": "SPECIFICATION / (SPECIFICATION + PROCEDURE), pooled pairs"},
        {"key": "unclear_pct", "value": f"{100 * unc_share:.4f}", "note": f"INCONCLUSIVE above {100 * UNCLEAR_MAX:.0f}%"},
        {"key": "outcome", "value": outcome, "note": "pre-registered: A S >= 90%, B S <= 75%, M between"},
        {"key": "S_pct_also_procedure_as_procedure", "value": f"{100 * S_sens:.4f}",
         "note": f"sensitivity: {both} SPECIFICATION pairs flagged also_procedure counted as PROCEDURE"},
        {"key": "gdc_pairs", "value": len(gdc), "note": ", ".join(f"{p['qid']} {p['final_label']}" for p in gdc)},
        {"key": "gdc_check", "value": "pass" if gdc_ok else "fail", "note": "every GDC pair SPECIFICATION or SCORING"},
        {"key": "pairs_cells_differ", "value": sum(p["cells_differ"] == "True" for p in pairs),
         "note": "pairs whose repetitions got different final labels"},
    ]
    for k, (a, m, kp) in agree.items():
        head.append({"key": f"agreement_{k}_pct", "value": f"{100 * a / m:.4f}" if m else "", "note": f"{a} of {m}"})
        head.append({"key": f"kappa_{k}", "value": f"{kp:.4f}", "note": "Cohen's kappa, passes 1 and 2, four labels"})
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "by_system.csv", by)
    _write(out / "headline.csv", head)
    dis = [p for p in pairs if p["passes_agree"] != "True"]
    L = ["Error classification (review/error_classification_prereg.md): strict silent-error cells, claude-sonnet-4-5,",
         "in scope and within the vocabulary (employee as run, Spider and held-out answer tiers).", "",
         "Final pair labels (cells in brackets):"]
    for s in POOLED + (CHECK,):
        L.append(f"  {s:<11} " + "  ".join(f"{lab} {next(b['pairs'] for b in by if b['system'] == s and b['label'] == lab)}"
                                         f" ({next(b['cells'] for b in by if b['system'] == s and b['label'] == lab)})"
                                         for lab in LABELS))
    L += ["", f"Pooled (planner, react, code agent): {len(pool)} pairs; S = {spec}/({spec}+{proc}) = {100 * S:.1f}%; "
          f"UNCLEAR {unc} ({100 * unc_share:.1f}%); OUTCOME {outcome}.",
          f"Sensitivity, also_procedure pairs counted as PROCEDURE ({both}): S = {100 * S_sens:.1f}%.",
          f"GDC check: {'pass' if gdc_ok else 'FAIL'} ({', '.join(p['qid'] + ' ' + p['final_label'] for p in gdc)}).",
          "Agreement of passes 1 and 2: " + "; ".join(f"{k} {a}/{m} = {100 * a / m:.1f}%, kappa {kp:.3f}"
                                                      for k, (a, m, kp) in agree.items() if m) + ".",
          f"Disagreements ({len(dis)}), resolved by pass 3:"]
    for p in dis:
        L.append(f"  {p['pair_id']}: pass 1 {p['pass1_label']}, pass 2 {p['pass2_label']} -> {p['final_label']}")
    (out / "summary.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    echo(f"error classification: OUTCOME {outcome} (S {100 * S:.1f}%, {len(pool)} pooled pairs); GDC check "
         f"{'pass' if gdc_ok else 'FAIL'}; outputs {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}/")
    return {"outcome": outcome, "S": S, "by_system": by, "headline": head, "agreement": agree, "gdc_ok": gdc_ok}


def _write(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", default=str(CSV_IN))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-check", action="store_true", help="skip the scope and raw-pass checks (tests only)")
    a = ap.parse_args(argv)
    path = Path(a.csv)
    if not path.exists():
        print(f"error classification: {path.name} not present, nothing written")
        return 0
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not a.no_check:
        check(rows)
    compute(rows, Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
