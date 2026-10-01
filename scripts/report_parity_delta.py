#!/usr/bin/env python
"""Before/after/delta for the temperature-parity re-run.

RESOLVED as of the parity adoption: the confound was real but small (all
in-contract silent-error deltas <=1.3 points, no relative standing changed), so
the temperature-0 numbers are now CANONICAL:

BEFORE = reports/paper_numbers_default_temp.csv  (provenance snapshot — three
          baselines at the original API-default temperature)
AFTER  = reports/paper_numbers.csv               (canonical — all five systems
          at temperature 0; produced by PARITY_TEMP0=1 python
          scripts/build_paper_numbers.py, then copied over the old
          default-temp paper_numbers.csv)

For every headline number that involves the three baselines it prints the
provenance (pre-parity) value, the canonical (temperature-0) value, and the
delta. GDC (`network`) and planner are shown for context — they already
emitted at temperature 0 throughout, so they must NOT move (0 rows moved,
verified).

No API, no writes — pure read of the two CSVs.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEFORE = ROOT / "reports" / "paper_numbers_default_temp.csv"
AFTER = ROOT / "reports" / "paper_numbers.csv"

SYSTEMS = ["network", "planner", "react", "code_agent", "text_to_sql"]
BASELINES = {"react", "code_agent", "text_to_sql"}
# (fig_table, domain label) blocks to report, in order
BLOCKS = [
    ("Fig1/Fig2/Table3", "spider_pooled", "Spider in-contract (answer tiers, pooled)"),
    ("Table2", "risk", "Risk in-contract (T1-T5 + house_vocab)"),
    ("Table2", "employee", "Employee in-contract (T1-T5 + house_vocab)"),
]


def load_csv(p):
    if not p.exists():
        return None
    d = {}
    with p.open() as fh:
        for r in csv.DictReader(fh):
            key = (r["fig_table"], r["domain"], r["system"], r["tier_group"], r["scoring"], r["metric"])
            d[key] = r
    return d


def val(row):
    if row is None:
        return None
    v = row.get("value_pct")
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def frac(row):
    return f"{row['numerator']}/{row['denominator']}" if row else "—"


def cell(v):
    return "  —  " if v is None else f"{v:5.1f}"


def dcell(b, a):
    if b is None or a is None:
        return "   ? "
    d = a - b
    return f"{d:+5.1f}"


def main():
    before = load_csv(BEFORE)
    after = load_csv(AFTER)
    if before is None:
        print(f"ERROR: {BEFORE} not found — this is the provenance snapshot; it should be checked in.")
        return 2
    have_after = after is not None
    print("=" * 92)
    print("TEMPERATURE-PARITY  PROVENANCE (default temp)  vs  CANONICAL (temp 0)  —  three baselines")
    print(f"  BEFORE: {BEFORE.name}      AFTER: {AFTER.name} "
          f"{'[present]' if have_after else '[NOT PRESENT — run scripts/build_paper_numbers.py]'}")
    print("=" * 92)

    for fig, dom, title in BLOCKS:
        for scoring in ("strict", "tolerant"):
            # skip tolerant for the syn domains (only spider has a tolerant column)
            keys_here = [(fig, dom, s, tg, scoring, "accuracy")
                         for s in SYSTEMS for tg in ("in_contract_answer",)]
            if not any(k in before for k in keys_here):
                continue
            print(f"\n{title}  [{scoring}]")
            print(f"  {'system':<12} {'acc_before':>10} {'acc_after':>10} {'Δacc':>7}   "
                  f"{'sil_before':>10} {'sil_after':>10} {'Δsil':>7}   frac")
            for s in SYSTEMS:
                ka = (fig, dom, s, "in_contract_answer", scoring, "accuracy")
                ks = (fig, dom, s, "in_contract_answer", scoring, "silent_wrong")
                if ka not in before:
                    continue
                ab, aa = val(before.get(ka)), val((after or {}).get(ka))
                sb, sa = val(before.get(ks)), val((after or {}).get(ks))
                tag = " *" if s in BASELINES else "  "   # * = baseline (re-emitted at temp 0)
                print(f"  {s:<12}{tag}{cell(ab):>9} {cell(aa):>10} {dcell(ab, aa):>7}   "
                      f"{cell(sb):>10} {cell(sa):>10} {dcell(sb, sa):>7}   {frac(before.get(ka))}")
    print("\n  * = baseline re-emitted at temperature 0 (moves).  network/planner already temp 0 (context; must not move).")
    if not have_after:
        print("  AFTER missing: run  PARITY_TEMP0=1 python scripts/build_paper_numbers.py  "
              "and copy its output over reports/paper_numbers.csv, then re-run this.")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())
