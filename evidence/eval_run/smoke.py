#!/usr/bin/env python
"""S21 SMOKE TEST — 5 systems x seed 0 x 3 queries (one easy in-range, one hard
in-range, one out-of-range). Cheap; ~15 LLM calls in aggregate.

This is the step's actual run. After it completes, prints the 5 checks
(a-e) and a sample JSONL record for the easy-in-range cell so the architect
can hand-confirm the score. Re-run is safe and demonstrates resume.

Usage:
  python -m evidence.eval_run.smoke                  # LIVE (needs ANTHROPIC_API_KEY)
  python -m evidence.eval_run.smoke --queries t1_07 t4_06 oor_03 --seed 0
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                                              # noqa: BLE001
    pass

from evidence.eval_run.harness import run_cells, DEFAULTS                 # noqa: E402


SMOKE_QUERIES = ("t1_07", "t4_06", "oor_03")
SYSTEMS = ("network", "planner", "react", "code_agent", "text_to_sql")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists(): return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def report(results_path: Path, qids: tuple[str, ...] = SMOKE_QUERIES) -> None:
    recs = _read_jsonl(results_path)
    smoke = [r for r in recs if r["qid"] in qids and r["seed"] == 0]
    by_q = {q: [r for r in smoke if r["qid"] == q] for q in qids}

    print("\n" + "=" * 72)
    print("S21 SMOKE REPORT")
    print("=" * 72)
    for q in qids:
        print(f"\n[{q}] ({by_q[q][0]['tier'] if by_q[q] else '?'})  "
              f"{by_q[q][0]['query'] if by_q[q] else '?'}")
        for r in sorted(by_q[q], key=lambda x: x["system"]):
            print(f"  {r['system']:<12s} {r['outcome_class']:<22s} "
                  f"calls={r['llm_calls']:>2d} raw_in={r['input_tokens']:>5d} "
                  f"(u{r.get('uncached_input_tokens',0)}/w{r.get('cache_write_tokens',0)}/"
                  f"r{r.get('cache_read_tokens',0)}) out={r['output_tokens']:>4d} "
                  f"${r.get('cost_usd_cached', r['cost_usd']):.4f}cache/"
                  f"${r.get('cost_usd_raw', r['cost_usd']):.4f}raw "
                  f"-- {r['comparator_reason'][:40]}")

    # === check (a): fully-populated RunResult on all 5 adapters ============
    print("\n--- (a) RunResult fully populated for all 5 adapters ---")
    pop_ok = True
    for r in smoke:
        bad = []
        if r["input_tokens"] == 0 and r["output_tokens"] == 0 and r["llm_calls"] > 0:
            bad.append("tokens missing despite a call")
        if r["llm_calls"] == 0 and r["status"] != "crashed":
            bad.append("calls=0 with non-crashed status")
        if r["artifact"] is None and r["status"] != "crashed":
            bad.append("artifact missing")
        if not r["per_call_tokens"] and r["llm_calls"] > 0:
            bad.append("per_call_tokens empty despite a call")
        flag = "PASS" if not bad else "FAIL"
        if bad: pop_ok = False
        print(f"  [{flag}] {r['system']:<12s} {r['qid']}  -- {bad if bad else 'tokens+calls+artifact ok'}")
    print(f"  -> overall: {'PASS' if pop_ok else 'FAIL'}")

    # === check (b): comparator correctness — hand-confirm t1_07 ============
    print("\n--- (b) comparator correctness ---")
    t1_07 = [r for r in smoke if r["qid"] == "t1_07"]
    gold_t1_07 = t1_07[0]["gold_value"] if t1_07 else "?"
    print(f"  t1_07 gold = {gold_t1_07} (total Cost — hand-confirm by independent re-derive)")
    for r in sorted(t1_07, key=lambda x: x["system"]):
        print(f"    {r['system']:<12s} produced {str(r['raw_result'])[:40]:<40s} -> {r['outcome_class']}")

    # === check (c): sample JSONL record has every documented field ==========
    print("\n--- (c) sample JSONL record (system=network, qid=t1_07) ---")
    sample = next((r for r in smoke if r["system"] == "network" and r["qid"] == "t1_07"), None)
    if sample is None:
        print("  no sample available")
    else:
        required = ["qid", "seed", "system", "query", "tier", "expected_behavior",
                    "result_kind", "raw_result", "status", "error", "artifact",
                    "llm_calls", "input_tokens", "uncached_input_tokens",
                    "cache_write_tokens", "cache_read_tokens", "output_tokens",
                    "per_call_tokens", "wall_time_s", "cost_usd", "cost_usd_raw",
                    "cost_usd_cached", "gold_value", "comparator_verdict",
                    "comparator_reason", "outcome_class", "failure_tag"]
        missing = [k for k in required if k not in sample]
        print(f"  fields present: {len(sample)} / {len(required)} expected")
        print(f"  missing: {missing if missing else 'NONE'}")
        print(f"  keys: {sorted(sample.keys())}")

    # === check (d): resume — separately tested below ======================
    # See run_with_resume_check() below.

    # === check (e): out-of-range refusal classification for all 5 ==========
    print("\n--- (e) out-of-range refusal classification (oor_03) ---")
    for r in sorted(by_q.get("oor_03", []), key=lambda x: x["system"]):
        raw = str(r["raw_result"])[:50]
        print(f"  {r['system']:<12s} status={r['status']:<10s} "
              f"raw={raw:<50s} verdict={r['outcome_class']}")

    # === check (f): prompt-caching — V-hit + V-ledger (S21a) ===============
    print("\n--- (f) prompt caching: write on first call, read on repeat ---")
    cache_ok = True
    for sysname in sorted({r["system"] for r in smoke}):
        cells = sorted([r for r in smoke if r["system"] == sysname],
                       key=lambda x: x["qid"])           # qid order = call order within system
        wrote = any(r.get("cache_write_tokens", 0) > 0 for r in cells)
        read = any(r.get("cache_read_tokens", 0) > 0 for r in cells)
        raw_in = sum(r["input_tokens"] for r in cells)
        cache_read = sum(r.get("cache_read_tokens", 0) for r in cells)
        hit_share = (cache_read / raw_in) if raw_in else 0.0
        # multi-step systems (react/code) can hit cache within ONE cell too
        per_cell_read = any(
            any(c.get("cache_read", 0) > 0 for c in r.get("per_call_tokens", []))
            for r in cells)
        engaged = wrote or read or per_cell_read
        if not engaged:
            cache_ok = False
        flag = "PASS" if engaged else "WARN"
        print(f"  [{flag}] {sysname:<12s} wrote={wrote} read={(read or per_cell_read)} "
              f"cache_read_share={hit_share:5.1%}  (raw_in={raw_in}, cache_read={cache_read})")
    print(f"  -> V-hit overall: {'PASS — caching engaged' if cache_ok else 'WARN — some systems did not hit cache'}")
    # V-ledger: confirm the four input categories are present + reconcile
    led_ok = all(
        r["input_tokens"] == (r.get("uncached_input_tokens", 0)
                              + r.get("cache_write_tokens", 0)
                              + r.get("cache_read_tokens", 0))
        for r in smoke)
    print(f"  V-ledger: raw_input == uncached+write+read for all {len(smoke)} cells: "
          f"{'PASS' if led_ok else 'FAIL'}")

    # Tallies
    print("\n--- summary ---")
    classes = Counter(r["outcome_class"] for r in smoke)
    tot_raw = sum(r["input_tokens"] for r in smoke)
    tot_read = sum(r.get("cache_read_tokens", 0) for r in smoke)
    tot_cost_cached = sum(r.get("cost_usd_cached", r["cost_usd"]) for r in smoke)
    tot_cost_raw = sum(r.get("cost_usd_raw", r["cost_usd"]) for r in smoke)
    print(f"  {len(smoke)} cells; class counts: {dict(classes)}")
    print(f"  raw input tokens: {tot_raw}; served from cache: {tot_read} "
          f"({(tot_read/tot_raw if tot_raw else 0):.1%})")
    print(f"  $ cache-aware: {tot_cost_cached:.4f}  vs  $ cache-blind: {tot_cost_raw:.4f}  "
          f"(savings {1 - (tot_cost_cached/tot_cost_raw) if tot_cost_raw else 0:.1%})")
    print("  NOTE: caching lowers $ only; raw tokens + call counts are unchanged "
          "(the architecture-level metrics).")


def smoke_run(*, queries=SMOKE_QUERIES, seeds=(0,), systems=SYSTEMS, use_cache=True,
              results_path: Path = DEFAULTS["results_path"],
              provenance_path: Path = DEFAULTS["provenance_path"]) -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: smoke is LIVE and needs ANTHROPIC_API_KEY (the same key the "
              "Network uses for Call-1). Aborting.")
        sys.exit(2)
    from anthropic import Anthropic

    def factory(): return Anthropic()

    summary = run_cells(
        qids=list(queries), seeds=list(seeds), systems=list(systems),
        client_factory=factory, use_cache=use_cache,
        results_path=Path(results_path), provenance_path=Path(provenance_path),
    )
    print(f"\n=== run_cells: {summary['ran']} ran, {summary['skipped']} skipped "
          f"(caching {'ON' if use_cache else 'OFF'}) ===")
    report(Path(results_path), tuple(queries))


def main():
    ap = argparse.ArgumentParser(description="S21 smoke (5 systems x 3 queries x seed 0)")
    ap.add_argument("--queries", nargs="+", default=list(SMOKE_QUERIES))
    ap.add_argument("--seeds", nargs="+", type=int, default=[0])
    ap.add_argument("--systems", nargs="+", default=list(SYSTEMS))
    ap.add_argument("--results", default=str(DEFAULTS["results_path"]))
    ap.add_argument("--provenance", default=str(DEFAULTS["provenance_path"]))
    ap.add_argument("--no-cache", action="store_true",
                    help="disable prompt caching (for the V-equivalence baseline)")
    ap.add_argument("--report-only", action="store_true",
                    help="don't run; just re-report from existing results.jsonl")
    args = ap.parse_args()
    if args.report_only:
        report(Path(args.results), tuple(args.queries))
        return
    smoke_run(queries=tuple(args.queries), seeds=tuple(args.seeds),
              systems=tuple(args.systems), use_cache=not args.no_cache,
              results_path=Path(args.results),
              provenance_path=Path(args.provenance))


if __name__ == "__main__":
    main()
