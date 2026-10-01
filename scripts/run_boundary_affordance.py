#!/usr/bin/env python
"""Boundary-tier decline-affordance experiment — LIVE, needs ANTHROPIC_API_KEY.

The reviewer's remaining ask: A.4/A.5 (the Spider boundary tiers) are GDC-only.
Now that the tables show the baselines CAN decline, run the four baselines on the
boundary tiers WITH an explicit, uniform, non-leading decline affordance and ask
whether a VERBAL affordance produces reliable declines, or whether GDC's STRUCTURAL
refusal declines more reliably.

Affordance: refuse_config.decline_affordance() (toggle DECLINE_AFFORDANCE=1), a
general "if you cannot answer correctly, for any reason, you may decline —
CANNOT_ANSWER + reason" line, phrased per output format, applied UNIFORMLY to every
question (no tier is named). react and planner get it added here (they had none in
the main run); code_agent and text_to_sql already carry an equivalent always-on
inline affordance, so they are re-run unchanged for temperature-0 parity.

Two boundary tiers are NOT the same experiment (see the design report):
  * out_of_scope_register (46 q): needs an EXCLUDED TABLE — genuinely unanswerable
    for every system (no gold answer). A correct decline is the right behavior for
    all four. This is the clean, uniform, acceptance-deciding comparison vs GDC's A.4.
  * out_of_contract_aggregate (12 q): needs multi-aggregate — out of contract ONLY
    for the four-primitive systems (GDC, planner, react); a general SQL/pandas system
    (text_to_sql, code_agent) CAN answer it and has a REAL gold answer, so for them a
    decline is a false under-answer, NOT correct behavior. Scored against gold; read
    per system (see the report — this is the flagged asymmetry, not a bug).

False-decline check: the same affordance re-run on the in-contract ANSWER tier for
react + planner (the two receiving the new affordance) — do they start declining
answerable queries? code_agent/text_to_sql's in-contract-with-affordance behavior is
already recorded (their affordance is always on) at temperature 0 in the parity run.

Scope + cost (recorded-cost proxy; temperature 0 does not change tokens/calls):
  boundary        58 q x 4 baselines x 5 reps = 1160 cells   ~ $25.8
  false-decline   27 q x 2 (react,planner) x 5 reps = 270 cells   ~ $3
  TOTAL ~ 1430 cells, ~ $29 (smaller than the temperature-parity re-run).

Everything else identical to the recorded runs: same prompts (plus the affordance),
harness, model claude-sonnet-4-5, bounds (15/3/60s), prompt caching, temperature 0.

Usage:
    python scripts/run_boundary_affordance.py --dry   # scope + cost, NO API
    python scripts/run_boundary_affordance.py         # LIVE run (per-domain files)
Resumable: run_cells skips any (system, qid, seed) already present.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                      # noqa: BLE001
    pass

from evidence.eval_run.comparator import COMPARATOR_VERSION            # noqa: E402
assert COMPARATOR_VERSION == "s48", COMPARATOR_VERSION

SPIDER = ["wta_1", "world_1", "bike_1"]
BOUNDARY = {"out_of_scope_register", "out_of_contract_aggregate"}
BASELINES = ["planner", "react", "code_agent", "text_to_sql"]
FALSE_DECLINE_SYS = ["planner", "react"]     # the two the affordance is NEW for
MODEL = "claude-sonnet-4-5"
TEMP = 0
SEEDS = [0, 1, 2, 3, 4]


def P(*a):
    return ROOT.joinpath(*a)


DOMAINS = {
    "wta_1": {"corpus": P("eval_corpus_spider_wta_1.json"), "gold": P("evidence", "gold", "gold_spider_wta_1.json"),
              "register": P("wta_1", "matches.pkl"), "schema": P("wta_1", "schema.json"),
              "recorded": "results_spider_wta_1.jsonl"},
    "world_1": {"corpus": P("eval_corpus_spider_world_1.json"), "gold": P("evidence", "gold", "gold_spider_world_1.json"),
                "register": P("world_1", "country.pkl"), "schema": P("world_1", "schema.json"),
                "recorded": "results_spider_world_1.jsonl"},
    "bike_1": {"corpus": P("eval_corpus_spider_bike_1.json"), "gold": P("evidence", "gold", "gold_spider_bike_1.json"),
               "register": P("bike_1", "trip.pkl"), "schema": P("bike_1", "schema.json"),
               "recorded": "results_spider_bike_1.jsonl"},
}


def out_boundary(dom):
    return ROOT / f"results/results_boundary_affordance_{dom}.jsonl"


def out_falsedecline(dom):
    return ROOT / f"results/results_falsedecline_{dom}.jsonl"


def _load(fn):
    p = ROOT / "results" / fn if not isinstance(fn, Path) else fn
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def boundary_qids(cfg):
    gold = json.loads(cfg["gold"].read_text())
    return sorted(q for q, g in gold.items() if g.get("tier") in BOUNDARY)


def incontract_qids(cfg):
    gold = json.loads(cfg["gold"].read_text())
    return sorted(q for q, g in gold.items() if g.get("tier") not in BOUNDARY)


def recorded_cost(cfg, qids, systems):
    """recorded cost_usd proxy over the exact cells (baselines, these qids, all seeds)."""
    qset = set(qids)
    n = 0
    cost = 0.0
    for r in _load(cfg["recorded"]):
        if r.get("system") in systems and r.get("qid") in qset:
            cost += r.get("cost_usd") or 0.0
            n += 1
    return n, cost


def scope():
    print("=" * 88)
    print("BOUNDARY-TIER DECLINE-AFFORDANCE EXPERIMENT — 4 baselines, temperature 0, DECLINE_AFFORDANCE=1")
    print("=" * 88)
    gb_cells = gb_cost = 0.0
    print("  -- boundary tiers (all 4 baselines) --")
    for dom, cfg in DOMAINS.items():
        qids = boundary_qids(cfg)
        cells = len(qids) * len(SEEDS) * len(BASELINES)
        n, cost = recorded_cost(cfg, qids, BASELINES)
        gb_cells += cells
        gb_cost += cost
        print(f"    {dom:<9} {len(qids):>2} boundary q x {len(SEEDS)} seeds x {len(BASELINES)} sys = {cells:>4} cells   ~${cost:5.2f}")
    fd_cells = fd_cost = 0.0
    print("  -- false-decline check (in-contract answer tier, react+planner) --")
    for dom, cfg in DOMAINS.items():
        qids = incontract_qids(cfg)
        cells = len(qids) * len(SEEDS) * len(FALSE_DECLINE_SYS)
        n, cost = recorded_cost(cfg, qids, FALSE_DECLINE_SYS)
        fd_cells += cells
        fd_cost += cost
        print(f"    {dom:<9} {len(qids):>2} answer q x {len(SEEDS)} seeds x {len(FALSE_DECLINE_SYS)} sys = {cells:>4} cells   ~${cost:5.2f}")
    print("-" * 88)
    print(f"  boundary       {int(gb_cells):>4} cells  ~${gb_cost:5.2f}")
    print(f"  false-decline  {int(fd_cells):>4} cells  ~${fd_cost:5.2f}")
    print(f"  GRAND TOTAL    {int(gb_cells + fd_cells):>4} cells  ~${gb_cost + fd_cost:5.2f}")
    print("=" * 88)


def run():
    from anthropic import Anthropic                                    # noqa: E402
    from evidence.eval_run.harness import run_cells                    # noqa: E402
    os.environ["DECLINE_AFFORDANCE"] = "1"                             # the affordance toggle (read at call time)
    factory = lambda: Anthropic()                                      # noqa: E731
    ran = skipped = 0

    def _do(dom, cfg, qids, systems, out_path, label):
        nonlocal ran, skipped
        if not qids:
            return True
        print(f"  [{dom}/{label}] {len(qids)} q x {len(SEEDS)} seeds x {len(systems)} sys, temp {TEMP} ...")
        s = run_cells(qids=qids, seeds=SEEDS, systems=systems, client_factory=factory, model=MODEL,
                      temperature=TEMP, use_cache=True, corpus_path=cfg["corpus"], gold_path=cfg["gold"],
                      register_path=cfg["register"], schema_path=cfg["schema"],
                      results_path=out_path, provenance_path=out_path.with_suffix(".provenance.json"))
        ran += s.get("ran", 0)
        skipped += s.get("skipped", 0)
        if s.get("aborted"):
            print("  ABORTED on a transport/API error — re-run the same command to resume.")
            return False
        return True

    for dom, cfg in DOMAINS.items():
        if not _do(dom, cfg, boundary_qids(cfg), BASELINES, out_boundary(dom), "boundary"):
            return False, ran, skipped
    for dom, cfg in DOMAINS.items():
        if not _do(dom, cfg, incontract_qids(cfg), FALSE_DECLINE_SYS, out_falsedecline(dom), "false-decline"):
            return False, ran, skipped
    return True, ran, skipped


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="print scope + cost, no API")
    args = ap.parse_args(argv)
    scope()
    if args.dry:
        print("\n--dry: no API calls made. Set ANTHROPIC_API_KEY and drop --dry to run.")
        return 0
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\nERROR: this run is LIVE and needs ANTHROPIC_API_KEY. Set it and re-run:")
        print("  export ANTHROPIC_API_KEY=sk-...\n  python scripts/run_boundary_affordance.py")
        return 2
    ok, ran, skipped = run()
    print(f"\n=== run_cells: {ran} ran, {skipped} skipped{' — ABORTED (resume by re-running)' if not ok else ''} ===")
    if ok:
        print("Next: python scripts/score_boundary_affordance.py")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
