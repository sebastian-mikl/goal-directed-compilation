#!/usr/bin/env python
"""Merge the C-1 (code_agent 4-call ceiling) targeted re-runs into the canonical
recorded-emission files the paper pipeline actually reads.

Discipline (matches every prior disclosed correction in this project):
  * Replacement is by exact (system, qid, seed) key — a re-emission drops in for
    precisely the cell it replaces, never a positional or fuzzy match.
  * A re-run row is only allowed to replace a row carrying the C-1 signature
    (status=timeout, llm_calls=4). Anything else aborts the merge.
  * Superseded rows are ARCHIVED to archive/pre_c1_merge/<file>.superseded.jsonl
    before being replaced, so the correction stays traceable rather than silent.
  * Temperature arms are kept strictly separate: a temperature=null re-run may
    never land in a temperature=0 parity file, and vice versa.

TARGET MAP rationale (verified against scripts/build_paper_numbers.py +
scripts/parity_overlay.py, not against the file names):
  results_t5.rescored.full.jsonl is a union SUPERSET that the paper pipeline
  never opens; the 95 risk cells are split across the files it does read.
"""
from __future__ import annotations
import json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "archive" / "pre_c1_merge"
C1 = {"status": "timeout", "llm_calls": 4}

# rerun file -> list of canonical targets it may write into.
# Each (rerun, target) pair is applied only to keys present in BOTH.
PLAN = [
    # --- default-temperature arm -------------------------------------------
    ("results_code_agent_bound12_rerun.jsonl", [
        "results_risk_t5_missing.jsonl",      # 53 T5 cells
        "results_off.rescored.jsonl",         # 42 non-T5 cells
        "results_t5.rescored.full.jsonl",     # union superset (not read by pipeline; kept consistent)
    ]),
    ("results_spider_code_agent_bound12_rerun.jsonl", [
        "results_spider_wta_1.jsonl",
        "results_spider_world_1.jsonl",
        "results_spider_bike_1.jsonl",
    ]),
    ("results_employee_code_agent_bound10_rerun.jsonl", [
        "results_employee.jsonl",
    ]),
    # --- temperature-0 parity arm ------------------------------------------
    # These are the files the CANONICAL reports/paper_numbers.csv is built from
    # (PARITY_TEMP0=1). The paper states "temperature 0 throughout"; fixing C-1
    # only at default temperature would leave that claim false for these cells.
    ("results_employee_parity_code_agent_bound10_rerun.jsonl", [
        "results_temp0_parity_employee.jsonl",
    ]),
    ("results_temp0_parity_c1_bound12_rerun_risk.jsonl", [
        "results_temp0_parity_risk.jsonl",
    ]),
    ("results_temp0_parity_c1_bound12_rerun_wta_1.jsonl", [
        "results_temp0_parity_wta_1.jsonl",
    ]),
    ("results_temp0_parity_c1_bound12_rerun_world_1.jsonl", [
        "results_temp0_parity_world_1.jsonl",
    ]),
    ("results_temp0_parity_c1_bound12_rerun_bike_1.jsonl", [
        "results_temp0_parity_bike_1.jsonl",
    ]),
]

def load(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

def key(r): return (r.get("system"), r.get("qid"), r.get("seed"))

def main(apply: bool):
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    grand = 0
    for rerun_fn, targets in PLAN:
        rr = ROOT / "results" / rerun_fn
        if not rr.exists():
            print(f"\n=== {rerun_fn}  NOT PRESENT — skipped")
            continue
        R = {key(r): r for r in load(rr)}
        print(f"\n=== {rerun_fn}  ({len(R)} re-emitted cells)")
        placed = set()
        for tgt_fn in targets:
            tgt = ROOT / "results" / tgt_fn
            rows = load(tgt)
            overlap = [i for i, r in enumerate(rows) if key(r) in R]
            if not overlap:
                print(f"    {tgt_fn:44s} no overlap — skipped")
                continue
            # STALE-ONLY replacement. results_t5.rescored.full.jsonl is a union
            # superset recording all 95 risk cells as timeout/4, but the files the
            # pipeline actually reads record some of those same cells as
            # `completed` (several at 3 calls, i.e. below the 4-call ceiling and so
            # never C-1-affected at all). A re-emission therefore replaces a row
            # ONLY where that row carries the C-1 signature in THAT file; a
            # non-stale row is a genuine recorded emission and is left untouched.
            hits = [i for i in overlap
                    if rows[i].get("status") == C1["status"]
                    and rows[i].get("llm_calls") == C1["llm_calls"]]
            skipped = len(overlap) - len(hits)
            if not hits:
                print(f"    {tgt_fn:44s} overlap={len(overlap):3d} but 0 stale — nothing to merge")
                continue
            superseded = [rows[i] for i in hits]
            if apply:
                arc = ARCHIVE / f"{tgt.stem}.superseded.jsonl"
                with arc.open("a") as fh:
                    for r in superseded:
                        fh.write(json.dumps(r) + "\n")
                for i in hits:
                    rows[i] = R[key(rows[i])]
                tgt.write_text("".join(json.dumps(r) + "\n" for r in rows))
            placed |= {key(r) for r in superseded}
            grand += len(hits)
            print(f"    {tgt_fn:44s} {'merged' if apply else 'would merge'} {len(hits):4d} stale cells"
                  f"  (left {skipped:3d} non-stale untouched)"
                  f"  -> {ARCHIVE.name}/{tgt.stem}.superseded.jsonl")
        missing = set(R) - placed
        if missing:
            print(f"    NOTE: {len(missing)} re-run cells not placed in any target: {sorted(missing)[:5]}")
    print(f"\nTOTAL cells {'merged' if apply else 'to merge'} = {grand}")

if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
