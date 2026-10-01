#!/usr/bin/env python
"""Full cross-system offline rescore — the s36-style gate for comparator changes.

For EVERY row of EVERY substantive results file (all 5 systems, all 5 domains,
plus the Phase L verification file), re-score the RECORDED raw_result with the
CURRENT comparator and diff against the recorded outcome_class. This is the
rescore AXIS only (values as recorded; no engine re-execution — that is
scripts/replay_goals.py's job), which is exactly what a comparator change must
be gated on: every flip it causes is attributable to the comparator alone.

Output: per-(file, system) before/after correct counts + every flip, listed
individually with its comparator reason. Pure local; writes nothing unless
--json is given.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evidence.eval_run.comparator import score, COMPARATOR_VERSION  # noqa: E402

GOLD = {
    "risk":     ROOT / "evidence" / "gold" / "gold.json",
    "employee": ROOT / "evidence" / "gold" / "gold_employee.json",
    "wta_1":    ROOT / "evidence" / "gold" / "gold_spider_wta_1.json",
    "world_1":  ROOT / "evidence" / "gold" / "gold_spider_world_1.json",
    "bike_1":   ROOT / "evidence" / "gold" / "gold_spider_bike_1.json",
}

FILES = {
    "results_off.rescored.jsonl":       "risk",
    "results_haiku.rescored.jsonl":     "risk",
    "results_haiku_post_fix1.jsonl":    "risk",
    "results_t5.rescored.jsonl":        "risk",
    "results_employee.jsonl":           "employee",
    "results_spider_wta_1.jsonl":       "wta_1",
    "results_spider_world_1.jsonl":     "world_1",
    "results_spider_bike_1.jsonl":      "bike_1",
    # Phase L verification rows (per-row domain field; network only)
    "results_verify_spider_fixes.jsonl": None,
}


def rescore_file(path: Path, domain: str | None):
    # Absent-tolerant: the risk domain's gold is withheld from the public
    # release, so load only the golds that are actually present.
    golds = {d: json.loads(p.read_text()) for d, p in GOLD.items() if p.exists()}
    per_system = defaultdict(lambda: {"n": 0, "old_correct": 0, "new_correct": 0})
    flips = []
    with open(path) as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            dom = domain or r.get("domain")
            qid = r["qid"]
            gold_rec = golds[dom].get(qid)
            if gold_rec is None:
                continue
            system = r.get("system", "network")
            old = r.get("outcome_class")
            new, reason, _ = score(r.get("raw_result"), gold_rec,
                                   status=r.get("status", "completed"))
            s = per_system[system]
            s["n"] += 1
            s["old_correct"] += (old == "correct")
            s["new_correct"] += (new == "correct")
            if new != old:
                flips.append({"system": system, "domain": dom, "qid": qid,
                              "seed": r.get("seed"), "old": old, "new": new,
                              "reason": reason})
    return per_system, flips


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)

    print(f"comparator version: {COMPARATOR_VERSION}")
    all_flips = []
    print(f"\n{'file':<38s} {'system':<12s} {'n':>5s} {'old✓':>6s} {'new✓':>6s} {'Δ':>4s}")
    print("-" * 78)
    for name, domain in FILES.items():
        path = ROOT / "results" / name
        if not path.exists():
            note = ("risk domain data not included in this release — skipping"
                    if domain == "risk" else "MISSING — skipped")
            print(f"{name:<38s} {note}")
            continue
        per_system, flips = rescore_file(path, domain)
        for system in sorted(per_system):
            s = per_system[system]
            delta = s["new_correct"] - s["old_correct"]
            print(f"{name:<38s} {system:<12s} {s['n']:>5d} {s['old_correct']:>6d} "
                  f"{s['new_correct']:>6d} {delta:>+4d}")
        for f in flips:
            f["file"] = name
        all_flips.extend(flips)

    print(f"\n{'='*78}\nFLIPS ({len(all_flips)} rows) — every one listed, both directions, never netted:")
    by_dir = Counter((f["old"], f["new"]) for f in all_flips)
    for (old, new), n in sorted(by_dir.items()):
        print(f"  {old} -> {new}: {n}")
    for f in all_flips:
        print(f"    [{f['file']}] {f['system']} {f['domain']}/{f['qid']} s{f['seed']}: "
              f"{f['old']} -> {f['new']}")
        print(f"        {f['reason'][:110]}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(all_flips, indent=1, default=str))
        print(f"\nflip detail -> {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
