#!/usr/bin/env python
"""Write heldout/FROZEN.json: the sha256 of every file the held-out run and its analysis
depend on, and the engine-file hashes of the frozen commit. Run once, when the
pre-registration is approved and before any held-out cell runs; heldout/run_heldout.py
refuses to plan or run if any hash differs.

    python heldout/freeze.py [--check]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from scripts.parity_overlay import engine_hashes  # noqa: E402

FROZEN_COMMIT = "8605f521f4d106509b79ca49f0f8fc439dc2fd3f"   # public-release
FILES = [
    # the pre-registration and the held-out code
    "heldout/PREREGISTRATION.md", "heldout/sql_tier.py", "heldout/build_domains.py", "heldout/build_gold.py",
    "heldout/detect_ties.py", "heldout/run_heldout.py", "heldout/analyse_heldout.py", "heldout/freeze.py",
    # what they built
    "heldout/eval_corpus_spider_employee_hire_evaluation.json", "heldout/eval_corpus_spider_train_station.json",
    "heldout/employee_hire_evaluation/schema.json", "heldout/employee_hire_evaluation/shop.pkl",
    "heldout/train_station/schema.json", "heldout/train_station/station.pkl",
    "heldout/gold/gold_spider_employee_hire_evaluation.json",
    "heldout/gold/gold_spider_employee_hire_evaluation_digest.txt",
    "heldout/gold/gold_spider_train_station.json", "heldout/gold/gold_spider_train_station_digest.txt",
    "heldout/tie_alternatives_heldout.json",
    # what they read, tracked at the frozen commit
    "combined_data.parquet", "dev_data.parquet",
    "spider/employee_hire_evaluation.sqlite", "database/train_station/train_station.sqlite",
    "evidence/gold/spider_gold_common.py", "evidence/eval_run/comparator.py", "evidence/eval_run/briefing.py",
    "evidence/eval_run/logger.py", "evidence/eval_run/refuse_config.py", "evidence/eval_run/emission_config.py",
    "evidence/eval_run/pricing.py", "gdc/config.py",
    "scripts/detect_ties.py", "scripts/rerun_temp0_batch2.py", "scripts/parity_overlay.py",
    "scripts/probe_temperature.py",
    # the s45 reference: GDC's Spider numbers of record and their databases
    "results_spider_wta_1_postfix_s40off.provenance.json", "results_spider_world_1_postfix_s40off.provenance.json",
    "results_spider_bike_1_postfix_s40off.provenance.json",
    "wta_1/schema.json", "wta_1/matches.pkl", "world_1/schema.json", "world_1/country.pkl",
    "bike_1/schema.json", "bike_1/trip.pkl",
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def snapshot() -> dict:
    return {"frozen_commit": FROZEN_COMMIT,
            "files": {f: sha(ROOT / f) for f in FILES},
            "engine_sha256": engine_hashes(ROOT)}


def main(argv=None) -> int:
    out = HERE / "FROZEN.json"
    snap = snapshot()
    if "--check" in (argv if argv is not None else sys.argv[1:]):
        old = json.loads(out.read_text())
        diff = sorted(k for k in set(old["files"]) | set(snap["files"])
                      if old["files"].get(k) != snap["files"].get(k))
        eng = old["engine_sha256"] != snap["engine_sha256"]
        print("FROZEN.json matches" if not diff and not eng else f"DIFFERS: files {diff}, engine {eng}")
        return 0 if not diff and not eng else 1
    out.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out.name}: {len(snap['files'])} files, {len(snap['engine_sha256'])} engine files; "
          f"sha256 {sha(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
