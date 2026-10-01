#!/usr/bin/env python
"""Temperature-confound probe (Probe B) — LIVE, needs ANTHROPIC_API_KEY.

The reviewer's confound: "GDC and planner emit at temperature 0 while the three
unconstrained baselines (react, code_agent, text_to_sql) run at the API default;
temperature 0 may suppress errors, flattering GDC." This probes that hypothesis
in the direction it was raised: take every distinct IN-CONTRACT question a
default-temperature baseline currently gets SILENT-WRONG, re-emit it ONCE at
temperature 0 (everything else identical — same prompts, harness, model
claude-sonnet-4-5, s47 scoring), and count how many flip to CORRECT.

  - Confound FALSE  -> few flips: these are grounding/architectural errors
    temperature does not touch; the temperature asymmetry cannot explain GDC's
    lower silent-error rate.
  - Confound TRUE   -> many flips: temperature is material; report it straight —
    the comparison then needs a full temperature-matched re-run.

Target set (computed live from the recorded results + current gold, s47; 63 cells):
react 19, code_agent 27, text_to_sql 17 distinct (system, question, domain).
Each re-run ONCE (seed 0) — temperature 0 is near-deterministic, so one emission
per question is sufficient to see whether it flips. Estimated cost ~ $1 (Probe B).

This depends on base.Adapter._temp_kwargs() threading `temperature` into the
baseline messages.create calls (added in the same change as this script); at the
default temperature=None it is a no-op, so recorded numbers are unaffected.

Usage:
    python scripts/probe_temperature.py --dry        # target + cost only, NO API
    python scripts/probe_temperature.py              # LIVE run (per-domain files) + flip matrix
    python scripts/probe_temperature.py --analyze    # (re)build the flip matrix from existing output
Resumable: run_cells skips any (system, qid, seed) already in the per-domain
output file, so a re-run resumes and never double-charges.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:                                   # pick up ANTHROPIC_API_KEY from .env, if present
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                      # noqa: BLE001 — .env is optional
    pass

from evidence.eval_run.comparator import score, COMPARATOR_VERSION   # noqa: E402
assert COMPARATOR_VERSION == "s48", COMPARATOR_VERSION

BASELINES = ["react", "code_agent", "text_to_sql"]     # the three default-temperature systems
MODEL = "claude-sonnet-4-5"
PROBE_TEMP = 0
IN_SYN = {"T1_single_op", "T2_two_ops", "T3_three_ops",
          "T4_business_rule_multi_op", "T5_high_complexity", "house_vocab"}
BOUNDARY = {"out_of_scope_register", "out_of_contract_aggregate"}
OUTCOMES = ["correct", "silent_wrong", "refused_should_answer", "crash", "timeout"]

# domain -> files. `recorded` maps a tier-selector to the baseline result file(s)
# used ONLY to pick the target cells (which are currently silent-wrong); the
# corpus/register/schema/gold are the live-run inputs. Risk splits T5 from the
# rest to match build_paper_numbers.py's provenance exactly.
def P(*a): return ROOT.joinpath(*a)
DOMAINS = {
    "risk": {
        "kind": "syn", "t5_split": True,       # canonical: non-T5 from results_off, T5 from results_t5(+fill)
        "corpus": P("eval_corpus.json"), "gold": P("evidence", "gold", "gold.json"),
        "register": P("environment", "register.pkl"), "schema": P("environment", "schema.json"),
        "recorded_nonT5": ["results_off.rescored.jsonl"],
        "recorded_T5": ["results_t5.rescored.jsonl", "results_risk_t5_missing.jsonl"],
    },
    "employee": {
        "kind": "syn", "t5_split": False,      # one file holds every tier, incl. T5
        "corpus": P("eval_corpus_employee.json"), "gold": P("evidence", "gold", "gold_employee.json"),
        "register": P("probe_domain", "employees.pkl"), "schema": P("probe_domain", "schema.json"),
        "recorded_all": ["results_employee.jsonl"],
    },
    "wta_1": {"kind": "spider", "db": "wta_1",
              "corpus": P("eval_corpus_spider_wta_1.json"), "gold": P("evidence", "gold", "gold_spider_wta_1.json"),
              "register": P("wta_1", "matches.pkl"), "schema": P("wta_1", "schema.json"),
              "recorded": ["results_spider_wta_1.jsonl"]},
    "world_1": {"kind": "spider", "db": "world_1",
                "corpus": P("eval_corpus_spider_world_1.json"), "gold": P("evidence", "gold", "gold_spider_world_1.json"),
                "register": P("world_1", "country.pkl"), "schema": P("world_1", "schema.json"),
                "recorded": ["results_spider_world_1.jsonl"]},
    "bike_1": {"kind": "spider", "db": "bike_1",
               "corpus": P("eval_corpus_spider_bike_1.json"), "gold": P("evidence", "gold", "gold_spider_bike_1.json"),
               "register": P("bike_1", "trip.pkl"), "schema": P("bike_1", "schema.json"),
               "recorded": ["results_spider_bike_1.jsonl"]},
}

def out_results(dom): return ROOT / f"results/results_probe_temp0_{dom}.jsonl"
def out_prov(dom):    return ROOT / f"results/results_probe_temp0_{dom}.provenance.json"

def _load(fn):
    p = ROOT / "results" / fn
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def target_map():
    """{domain: {system: [silent-wrong in-contract qids]}} + per-cell recorded cost.
    Uses the CURRENT gold + s47 — i.e. the same scoring the paper reports."""
    tmap, cost = {}, {}
    for dom, cfg in DOMAINS.items():
        gold = json.loads(cfg["gold"].read_text())
        # gather recorded baseline rows with the canonical per-tier source
        rows = []
        if cfg["kind"] == "syn":
            if cfg["t5_split"]:                # risk: T5 emissions live in a separate file
                for fn in cfg["recorded_nonT5"]:
                    rows += [r for r in _load(fn) if r.get("tier") != "T5_high_complexity"]
                for fn in cfg["recorded_T5"]:
                    rows += [r for r in _load(fn) if r.get("tier") == "T5_high_complexity"]
            else:                              # employee: one file holds every tier
                for fn in cfg["recorded_all"]:
                    rows += _load(fn)
        else:
            rows += [r for r in _load(cfg["recorded"][0])]
        per_sys = {s: {} for s in BASELINES}     # qid -> [cost_usd of its silent reps]
        for r in rows:
            s = r.get("system")
            if s not in BASELINES:
                continue
            g = gold.get(r["qid"])
            if g is None:
                continue
            if cfg["kind"] == "syn":
                if r.get("tier") not in IN_SYN:
                    continue
            else:
                if g.get("tier") in BOUNDARY:
                    continue
            if score(r.get("raw_result"), g, status=r.get("status", "completed"))[0] == "silent_wrong":
                per_sys[s].setdefault(r["qid"], []).append(r.get("cost_usd") or 0.0)
        tmap[dom] = {s: sorted(per_sys[s]) for s in BASELINES}
        cost[dom] = {s: {q: (sum(v) / len(v)) for q, v in per_sys[s].items()} for s in BASELINES}
    return tmap, cost


def print_targets(tmap, cost):
    print("=" * 84)
    print("TARGET SET — in-contract silent-wrong questions, one temp-0 re-emission each")
    print("=" * 84)
    grand_q = 0
    grand_cost = 0.0
    per_system_q = defaultdict(int)
    for dom in DOMAINS:
        for s in BASELINES:
            qs = tmap[dom][s]
            if qs:
                print(f"  {dom:<9} {s:<12} {len(qs):>2} q: {qs}")
                grand_q += len(qs); per_system_q[s] += len(qs)
                grand_cost += sum(cost[dom][s].values())
    print("-" * 84)
    for s in BASELINES:
        print(f"  per-system total: {s:<12} {per_system_q[s]} questions")
    print(f"  GRAND TOTAL: {grand_q} questions (one seed each)   est. cost ~${grand_cost:.2f}")
    print("  Thin per-domain slices (report but don't read alone): "
          "react/risk, text_to_sql/spider.")
    print("=" * 84)
    return grand_q


def run_probe(tmap):
    from anthropic import Anthropic                                # noqa: E402
    from evidence.eval_run.harness import run_cells                # noqa: E402
    factory = lambda: Anthropic()                                  # noqa: E731
    ran = skipped = 0
    for dom, cfg in DOMAINS.items():
        for s in BASELINES:
            qids = tmap[dom][s]
            if not qids:
                continue
            print(f"  [{dom}/{s}] {len(qids)} questions at temperature {PROBE_TEMP} ...")
            summary = run_cells(
                qids=qids, seeds=[0], systems=[s],
                client_factory=factory, model=MODEL,
                temperature=PROBE_TEMP, use_cache=True,
                corpus_path=cfg["corpus"], gold_path=cfg["gold"],
                register_path=cfg["register"], schema_path=cfg["schema"],
                results_path=out_results(dom), provenance_path=out_prov(dom),
            )
            ran += summary.get("ran", 0); skipped += summary.get("skipped", 0)
            if summary.get("aborted"):
                print("  ABORTED on a transport/API error — re-run the same command to resume.")
                return False, ran, skipped
    return True, ran, skipped


def analyze(tmap):
    """Flip matrix: of each system's target questions (all silent-wrong before),
    how many are correct / still silent / other at temperature 0."""
    print("\n" + "=" * 84)
    print("FLIP MATRIX — temperature-0 re-emission of in-contract silent-wrong questions")
    print("=" * 84)
    per_sys = {s: defaultdict(int) for s in BASELINES}
    missing = 0
    for dom, cfg in DOMAINS.items():
        gold = json.loads(cfg["gold"].read_text())
        rows = {(r["system"], r["qid"]): r for r in _load(out_results(dom).name)}
        for s in BASELINES:
            for q in tmap[dom][s]:
                r = rows.get((s, q))
                if r is None:
                    per_sys[s]["MISSING"] += 1; missing += 1; continue
                oc = score(r.get("raw_result"), gold[q], status=r.get("status", "completed"))[0]
                per_sys[s][oc] += 1
    hdr = f"{'system':<12}{'N':>4}" + "".join(f"{o[:8]:>10}" for o in OUTCOMES)
    print(hdr)
    pooled = defaultdict(int); N = 0
    for s in BASELINES:
        n = sum(v for v in per_sys[s].values())
        row = f"{s:<12}{n:>4}" + "".join(f"{per_sys[s].get(o,0):>10}" for o in OUTCOMES)
        print(row); N += n
        for o in OUTCOMES: pooled[o] += per_sys[s].get(o, 0)
    print("-" * 84)
    print(f"{'POOLED':<12}{N:>4}" + "".join(f"{pooled.get(o,0):>10}" for o in OUTCOMES))
    if missing:
        print(f"\n  NOTE: {missing} target cell(s) not yet in the probe output — run/finish the live probe first.")
    if N:
        flip = pooled.get("correct", 0)
        print(f"\n  FLIP FRACTION: {flip}/{N} = {100*flip/N:.1f}% of silent-wrong questions flipped to CORRECT at temp 0.")
        print("  Interpretation guide: <20% -> confound does NOT explain GDC's lower silent-error;")
        print("                        high  -> temperature IS material — STOP and flag for a full parity re-run.")
    print("=" * 84)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="print target + cost, no API")
    ap.add_argument("--analyze", action="store_true", help="rebuild flip matrix from existing output")
    args = ap.parse_args(argv)

    tmap, cost = target_map()
    n = print_targets(tmap, cost)
    if n != 63:
        print(f"  NOTE: target is {n}, not the expected 63 — the recorded results/gold may have changed; proceeding with what's there.")

    if args.analyze:
        analyze(tmap); return 0
    if args.dry:
        print("\n--dry: no API calls made. Set ANTHROPIC_API_KEY and drop --dry to run.")
        return 0
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\nERROR: this probe is LIVE and needs ANTHROPIC_API_KEY. Set it and re-run:")
        print("  export ANTHROPIC_API_KEY=sk-...")
        print("  python scripts/probe_temperature.py")
        return 2

    ok, ran, skipped = run_probe(tmap)
    print(f"\n=== run_cells: {ran} ran, {skipped} skipped{' — ABORTED (resume by re-running)' if not ok else ''} ===")
    if ok:
        analyze(tmap)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
