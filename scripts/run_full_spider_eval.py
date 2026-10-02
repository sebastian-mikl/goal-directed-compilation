#!/usr/bin/env python
"""Spider-domain FULL evaluation run — the real thing, not a smoke test.

SCOPE:
  domains: wta_1 (30q: 14 answer + 16 refuse), world_1 (30q: 15 answer + 15
           refuse), bike_1 (25q: 10 answer + 15 refuse) = 85 queries total.
           Of the 39 answer-type queries, 10 carry tier=out_of_contract_aggregate
           (a permanent Goal-contract architectural gap); all 46 refuse-type
           queries carry tier=out_of_scope_register.
  systems: network, planner, react, code_agent, text_to_sql (all 5)
  seeds:   0, 1, 2, 3, 4
  model:   claude-sonnet-4-5 for ALL 5 systems, no Haiku emission override.
  = 85 x 5 x 5 = 2125 live cells.

REFUSE_OUT_OF_SCOPE_DATA stays OFF here: this run establishes the toggle-off
baseline, matching every other system/toggle default in this project.

temperature and bounds: network (gdc/llm.py::llm1) and planner_upfront hardcode
temperature=0 at their own client.messages.create() call sites and do NOT accept
an override. react, code_agent and text_to_sql are run here at temperature 0 and
code_agent at 12 retries (13 calls), the protocol the paper reports. The first
recorded Spider runs used the API default and 3 retries (the harness defaults);
the temperature-0 re-runs in results/results_temp0_* replaced those cells, and
each provenance file records what its run used.

Calls evidence.eval_run.harness.run_cells() DIRECTLY per domain, with each
domain's corpus, gold, register and schema paths.

ONE results/provenance file PER DOMAIN — NOT a single combined file. Sharing one
file across domains with genuinely different corpus/gold trips harness.py's
provenance-drift guard on the 2nd+ domain. Each domain's run_cells() call is
wrapped in try/except SystemExit so a legitimate drift on ONE domain can't block
the other two; a genuine transport/API error does NOT raise (run_cells() catches
it internally and returns summary["aborted"]=True), so this script checks that
flag and stops rather than burning retries against a down/rate-limited API.

RESUMABLE, like every other run_cells()-based script here: its own JsonlLogger
reads any EXISTING results file for a domain and skips any (system, qid, seed)
cell already present. If interrupted for ANY reason, just re-run the SAME
command — completed cells are skipped and nothing is double-paid-for.

Writes results_spider_{wta_1,world_1,bike_1}.jsonl (+ matching .provenance.json).
Does NOT touch results_spider_smoke_*.jsonl or any employee results.

Needs ANTHROPIC_API_KEY:

    python scripts/run_full_spider_eval.py

See the cost estimate below before running — it uses real empirical per-cell
token counts rather than a guess.
"""
from __future__ import annotations

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

SYSTEMS = ["network", "planner", "react", "code_agent", "text_to_sql"]   # ADAPTER_CLASSES keys
SEEDS = [0, 1, 2, 3, 4]
MODEL = "claude-sonnet-4-5"

DOMAINS = {
    "wta_1": {
        "corpus": ROOT / "evaluation/domains/spider_wta_1/eval_corpus_spider_wta_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_wta_1.json",
        "register": ROOT / "evaluation/domains/spider_wta_1" / "matches.pkl",
        "schema": ROOT / "evaluation/domains/spider_wta_1" / "schema.json",
        "results": ROOT / "results/results_spider_wta_1.jsonl",
        "provenance": ROOT / "results/results_spider_wta_1.provenance.json",
    },
    "world_1": {
        "corpus": ROOT / "evaluation/domains/spider_world_1/eval_corpus_spider_world_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_world_1.json",
        "register": ROOT / "evaluation/domains/spider_world_1" / "country.pkl",
        "schema": ROOT / "evaluation/domains/spider_world_1" / "schema.json",
        "results": ROOT / "results/results_spider_world_1.jsonl",
        "provenance": ROOT / "results/results_spider_world_1.provenance.json",
    },
    "bike_1": {
        "corpus": ROOT / "evaluation/domains/spider_bike_1/eval_corpus_spider_bike_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_bike_1.json",
        "register": ROOT / "evaluation/domains/spider_bike_1" / "trip.pkl",
        "schema": ROOT / "evaluation/domains/spider_bike_1" / "schema.json",
        "results": ROOT / "results/results_spider_bike_1.jsonl",
        "provenance": ROOT / "results/results_spider_bike_1.provenance.json",
    },
}

# Empirical per-call tokens. network: this project's OWN real Spider data (the
# smoke test's 6 real network cells across all 3 domains — domain-matched, the
# best available estimate). The other 4 systems have never run against Spider,
# so — same reasoning run_employee_full_eval.py used when employee had no data
# of its own yet — carried over from the closest available REAL, at-scale,
# completed run: results_employee.jsonl (273 cells/system), which is a better
# base than the risk-only numbers that script had to use, since employee is
# itself a general (non-Spider-specific) domain, not specific to the synthetic domain.
# network's employee-real (1410 in/155 out) vs Spider-real (1304 in/124 out)
# are close, which is reassuring cross-validation for using employee's numbers
# for the other 4 systems too.
EMPIRICAL_TOKENS = {
    "network":     (1304, 124),    # real Spider smoke-test average (n=6)
    "planner":     (755, 187),     # real employee-domain average (n=273)
    "react":       (11468, 389),   # real employee-domain average (n=273)
    "code_agent":  (3583, 507),    # real employee-domain average (n=273)
    "text_to_sql": (1113, 110),    # real employee-domain average (n=273)
}
EMPIRICAL_CALLS_PER_CELL = {          # from the same results_employee.jsonl averages
    "network": 1.00, "planner": 1.00, "react": 3.57, "code_agent": 2.40, "text_to_sql": 1.00,
}
# PER-SYSTEM real cost_usd_cached / cost_usd_raw ratio, summed across ALL of that
# system's rows in results_employee.jsonl. Varies far too much per system for one
# blended number: network (0.432) and react (0.643) get real caching savings, but
# planner/code_agent/text_to_sql show ratio=1.000 -- ZERO savings, cached cost
# equals raw cost exactly -- consistent with the "static prefix ~NNN tok < min
# cacheable 1024 -- caching may not engage" warnings the smoke tests emit. Used
# only to project an "expected real bill" per system alongside the cache-blind
# ceiling.
EMPIRICAL_CACHE_SAVINGS_RATIO = {
    "network": 0.432, "planner": 1.000, "react": 0.643,
    "code_agent": 1.000, "text_to_sql": 1.000,
}


def check_wiring() -> dict[str, list[str]]:
    """Fail loudly and early if any domain's 4 files are missing or corpus/gold
    disagree on ids -- before printing a cost estimate for calls that couldn't
    succeed anyway. Returns {domain: [qids]}."""
    qids_by_domain: dict[str, list[str]] = {}
    for domain, cfg in DOMAINS.items():
        missing = [p for p in (cfg["corpus"], cfg["gold"], cfg["register"], cfg["schema"])
                  if not p.exists()]
        if missing:
            raise SystemExit(f"ERROR: missing {domain} file(s):\n  " +
                             "\n  ".join(str(p) for p in missing))
        corpus = json.loads(cfg["corpus"].read_text())
        qids = [q["id"] for q in corpus["queries"]]
        gold = json.loads(cfg["gold"].read_text())
        missing_gold = [q for q in qids if q not in gold]
        if missing_gold:
            raise SystemExit(f"ERROR: {domain}: {len(missing_gold)} corpus id(s) "
                             f"missing from gold: {missing_gold[:5]}")
        qids_by_domain[domain] = qids
    return qids_by_domain


def print_cost_estimate(qids_by_domain: dict[str, list[str]]) -> None:
    from evidence.eval_run.pricing import cost_usd
    n_queries_total = sum(len(q) for q in qids_by_domain.values())
    n_cells_total = n_queries_total * len(SYSTEMS) * len(SEEDS)
    print("=" * 88)
    print("COST ESTIMATE (before any live call)")
    print("=" * 88)
    for domain, qids in qids_by_domain.items():
        print(f"  {domain}: {len(qids)} queries")
    print(f"  {n_queries_total} queries total x {len(SYSTEMS)} systems x {len(SEEDS)} seeds "
          f"= {n_cells_total} cells")
    print(f"  (react/code_agent issue several LLM calls per cell -- see per-system figures)\n")
    grand_total = 0.0
    grand_expected = 0.0
    for sysname in SYSTEMS:
        in_tok, out_tok = EMPIRICAL_TOKENS[sysname]
        per_cell = cost_usd(MODEL, in_tok, out_tok) * EMPIRICAL_CALLS_PER_CELL[sysname]
        n_cells_sys = n_queries_total * len(SEEDS)
        sys_total = per_cell * n_cells_sys
        sys_ratio = EMPIRICAL_CACHE_SAVINGS_RATIO[sysname]
        sys_expected = sys_total * sys_ratio
        grand_total += sys_total
        grand_expected += sys_expected
        print(f"  {sysname:<12s} ~{in_tok:>6d} in / ~{out_tok:>4d} out tok/call x "
              f"~{EMPIRICAL_CALLS_PER_CELL[sysname]:.2f} calls/cell x {n_cells_sys} cells "
              f"= ~${sys_total:>7.2f} ceiling, ~${sys_expected:>7.2f} expected "
              f"(real cache ratio {sys_ratio:.0%})")
    print(f"\n  {n_cells_total} cells TOTAL:")
    print(f"    ~${grand_total:.2f}  cache-blind CEILING (worst case, every call at full raw price)")
    print(f"    ~${grand_expected:.2f}  EXPECTED real bill (per-system cache ratio applied, from "
          f"results_employee.jsonl's actual completed-run costs)")
    print("=" * 88 + "\n")


def run_domain(domain: str, cfg: dict, qids: list[str]) -> dict:
    from anthropic import Anthropic
    from evidence.eval_run.harness import run_cells

    def factory():
        return Anthropic()

    return run_cells(
        qids=qids, seeds=SEEDS, systems=SYSTEMS,
        client_factory=factory, model=MODEL,
        temperature=0, use_cache=True,
        bounds={"react_max_iters": 15, "code_max_retries": 12, "per_query_timeout_s": 60},
        corpus_path=cfg["corpus"], gold_path=cfg["gold"],
        register_path=cfg["register"], schema_path=cfg["schema"],
        results_path=cfg["results"], provenance_path=cfg["provenance"],
    )


def print_summary(domain: str, cfg: dict) -> None:
    if not cfg["results"].exists():
        return
    rows = [json.loads(l) for l in cfg["results"].read_text().splitlines() if l.strip()]
    by_system: dict[str, list[dict]] = {}
    for r in rows:
        by_system.setdefault(r["system"], []).append(r)

    print(f"--- {domain}: {len(rows)} rows in {cfg['results'].name} ---")
    print(f"  {'system':<12s} {'n':>5s} {'correct%':>9s} {'silent_wrong%':>14s} "
          f"{'crash%':>7s} {'timeout%':>9s}")
    for sysname in SYSTEMS:
        sys_rows = by_system.get(sysname, [])
        n = len(sys_rows)
        if n == 0:
            print(f"  {sysname:<12s} {'0':>5s}  (no rows)")
            continue
        pct = lambda cls: 100.0 * sum(1 for r in sys_rows if r["outcome_class"] == cls) / n
        print(f"  {sysname:<12s} {n:>5d} {pct('correct'):>8.1f}% {pct('silent_wrong'):>13.1f}% "
              f"{pct('crash'):>6.1f}% {pct('timeout'):>8.1f}%")
    print()


def main() -> int:
    print(__doc__)
    qids_by_domain = check_wiring()
    print(f"Wiring check OK: " +
          ", ".join(f"{d} ({len(q)}q)" for d, q in qids_by_domain.items()) + "\n")
    print_cost_estimate(qids_by_domain)

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        print("This script makes live calls to the Anthropic API (see the cost estimate above)")
        print("and needs a real key to do that. Set it and re-run, e.g.:")
        print("  export ANTHROPIC_API_KEY=sk-...")
        print("  python scripts/run_full_spider_eval.py")
        return 2

    overall = {"ran": 0, "skipped": 0, "aborted": False, "domain_errors": []}
    for domain, cfg in DOMAINS.items():
        print(f"\n{'=' * 88}\n--- {domain} ---\n{'=' * 88}")
        try:
            summary = run_domain(domain, cfg, qids_by_domain[domain])
        except SystemExit as exc:
            # harness.py's provenance-drift guard -- a LOCAL, domain-specific
            # config problem (this domain's corpus/gold changed since its own
            # recorded provenance). Isolated so it can't block the other domains.
            # Genuine API/transport errors do NOT reach here (run_cells() catches
            # them internally and returns summary["aborted"]=True instead).
            print(f"  [SKIPPED -- provenance guard]: {exc}")
            overall["domain_errors"].append(domain)
            continue
        overall["ran"] += summary["ran"]
        overall["skipped"] += summary["skipped"]
        print(f"  {summary['ran']} ran, {summary['skipped']} skipped"
              f"{' — ABORTED' if summary.get('aborted') else ''}")
        if summary.get("aborted"):
            overall["aborted"] = True
            print("\nRun aborted on a transport/API error -- re-run the SAME command to resume; "
                 "completed cells (in any domain) are skipped, the aborted cell retries.")
            break

    aborted_note = " — ABORTED" if overall["aborted"] else ""
    skipped_domains = overall["domain_errors"]
    guard_note = f"  (provenance guard skipped: {skipped_domains})" if skipped_domains else ""
    print(f"\n{'=' * 88}\nOVERALL: {overall['ran']} ran, {overall['skipped']} skipped"
          f"{aborted_note}{guard_note}\n{'=' * 88}\n")

    for domain, cfg in DOMAINS.items():
        print_summary(domain, cfg)

    return 1 if overall["aborted"] else 0


if __name__ == "__main__":
    sys.exit(main())
