#!/usr/bin/env python
"""Compare this release's files with the sha256 values in heldout/FROZEN.json.

FROZEN.json was written in the development tree, whose layout differs from this
repository's (the engine sat in planner/ and gdc/, the Spider registers at wta_1/,
world_1/ and bike_1/, the results at the top level). freeze.py --check hashes those
paths and so cannot run here: it stops at the first file this release does not ship.
This script reads FROZEN.json, looks each entry up at its path in this layout and says,
per entry, MATCH, DIFFERS or ABSENT. It changes nothing and does not replace freeze.py.

    python heldout/check_frozen_files.py

Exit status 0 when every entry under heldout/ matches (the pre-registration, the held-out
code, corpora, registers, schemas, gold and tie file are byte for byte what was frozen),
1 otherwise. The other entries are reported only: they are development-tree files that this
release carries in edited form (comments, docstrings and paths) or does not carry.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# frozen path (development layout) -> path in this release, where the file moved
MOVED = {
    "heldout/build_gold.py": "heldout/build_gold_heldout.py",     # renamed: build_gold.py is the withheld risk builder's name
    "wta_1/schema.json": "evaluation/domains/spider_wta_1/schema.json",
    "wta_1/matches.pkl": "evaluation/domains/spider_wta_1/matches.pkl",
    "world_1/schema.json": "evaluation/domains/spider_world_1/schema.json",
    "world_1/country.pkl": "evaluation/domains/spider_world_1/country.pkl",
    "bike_1/schema.json": "evaluation/domains/spider_bike_1/schema.json",
    "bike_1/trip.pkl": "evaluation/domains/spider_bike_1/trip.pkl",
    **{f"results_spider_{d}_postfix_s40off.provenance.json": f"results/results_spider_{d}_postfix_s40off.provenance.json"
       for d in ("wta_1", "world_1", "bike_1")},
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    frozen = json.loads((HERE / "FROZEN.json").read_text())
    counts = {"MATCH": 0, "DIFFERS": 0, "ABSENT": 0}
    bad_held = []
    for f, h in sorted(frozen["files"].items()):
        p = ROOT / MOVED.get(f, f)
        status = "ABSENT" if not p.exists() else ("MATCH" if sha(p) == h else "DIFFERS")
        counts[status] += 1
        where = f" (here: {MOVED[f]})" if f in MOVED else ""
        print(f"  {status:<7} {f}{where}")
        if f.startswith("heldout/") and status != "MATCH":
            bad_held.append(f)
    print(f"\n{len(frozen['files'])} frozen files: {counts['MATCH']} match, {counts['DIFFERS']} differ, "
          f"{counts['ABSENT']} not in this release.")
    print(f"engine files: the {len(frozen['engine_sha256'])} engine hashes in FROZEN.json are those of the "
          f"development layout (planner/*.py, gdc/primitives.py); this release's engine is pinned by "
          f"PORTED_ENGINE in scripts/parity_overlay.py and is not compared here.")
    print("heldout/ entries: " + ("all match." if not bad_held else f"NOT matching: {bad_held}"))
    return 0 if not bad_held else 1


if __name__ == "__main__":
    sys.exit(main())
