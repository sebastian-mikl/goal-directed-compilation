#!/usr/bin/env python
"""Reverse temperature probe (Direction 2) — LIVE, needs ANTHROPIC_API_KEY.

Direction 1 (scripts/probe_temperature.py) found 13/63 = 20.6% of default-temp
IN-CONTRACT silent-wrong questions flip to CORRECT at temperature 0. That is
ambiguous: a genuine temp-0 reliability gain, OR regression-to-the-mean noise
(unlucky default-temp draws that re-sampling would partly correct in EITHER
direction). To distinguish, measure the OTHER direction: sample cells CORRECT at
default temp, re-emit at temperature 0, and measure the correct→silent-wrong
flip rate.

  - correct→wrong rate COMPARABLE to 20.6%  -> symmetric re-sampling noise; temp 0
    confers no NET advantage; confound DEAD.
  - correct→wrong rate MUCH LOWER than 20.6% -> asymmetric; temp 0 genuinely
    reduces net errors; confound PARTLY REAL -> a full temperature-parity re-run
    of the three baselines is owed (do not claim the confound is dead).

Matched sample (fair comparison): for each baseline, the SAME per-system total as
Direction 1 (react 19, code_agent 27, text_to_sql 17 = 63), drawn from the same
domains and tiers in the same proportion as that system's silent set. Sampling is
tier-stratified: from each (system, domain, tier) we draw as many CORRECT
questions as there were silent ones; where a stratum's correct pool is smaller
than its silent count (notably employee T4: 8 silent, only 7 correct) we take the
whole pool and top up the per-domain deficit at random from that domain's
remaining correct questions. Deterministic: random.Random(SAMPLE_SEED).

This depends on the same base.Adapter._temp_kwargs() wiring as Direction 1
(no-op at the default temperature, so recorded numbers are unaffected).

Usage:
    python scripts/probe_temperature_reverse.py --dry       # matched sample + cost, NO API
    python scripts/probe_temperature_reverse.py             # LIVE run + reverse matrix + verdict
    python scripts/probe_temperature_reverse.py --analyze   # rebuild matrix + verdict from output
Resumable per-domain output files: results_probe_temp0_reverse_{dom}.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                              # noqa: BLE001
    pass

from evidence.eval_run.comparator import score, COMPARATOR_VERSION      # noqa: E402
from probe_temperature import (DOMAINS, _load, BASELINES, IN_SYN,       # noqa: E402
                               BOUNDARY, MODEL, PROBE_TEMP, OUTCOMES)
assert COMPARATOR_VERSION == "s48", COMPARATOR_VERSION

SAMPLE_SEED = 0
# Direction-1 result (scripts/probe_temperature.py), as reported. analyze() prefers
# recomputing from the Direction-1 output files if they are present.
DIR1 = {"react": (3, 19), "code_agent": (8, 27), "text_to_sql": (2, 17)}   # (wrong->correct, N)


def rev_results(dom): return ROOT / f"results/results_probe_temp0_reverse_{dom}.jsonl"
def rev_prov(dom):    return ROOT / f"results/results_probe_temp0_reverse_{dom}.provenance.json"


def _rows_for(dom, cfg):
    rows = []
    if cfg["kind"] == "syn":
        if cfg["t5_split"]:
            for fn in cfg["recorded_nonT5"]:
                rows += [r for r in _load(fn) if r.get("tier") != "T5_high_complexity"]
            for fn in cfg["recorded_T5"]:
                rows += [r for r in _load(fn) if r.get("tier") == "T5_high_complexity"]
        else:
            for fn in cfg["recorded_all"]:
                rows += _load(fn)
    else:
        rows += _load(cfg["recorded"][0])
    return rows


def _buckets(dom, cfg):
    """{system: {'sil': {tier:[qids]}, 'cor': {tier:[qids]}, 'cost': {qid:[costs]}}}.

    Aggregated PER QID across its default-temperature reps (baselines are
    stochastic, so a qid can be silent in some reps and correct in others):
      - a qid is SILENT-at-default if ANY rep scored silent_wrong (matches
        Direction 1's target definition);
      - a qid is a CORRECT-at-default candidate only if NO rep scored
        silent_wrong AND at least one rep scored correct — so the correct pool
        is DISJOINT from the silent set (a mixed qid never lands in both)."""
    gold = json.loads(cfg["gold"].read_text())
    # per (system, qid) -> {tier, outcomes[], costs[]}
    agg = {s: {} for s in BASELINES}
    for r in _rows_for(dom, cfg):
        s = r.get("system")
        if s not in BASELINES:
            continue
        g = gold.get(r["qid"])
        if g is None:
            continue
        tier = r.get("tier") if cfg["kind"] == "syn" else g.get("tier")
        if cfg["kind"] == "syn":
            if tier not in IN_SYN:
                continue
        elif tier in BOUNDARY:
            continue
        oc = score(r.get("raw_result"), g, status=r.get("status", "completed"))[0]
        d = agg[s].setdefault(r["qid"], {"tier": tier, "oc": [], "cost": []})
        d["oc"].append(oc); d["cost"].append(r.get("cost_usd") or 0.0)
    out = {s: {"sil": defaultdict(list), "cor": defaultdict(list), "cost": defaultdict(list)}
           for s in BASELINES}
    for s in BASELINES:
        for qid, d in agg[s].items():
            out[s]["cost"][qid] = d["cost"]
            if "silent_wrong" in d["oc"]:
                out[s]["sil"][d["tier"]].append(qid)
            elif "correct" in d["oc"]:
                out[s]["cor"][d["tier"]].append(qid)
    return out, gold


def select_matched(seed=SAMPLE_SEED):
    """Deterministic tier-stratified, count-matched sample of CORRECT questions.
    Returns (sample {dom:{sys:[qids]}}, cost {dom:{sys:$}}, notes[])."""
    rng = random.Random(seed)
    sample = {dom: {s: [] for s in BASELINES} for dom in DOMAINS}
    cost = {dom: {s: 0.0 for s in BASELINES} for dom in DOMAINS}
    notes = []
    for dom, cfg in DOMAINS.items():
        buckets, _ = _buckets(dom, cfg)
        for s in BASELINES:
            b = buckets[s]
            target = sum(len(v) for v in b["sil"].values())
            if target == 0:
                continue
            picked, same_tier_left, cross_tier = [], [], []
            for tier, silqs in b["sil"].items():           # tier-exact match first
                pool = sorted(b["cor"].get(tier, []))
                rng.shuffle(pool)
                k = len(silqs)
                picked += pool[:k]
                same_tier_left += pool[k:]
                if len(pool) < k:
                    notes.append(f"{s}/{dom}/{tier}: {len(pool)} correct for {k} silent "
                                 f"— took all, topping up cross-tier within {dom}")
            for tier, corqs in b["cor"].items():           # domain top-up pool: non-silent-bearing tiers
                if tier not in b["sil"]:
                    ct = sorted(corqs); rng.shuffle(ct); cross_tier += ct
            if len(picked) < target:                       # prefer same-tier leftovers, then cross-tier
                topup = same_tier_left + cross_tier
                picked += topup[:target - len(picked)]
            picked = sorted(set(picked))
            sample[dom][s] = picked
            if len(picked) < target:                       # domain correct pool genuinely exhausted
                notes.append(f"{s}/{dom}: DOMAIN SHORTFALL — {len(picked)} correct available for "
                             f"{target} silent; sample short by {target - len(picked)}")
            cost[dom][s] = sum((sum(b["cost"][q]) / len(b["cost"][q])) for q in picked if b["cost"].get(q))
    return sample, cost, notes


def print_sample(sample, cost, notes):
    print("=" * 84)
    print(f"REVERSE MATCHED SAMPLE — CORRECT-at-default questions (seed={SAMPLE_SEED})")
    print("=" * 84)
    per_sys = defaultdict(int); grand_cost = 0.0
    for dom in DOMAINS:
        for s in BASELINES:
            qs = sample[dom][s]
            if qs:
                print(f"  {dom:<9} {s:<12} {len(qs):>2} q: {qs}")
                per_sys[s] += len(qs); grand_cost += cost[dom][s]
    print("-" * 84)
    for s in BASELINES:
        print(f"  per-system total: {s:<12} {per_sys[s]} (Direction-1 had {DIR1[s][1]})")
    N = sum(per_sys.values())
    print(f"  GRAND TOTAL: {N} correct questions (one seed each)   est. cost ~${grand_cost:.2f}")
    if notes:
        print("  feasibility notes (stratum correct-pool < silent count):")
        for n in notes:
            print(f"    - {n}")
    print("=" * 84)
    return N


def _dir1_from_files():
    """Recompute Direction-1 (wrong->correct) from its output files if present."""
    per = {s: [0, 0] for s in BASELINES}
    from probe_temperature import target_map, out_results
    tmap, _ = target_map()
    ok = False
    for dom, cfg in DOMAINS.items():
        f = out_results(dom)
        if not f.exists():
            continue
        ok = True
        gold = json.loads(cfg["gold"].read_text())
        rows = {(r["system"], r["qid"]): r for r in _load(f.name)}
        for s in BASELINES:
            for q in tmap[dom][s]:
                r = rows.get((s, q))
                if r is None:
                    continue
                per[s][1] += 1
                if score(r.get("raw_result"), gold[q], status=r.get("status", "completed"))[0] == "correct":
                    per[s][0] += 1
    return (per, ok)


def run_probe(sample):
    from anthropic import Anthropic
    from evidence.eval_run.harness import run_cells
    factory = lambda: Anthropic()                          # noqa: E731
    ran = skipped = 0
    for dom, cfg in DOMAINS.items():
        for s in BASELINES:
            qids = sample[dom][s]
            if not qids:
                continue
            print(f"  [{dom}/{s}] {len(qids)} CORRECT questions at temperature {PROBE_TEMP} ...")
            summ = run_cells(qids=qids, seeds=[0], systems=[s],
                             client_factory=factory, model=MODEL,
                             temperature=PROBE_TEMP, use_cache=True,
                             corpus_path=cfg["corpus"], gold_path=cfg["gold"],
                             register_path=cfg["register"], schema_path=cfg["schema"],
                             results_path=rev_results(dom), provenance_path=rev_prov(dom))
            ran += summ.get("ran", 0); skipped += summ.get("skipped", 0)
            if summ.get("aborted"):
                print("  ABORTED — re-run to resume.")
                return False, ran, skipped
    return True, ran, skipped


def analyze(sample):
    print("\n" + "=" * 84)
    print("REVERSE FLIP MATRIX — temperature-0 re-emission of CORRECT-at-default questions")
    print("=" * 84)
    per_sys = {s: defaultdict(int) for s in BASELINES}
    missing = 0
    for dom, cfg in DOMAINS.items():
        gold = json.loads(cfg["gold"].read_text())
        f = rev_results(dom)
        rows = {(r["system"], r["qid"]): r for r in _load(f.name)} if f.exists() else {}
        for s in BASELINES:
            for q in sample[dom][s]:
                r = rows.get((s, q))
                if r is None:
                    per_sys[s]["MISSING"] += 1; missing += 1; continue
                oc = score(r.get("raw_result"), gold[q], status=r.get("status", "completed"))[0]
                per_sys[s][oc] += 1
    print(f"{'system':<12}{'N':>4}" + "".join(f"{o[:8]:>10}" for o in OUTCOMES))
    pooled = defaultdict(int); N = 0
    for s in BASELINES:
        n = sum(per_sys[s].values())
        print(f"{s:<12}{n:>4}" + "".join(f"{per_sys[s].get(o,0):>10}" for o in OUTCOMES))
        N += n
        for o in OUTCOMES:
            pooled[o] += per_sys[s].get(o, 0)
    print("-" * 84)
    print(f"{'POOLED':<12}{N:>4}" + "".join(f"{pooled.get(o,0):>10}" for o in OUTCOMES))
    if missing:
        print(f"\n  {missing} cell(s) not in output yet — finish the live run before reading the verdict.")
        return
    cw = pooled.get("silent_wrong", 0)                      # correct -> silent_wrong

    # Direction 1 (prefer files; fall back to reported constants)
    d1_per, ok = _dir1_from_files()
    if ok and sum(v[1] for v in d1_per.values()):
        wc = sum(v[0] for v in d1_per.values()); N1 = sum(v[1] for v in d1_per.values())
        src = "recomputed from Direction-1 output files"
    else:
        wc = sum(v[0] for v in DIR1.values()); N1 = sum(v[1] for v in DIR1.values())
        src = "from the reported Direction-1 matrix (output files not present here)"

    print("\n" + "=" * 84)
    print("SIDE BY SIDE — the number that settles it")
    print("=" * 84)
    r_wc = 100 * wc / N1 if N1 else 0
    r_cw = 100 * cw / N if N else 0
    print(f"  Direction 1  silent-wrong -> correct : {wc}/{N1} = {r_wc:.1f}%   ({src})")
    print(f"  Direction 2  correct -> silent-wrong : {cw}/{N} = {r_cw:.1f}%")
    net_cells = wc - cw
    net_frac = 100 * net_cells / ((N1 + N) / 2) if (N1 + N) else 0
    print(f"\n  NET (sample-level) = (wrong->correct) - (correct->wrong) = {wc} - {cw} = {net_cells} cells")
    print(f"                      = {net_frac:+.1f}% of the ~{round((N1+N)/2)} matched base")
    print("\n  Population caveat: correct cells vastly outnumber silent cells in-contract, so even")
    print("  symmetric per-cell rates imply temp 0 does NOT lower (and may slightly raise) the")
    print("  reported silent-error RATE. The rate comparison above is the fair, like-for-like test.")

    print("\n  VERDICT:")
    if r_cw >= 0.5 * r_wc:
        print(f"    Correct->wrong ({r_cw:.1f}%) is COMPARABLE to wrong->correct ({r_wc:.1f}%): symmetric")
        print("    re-sampling noise, net ~0. CONFOUND DEAD — temp 0 confers no net advantage.")
    elif r_cw <= 0.25 * r_wc:
        print(f"    Correct->wrong ({r_cw:.1f}%) is MUCH LOWER than wrong->correct ({r_wc:.1f}%): asymmetric.")
        print("    CONFOUND (PARTLY) REAL -> STOP: a full temperature-parity re-run of the three")
        print("    baselines is owed (~$2.88 full-reps / ~$1.03 one-rep, earlier estimate). Do NOT")
        print("    claim the confound is dead.")
    else:
        print(f"    BORDERLINE: correct->wrong ({r_cw:.1f}%) vs wrong->correct ({r_wc:.1f}%) — small but")
        print("    nonzero asymmetry. Report as borderline; decide framing with a human, don't force it.")
    print("=" * 84)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    args = ap.parse_args(argv)
    sample, cost, notes = select_matched()
    print_sample(sample, cost, notes)
    if args.analyze:
        analyze(sample); return 0
    if args.dry:
        print("\n--dry: no API calls made.")
        return 0
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\nERROR: LIVE probe needs ANTHROPIC_API_KEY. export it and re-run.")
        return 2
    ok, ran, skipped = run_probe(sample)
    print(f"\n=== run_cells: {ran} ran, {skipped} skipped{'' if ok else ' — ABORTED (resume by re-running)'} ===")
    if ok:
        analyze(sample)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
