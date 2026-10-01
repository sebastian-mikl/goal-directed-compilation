#!/usr/bin/env python
"""Decisions 3/4: fill the risk-T5 hole for code_agent + text_to_sql.

The risk domain's T5_high_complexity tier was only ever run for network, planner,
and react (results_t5.rescored.jsonl). code_agent and text_to_sql have NO risk-T5
data, so the risk in-contract cross-system table has uneven denominators (415 for
the three systems that have T5, 340 for the two that don't). This run fills that
hole so all five systems have uniform 415-cell risk in-contract coverage.

SCOPE (deliberately narrow):
  domain  = risk (eval_corpus.json / gold.json / environment register+schema)
  tier    = T5_high_complexity only (qids t5_01 .. t5_15)
  systems = code_agent, text_to_sql   (the two missing; network/planner/react stay)
  reps    = 0-4 (5 repetitions, matching results_t5's shape)
  model   = claude-sonnet-4-5, temperature 0   (baseline config; NO emission-model
            override — that knob is network/planner-only and unused here)
That's 15 qids x 5 reps x 2 systems = 150 (system, qid, rep) cells.

Writes a NEW file results_risk_t5_missing.jsonl (never overwrites results_t5).
RESUMABLE: run_cells skips any (system, qid, rep) already logged — re-run the same
command to resume after a transport error; completed cells are not re-paid.

IMPORTANT — config parity: confirm claude-sonnet-4-5 / temperature 0 matches the
config the ORIGINAL risk baselines (results_off.rescored.jsonl) used for
code_agent/text_to_sql, so the new T5 cells are comparable to their T1-T4 cells.
The provenance file records the config used.

Needs ANTHROPIC_API_KEY:

    python scripts/rerun_risk_t5_missing.py
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

from evidence.eval_run.harness import DEFAULTS, load_corpus, load_gold  # noqa: E402

TIER = "T5_high_complexity"
SYSTEMS = ["code_agent", "text_to_sql"]
SEEDS = [0, 1, 2, 3, 4]
MODEL = "claude-sonnet-4-5"
TEMPERATURE = 0
OUT = ROOT / "results/results_risk_t5_missing.jsonl"
PROV = ROOT / "results/results_risk_t5_missing.provenance.json"

def t5_qids():
    gold = load_gold(DEFAULTS["gold_path"])
    return sorted(q for q, g in gold.items() if g.get("tier") == TIER)

def main(argv=None):
    dry = "--dry-check" in (argv or sys.argv[1:])
    qids = t5_qids()
    n_cells = len(qids) * len(SEEDS) * len(SYSTEMS)
    print(f"risk-T5 fill: {len(qids)} qids x {len(SEEDS)} reps x {len(SYSTEMS)} systems "
          f"= {n_cells} cells -> {OUT.name}")
    print(f"  qids: {qids}")
    print(f"  systems: {SYSTEMS}  model: {MODEL}  temperature: {TEMPERATURE}")

    # cost estimate from the systems' existing risk-T4 usage (proxy; T5 tends higher)
    off = [json.loads(l) for l in (ROOT / "results/results_off.rescored.jsonl").read_text().splitlines() if l.strip()]
    from evidence.eval_run.pricing import cost_usd
    print("\n  COST ESTIMATE (from each system's risk-T4 usage as a proxy; T5 is deeper,")
    print("  so treat as a LOWER bound — budget ~1.3-1.5x):")
    total_lo = 0.0
    for s in SYSTEMS:
        t4 = [r for r in off if r["system"] == s and r["tier"] == "T4_business_rule_multi_op"]
        if not t4:
            print(f"    {s}: no T4 proxy found"); continue
        import statistics
        ain = statistics.mean(r.get("input_tokens", 0) or 0 for r in t4)
        aout = statistics.mean(r.get("output_tokens", 0) or 0 for r in t4)
        per = cost_usd(MODEL, int(ain), int(aout)) if _cost_takes_3(cost_usd) else None
        cells = len(qids) * len(SEEDS)
        est = (per or _rough(ain, aout)) * cells
        total_lo += est
        print(f"    {s:12} ~{ain:.0f} in / {aout:.0f} out per query x {cells} = ~${est:.2f}")
    print(f"    ---- lower-bound total for both systems: ~${total_lo:.2f}  (budget ~${total_lo*1.4:.2f})")

    if dry:
        print("\n[DRY-CHECK] corpus/gold load + qid selection only; no client constructed.")
        corpus = load_corpus(DEFAULTS["corpus_path"]); gold = load_gold(DEFAULTS["gold_path"])
        missing = [q for q in qids if q not in gold or q not in corpus]
        print(f"  corpus+gold resolve all {len(qids)} T5 qids: {'OK' if not missing else 'MISSING '+str(missing)}")
        if not os.getenv("ANTHROPIC_API_KEY"):
            print("  ANTHROPIC_API_KEY not set — dry-check only, as expected.")
        return 0

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\nANTHROPIC_API_KEY not set — refusing to run live. (Use --dry-check to validate.)")
        return 1
    from anthropic import Anthropic
    from evidence.eval_run.harness import run_cells
    summary = run_cells(
        qids=qids, seeds=SEEDS, systems=SYSTEMS,
        client_factory=lambda: Anthropic(), model=MODEL, temperature=TEMPERATURE, use_cache=True,
        corpus_path=DEFAULTS["corpus_path"], gold_path=DEFAULTS["gold_path"],
        register_path=DEFAULTS["register_path"], schema_path=DEFAULTS["schema_path"],
        results_path=OUT, provenance_path=PROV)
    print(f"\n{summary['ran']} ran, {summary['skipped']} skipped"
          f"{' — ABORTED (re-run to resume)' if summary.get('aborted') else ''}")
    return 1 if summary.get("aborted") else 0

def _cost_takes_3(fn):
    import inspect
    try:
        return len(inspect.signature(fn).parameters) >= 3
    except (TypeError, ValueError):
        return False

def _rough(ain, aout):
    return ain / 1e6 * 3.0 + aout / 1e6 * 15.0  # Sonnet-ish $3/M in, $15/M out fallback

if __name__ == "__main__":
    sys.exit(main())
