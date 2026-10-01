#!/usr/bin/env python
"""Before/after comparison for one system across tiers, plus term-binding audit.

Compares a baseline results file against a fresh run over the (qid, seed) cells
they share, per tier, and lists every cell whose outcome changed in either
direction. Regressions print FIRST and drive the exit code, so a win on the
targeted query cannot hide a loss elsewhere.

`--audit-col TERM COL` additionally reports which column each run bound a given
category term to — the check for Fix 2 (schema-pinned term binding), where the
failure mode is a silent rebinding (group -> source) rather than a crash.

Usage:
  python scripts/compare_runs.py BASE.jsonl NEW.jsonl
  python scripts/compare_runs.py BASE.jsonl NEW.jsonl --audit-col Technical group
  python scripts/compare_runs.py BASE.jsonl NEW.jsonl --system planner

Exit codes: 0 = no regressions, 1 = at least one cell regressed.
Pure local: no API key, reads results files only, writes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

TIER_ORDER = ["T1_single_op", "T2_two_ops", "T3_three_ops",
              "T4_business_rule_multi_op", "T5_high_complexity",
              "house_vocab", "out_of_range"]


def load(path: str, system: str) -> dict:
    out = {}
    with open(path) as fh:
        for line in fh:
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("system") != system:
                continue
            out[(d["qid"], d["seed"])] = d
    return out


def where_cols(rec: dict, term: str) -> list[str]:
    """Columns this record filtered `term` on (artifact goals store where as triples)."""
    g = (rec.get("artifact") or {}).get("goal") or {}
    hits = []
    for conj in g.get("where", []):
        try:
            col, _op, val = conj
        except (ValueError, TypeError):
            continue
        vals = val if isinstance(val, list) else [val]
        if any(str(v) == term for v in vals):
            hits.append(str(col))
    return hits


def tier_table(recs: dict) -> dict:
    agg = defaultdict(lambda: [0, 0])
    for d in recs.values():
        b = agg[d.get("tier")]
        b[1] += 1
        if d.get("outcome_class") == "correct":
            b[0] += 1
    return agg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline")
    ap.add_argument("new")
    ap.add_argument("--system", default="network")
    ap.add_argument("--audit-col", nargs=2, metavar=("TERM", "EXPECTED_COL"), default=None)
    a = ap.parse_args()

    base, new = load(a.baseline, a.system), load(a.new, a.system)
    common = sorted(set(base) & set(new))
    if not common:
        print("no overlapping (qid, seed) cells — nothing to compare", file=sys.stderr)
        return 1

    bt, nt = tier_table({k: base[k] for k in common}), tier_table({k: new[k] for k in common})
    print(f"=== {a.system}: per-tier accuracy, baseline -> new ({len(common)} shared cells) ===\n")
    print(f"{'tier':28} {'before':>12}  {'after':>12}   delta")
    tb = nb = tn = nn = 0
    for tier in TIER_ORDER + sorted(set(bt) - set(TIER_ORDER)):
        if tier not in bt:
            continue
        bc, bnn = bt[tier]
        nc, nnn = nt.get(tier, (0, 0))
        tb += bc; nb += bnn; tn += nc; nn += nnn
        d = 100 * nc / nnn - 100 * bc / bnn if bnn and nnn else 0
        flag = "  <== REGRESSION" if d < -1e-9 else ("  <== gain" if d > 1e-9 else "")
        print(f"{tier:28} {bc:4d}/{bnn:<3d}{100*bc/bnn:6.1f}%  "
              f"{nc:4d}/{nnn:<3d}{100*nc/nnn:6.1f}%  {d:+6.1f}{flag}")
    print(f"{'-'*28} {'-'*12}  {'-'*12}")
    print(f"{'OVERALL':28} {tb:4d}/{nb:<3d}{100*tb/nb:6.1f}%  "
          f"{tn:4d}/{nn:<3d}{100*tn/nn:6.1f}%  {100*tn/nn-100*tb/nb:+6.1f}")

    regressions, gains = [], []
    for k in common:
        b_ok = base[k].get("outcome_class") == "correct"
        n_ok = new[k].get("outcome_class") == "correct"
        if b_ok and not n_ok:
            regressions.append(k)
        elif n_ok and not b_ok:
            gains.append(k)

    print(f"\n=== REGRESSIONS (correct -> not correct): {len(regressions)} ===")
    for qid, seed in regressions:
        n = new[(qid, seed)]
        print(f"  {qid}/s{seed} [{n.get('tier')}] -> {n.get('outcome_class')} ({n.get('failure_tag')})")
        print(f"      {str(n.get('error') or n.get('comparator_reason'))[:150]}")
    if not regressions:
        print("  none")

    print(f"\n=== GAINS (not correct -> correct): {len(gains)} ===")
    for qid, seed in gains:
        print(f"  {qid}/s{seed} [{new[(qid, seed)].get('tier')}] "
              f"{base[(qid, seed)].get('outcome_class')} -> correct")
    if not gains:
        print("  none")
    print(f"\nnet: {len(gains)-len(regressions):+d} cells")

    if a.audit_col:
        term, expected = a.audit_col
        print(f"\n=== TERM-BINDING AUDIT: {term!r} (expected column: {expected!r}) ===")
        rows = []
        for k in common:
            b_cols, n_cols = where_cols(base[k], term), where_cols(new[k], term)
            if b_cols or n_cols:
                rows.append((k, b_cols, n_cols))
        if not rows:
            print(f"  no cell filtered on {term!r} in either run")
        agg = defaultdict(lambda: [0, 0, 0])   # qid -> [n, base_ok, new_ok]
        for (qid, _s), b_cols, n_cols in rows:
            r = agg[qid]
            r[0] += 1
            r[1] += all(c == expected for c in b_cols) and bool(b_cols)
            r[2] += all(c == expected for c in n_cols) and bool(n_cols)
        for qid in sorted(agg):
            n, bok, nok = agg[qid]
            flag = "  <== MISBOUND" if nok < n else ""
            print(f"  {qid:8} cells={n:2d}  correct-binding before={bok:2d}  after={nok:2d}{flag}")
        tot_n = sum(v[0] for v in agg.values())
        tot_b = sum(v[1] for v in agg.values())
        tot_a = sum(v[2] for v in agg.values())
        print(f"  TOTAL    cells={tot_n:2d}  correct-binding before={tot_b:2d}  after={tot_a:2d}")

    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
