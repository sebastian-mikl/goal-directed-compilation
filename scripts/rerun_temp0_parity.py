#!/usr/bin/env python
"""Temperature-parity re-run — LIVE, needs ANTHROPIC_API_KEY.

Re-emit the three default-temperature baselines (react, code_agent, text_to_sql)
at temperature 0 across EVERY in-contract cell of all three domains, full reps, so
all five systems run at temperature 0 and the thesis comparison "differ only in
architecture" becomes literally true — the confound is removed, not argued away.

Motivation — the two-directional temperature probe (scripts/probe_temperature.py
and scripts/probe_temperature_reverse.py) found the confound partly real:
    silent-wrong -> correct at temp 0 : 20.6%   (asymmetric)
    correct      -> silent-wrong       :  0.0%
so re-sampling does not cancel out; the honest fix is a full parity re-run.

Scope (required — in-contract tiers, three baselines, full reps):
    risk      T1-T5 + house_vocab   83 qids x 5 seeds x 3 sys = 1245 cells
    employee  T1-T5 + house_vocab   81 qids x 3 seeds x 3 sys =  729 cells
    spider    answer tiers          27 qids x 5 seeds x 3 sys =  405 cells
                                                      TOTAL     2379 cells
Estimated cost ~ $38.5 (sum of the recorded cost_usd over exactly these cells;
temperature 0 does not change token/call counts materially). Full reps is the
faithful drop-in and the default. At temperature 0 the reps are near-deterministic,
so a one-rep run would be ~4x cheaper (~$9.3) — but the parity overlay matches by
exact (system, qid, seed), so `--seeds 0` alone leaves seeds 1-4 at default
temperature (a MIXED before/after); a true one-rep path needs a seed-broadcast
overlay that is not yet wired. Prefer full reps.

Held IDENTICAL to the recorded runs: same prompts, harness, model
claude-sonnet-4-5, bounds (react<=15 iters, code_agent<=3 retries, 60s timeout —
the harness DEFAULTS), prompt caching. ONLY temperature changes (default -> 0).
GDC (`network`) and planner already emit at temperature 0 and are NOT re-run.

Output — per-domain overlay files results_temp0_parity_{dom}.jsonl. Regenerate
the paper numbers over them with:
    PARITY_TEMP0=1 python scripts/build_paper_numbers.py   -> reports/paper_numbers_parity.csv
    PARITY_TEMP0=1 python scripts/pass3_outcome_stats.py   -> five-way / Wilson under parity
    python scripts/report_parity_delta.py                  -> before/after/delta tables
The overlay replaces ONLY the three baselines' in-contract cells, matched by exact
(system, qid, seed); GDC/planner and every out-of-contract row are untouched.
Resumable: run_cells skips any (system, qid, seed) already present, so re-running
the same command resumes and never double-charges.

Usage:
    python scripts/rerun_temp0_parity.py --dry       # scope + cost, NO API
    python scripts/rerun_temp0_parity.py             # LIVE full-reps parity re-run
    python scripts/rerun_temp0_parity.py --seeds 0   # LIVE one-rep approximation
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:                                   # pick up ANTHROPIC_API_KEY from .env, if present
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                      # noqa: BLE001 — .env is optional
    pass

from evidence.eval_run.comparator import COMPARATOR_VERSION            # noqa: E402
assert COMPARATOR_VERSION == "s48", COMPARATOR_VERSION
from scripts.probe_temperature import (                                # noqa: E402
    DOMAINS, BASELINES, MODEL, IN_SYN, BOUNDARY, _load)

PARITY_TEMP = 0
SEEDS_BY_DOMAIN = {"employee": [0, 1, 2]}     # employee = 3 reps; everything else = 5
DEFAULT_SEEDS = [0, 1, 2, 3, 4]


def seeds_for(dom):
    return SEEDS_BY_DOMAIN.get(dom, DEFAULT_SEEDS)


def out_results(dom):
    return ROOT / f"results/results_temp0_parity_{dom}.jsonl"


def out_prov(dom):
    return ROOT / f"results/results_temp0_parity_{dom}.provenance.json"


def in_contract_qids(cfg):
    """The in-contract question set from gold — for syn (risk/employee) the
    T1-T5+house_vocab tiers; for spider the answer tiers (not boundary). This
    exactly equals the recorded in-contract baseline qid set (verified: identical
    across all three baselines and equal to gold)."""
    gold = json.loads(cfg["gold"].read_text())
    if cfg["kind"] == "syn":
        return sorted(q for q, g in gold.items() if g.get("tier") in IN_SYN)
    return sorted(q for q, g in gold.items() if g.get("tier") not in BOUNDARY)


def recorded_cost(dom, cfg, qids, seeds):
    """Sum recorded cost_usd over exactly the in-contract baseline cells being
    re-run — the temp-0 cost proxy. Uses the canonical per-tier sources so the
    risk T5 split matches build_paper_numbers.py."""
    rows = []
    if cfg["kind"] == "syn":
        if cfg.get("t5_split"):
            for fn in cfg["recorded_nonT5"]:
                rows += [r for r in _load(fn) if r.get("tier") != "T5_high_complexity"]
            for fn in cfg["recorded_T5"]:
                rows += [r for r in _load(fn) if r.get("tier") == "T5_high_complexity"]
        else:
            for fn in cfg["recorded_all"]:
                rows += _load(fn)
    else:
        rows += _load(cfg["recorded"][0])
    qset, sset = set(qids), set(seeds)
    n = 0
    cost = 0.0
    for r in rows:
        if (r.get("system") in BASELINES and r.get("qid") in qset and r.get("seed") in sset
                and not (cfg["kind"] == "syn" and r.get("tier") not in IN_SYN)):
            cost += r.get("cost_usd") or 0.0
            n += 1
    return n, cost


def scope(seeds_override=None):
    print("=" * 84)
    print("TEMPERATURE-PARITY RE-RUN — three baselines at temperature 0, in-contract, full reps")
    print("=" * 84)
    grand_cells = 0
    grand_cost = 0.0
    for dom, cfg in DOMAINS.items():
        qids = in_contract_qids(cfg)
        seeds = seeds_override if seeds_override is not None else seeds_for(dom)
        cells = len(qids) * len(seeds) * len(BASELINES)
        n_rec, cost = recorded_cost(dom, cfg, qids, seeds)
        grand_cells += cells
        grand_cost += cost
        note = "" if n_rec == cells else f"  (recorded proxy covers {n_rec}/{cells})"
        print(f"  {dom:<9} {len(qids):>3} qids x {len(seeds)} seeds x {len(BASELINES)} sys "
              f"= {cells:>4} cells   recorded-cost ${cost:6.2f}{note}")
    print("-" * 84)
    print(f"  GRAND TOTAL: {grand_cells} cells   est. cost ~${grand_cost:.2f}")
    print("  (proxy = sum of recorded cost_usd over these exact cells; temp 0 leaves tokens/calls ~unchanged)")
    print("=" * 84)
    return grand_cells, grand_cost


def run_parity(seeds_override=None):
    from anthropic import Anthropic                                    # noqa: E402
    from evidence.eval_run.harness import run_cells                    # noqa: E402
    factory = lambda: Anthropic()                                      # noqa: E731
    ran = skipped = 0
    for dom, cfg in DOMAINS.items():
        qids = in_contract_qids(cfg)
        seeds = seeds_override if seeds_override is not None else seeds_for(dom)
        print(f"  [{dom}] {len(qids)} qids x {len(seeds)} seeds x {len(BASELINES)} baselines "
              f"= {len(qids) * len(seeds) * len(BASELINES)} cells at temperature {PARITY_TEMP} ...")
        summary = run_cells(
            qids=qids, seeds=seeds, systems=BASELINES,
            client_factory=factory, model=MODEL,
            temperature=PARITY_TEMP, use_cache=True,          # bounds omitted -> harness DEFAULTS (15/3/60s)
            corpus_path=cfg["corpus"], gold_path=cfg["gold"],
            register_path=cfg["register"], schema_path=cfg["schema"],
            results_path=out_results(dom), provenance_path=out_prov(dom),
        )
        ran += summary.get("ran", 0)
        skipped += summary.get("skipped", 0)
        if summary.get("aborted"):
            print("  ABORTED on a transport/API error — re-run the same command to resume.")
            return False, ran, skipped
    return True, ran, skipped


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="print scope + cost, no API")
    ap.add_argument("--seeds", type=int, nargs="+", default=None,
                    help="override the rep set (e.g. --seeds 0 for a one-rep temp-0 approximation)")
    args = ap.parse_args(argv)

    scope(args.seeds)
    if args.seeds is not None:
        print(f"  WARNING: --seeds {args.seeds} writes ONLY those seed rows. The parity overlay matches by exact")
        print("           (system, qid, seed), so the pipeline would read temp-0 for these seeds and the recorded")
        print("           DEFAULT temperature for the others — a MIXED before/after. Use full reps (omit --seeds)")
        print("           for the supported drop-in; a one-rep run needs a seed-broadcast overlay (not yet wired).")

    if args.dry:
        print("\n--dry: no API calls made. Set ANTHROPIC_API_KEY and drop --dry to run.")
        return 0
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\nERROR: this re-run is LIVE and needs ANTHROPIC_API_KEY. Set it and re-run:")
        print("  export ANTHROPIC_API_KEY=sk-...")
        print("  python scripts/rerun_temp0_parity.py")
        return 2

    ok, ran, skipped = run_parity(args.seeds)
    print(f"\n=== run_cells: {ran} ran, {skipped} skipped"
          f"{' — ABORTED (resume by re-running)' if not ok else ''} ===")
    if ok:
        print("Next: PARITY_TEMP0=1 python scripts/build_paper_numbers.py  &&  "
              "python scripts/report_parity_delta.py")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
