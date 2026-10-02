#!/usr/bin/env python
"""Check reports/decline_readings.json against the recorded cells (D8, hand readings).

Every no-answer cell of react, code_agent, text_to_sql and the planner that a reported set holds
(Spider oos/answer/boundary tiers, employee, risk), and that the text rule (paper_inputs.rule_channel)
classes `explicit` or `failed`, must have a reading; every reading must belong to such a cell. The
cells are read as the builder reads them (PARITY_TEMP0=1: the temperature-0 overlays, batch 2
included, so the check needs the same untracked inputs as the build). Prints, per domain group and
system, how many cells the readings move between the two classes.

    PYTHONDONTWRITEBYTECODE=1 python scripts/check_decline_readings.py

Exit 0 when complete and consistent, 1 when not, 77 when the recorded results are absent.
"""
from __future__ import annotations

import collections
import os
import sys
from pathlib import Path

os.environ["PARITY_TEMP0"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import paper_inputs as pi  # noqa: E402

SYN_TIERS = ["T1_single_op", "T2_two_ops", "T3_three_ops", "T4_business_rule_multi_op", "T5_high_complexity",
             "house_vocab", "algebra_boundary", "out_of_range"]
TEXT_SYSTEMS = ("planner", "react", "code_agent", "text_to_sql")


def reported_cells() -> list:
    """(dom, row) of every cell of the text systems that a reported number reads."""
    out = []
    for d in pi.SPIDER:
        for s in TEXT_SYSTEMS:
            out += [(d, r) for r in pi.load(pi.spider_file(d, s)) if r.get("system") == s and r.get("qid") in pi.GOLD[d]]
    emp = pi.employee()
    out += [("employee", r) for r in emp if r["system"] in TEXT_SYSTEMS and r.get("qid") in pi.GOLD["employee"]]
    if "risk" in pi.GOLD:
        off, t5 = pi.risk_off(), pi.risk_t5()
        for r in off:
            if r["system"] in TEXT_SYSTEMS and r["tier"] != pi.T5 and r.get("qid") in pi.GOLD["risk"]:
                out.append(("risk", r))
        for r in t5:
            if r["system"] in TEXT_SYSTEMS and r["tier"] == pi.T5 and r.get("qid") in pi.GOLD["risk"]:
                out.append(("risk", r))
    return out


def main() -> int:
    batch2 = [f"results_temp0_batch2_{d}.jsonl" for d in ("wta_1", "world_1", "bike_1", "employee")]
    if (not (pi.RESULTS_DIR / "results_employee.jsonl").exists() or not pi.DECLINE_READINGS_FILE.exists()
            or any(not (pi.RESULTS_DIR / f).exists() for f in batch2)):
        print("recorded results, batch-2 files or readings file absent: the readings are keyed to the batch-2 cells")
        return 77
    cells = reported_cells()
    seen, wanted = {}, {}
    for dom, r in cells:
        k = pi.reading_key(dom, r)
        ch = pi.rule_channel(dom, r)
        if k in seen and seen[k] != ch:
            print(f"AMBIGUOUS KEY {k}: two reported cells share it with different classes")
            return 1
        seen[k] = ch
        if ch is not None and ch[0] in ("explicit", "failed"):
            wanted[k] = ch[0]
    domains = {d for d, _ in cells}
    readings = {k: v for k, v in pi.DECLINE_READINGS.items() if k.split("|")[0] in domains}
    bad = []
    missing = sorted(set(wanted) - set(readings))
    if missing:
        bad.append(f"{len(missing)} no-answer cells with no reading, e.g. {missing[:3]}")
    stray = sorted(k for k in readings if seen.get(k) is None or seen[k][0] not in ("explicit", "failed"))
    if stray:
        bad.append(f"{len(stray)} readings for cells that are not text-classed no-answer cells, e.g. {stray[:3]}")
    moved = collections.Counter()
    for k, v in readings.items():
        if k in wanted:
            dom, sysname = k.split("|")[:2]
            moved[("risk" if dom == "risk" else "employee" if dom == "employee" else "spider", sysname,
                   wanted[k], v["class"])] += 1
    for k, n in sorted(moved.items()):
        print(f"  {k[0]:<9} {k[1]:<12} rule {k[2]:<8} -> read {k[3]:<8} {n}")
    print(f"{len(wanted)} text-classed no-answer cells, {len(readings)} readings, "
          f"{sum(1 for k, v in readings.items() if wanted.get(k) != v['class'])} change class")
    for b in bad:
        print("FAIL:", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
