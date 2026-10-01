#!/usr/bin/env python
"""Spider 3-domain harness wiring smoke test — 6 cells, NOT a real eval.

Checks that the harness threads cleanly through all 3 Spider domains (wta_1,
world_1, bike_1; 85 queries, 39 answer / 46 refuse). Each domain's refuse-cases
are tier="out_of_scope_register" — answerable in principle, but needing a table
outside the domain's single-table register. That is distinct from the employee
domain's tier="out_of_range", which means unanswerable in principle.

run_cells()'s corpus_path/gold_path/register_path/schema_path are plain Path
parameters with no filename assumptions — register loading is
`pd.read_pickle(register_path)`, so the per-table .pkl naming these domains use
(wta_1/matches.pkl, world_1/country.pkl, bike_1/trip.pkl) needs no special
handling. NetworkAdapter's schema_path threads through to build_bridge()
identically regardless of domain.

Scope: for EACH domain, 1 answer-query + 1 refuse-query, through ONE system
(network), 1 seed (0) = 6 live Call-1+ requests total. This also exercises the
out_of_scope_register tier through the harness — the refuse-query's expected
outcome is Unreachable, not a wrong answer.

ONE results/provenance file pair PER DOMAIN, deliberately not shared: each
Spider domain has a genuinely different corpus/gold pair, so a shared path
always looks like drift to harness.py's provenance-drift guard, which raises a
bare SystemExit. Each domain's run_cells() call is also wrapped in a targeted
`except SystemExit` so a drift guard firing on ONE domain can't block the other
two; genuine API/transport errors are NOT SystemExit and still propagate.

Needs ANTHROPIC_API_KEY:

    python scripts/smoke_test_spider_domains.py
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

DOMAINS = {
    "wta_1": {
        "corpus": ROOT / "evaluation/domains/spider_wta_1/eval_corpus_spider_wta_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_wta_1.json",
        "register": ROOT / "evaluation/domains/spider_wta_1" / "matches.pkl",
        "schema": ROOT / "evaluation/domains/spider_wta_1" / "schema.json",
        "qids": ["wta_02", "wta_01"],   # [answer, refuse]
        "results": ROOT / "results/results_spider_smoke_wta_1.jsonl",
        "provenance": ROOT / "results/results_spider_smoke_wta_1.provenance.json",
    },
    "world_1": {
        "corpus": ROOT / "evaluation/domains/spider_world_1/eval_corpus_spider_world_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_world_1.json",
        "register": ROOT / "evaluation/domains/spider_world_1" / "country.pkl",
        "schema": ROOT / "evaluation/domains/spider_world_1" / "schema.json",
        "qids": ["world_01", "world_07"],
        "results": ROOT / "results/results_spider_smoke_world_1.jsonl",
        "provenance": ROOT / "results/results_spider_smoke_world_1.provenance.json",
    },
    "bike_1": {
        "corpus": ROOT / "evaluation/domains/spider_bike_1/eval_corpus_spider_bike_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_bike_1.json",
        "register": ROOT / "evaluation/domains/spider_bike_1" / "trip.pkl",
        "schema": ROOT / "evaluation/domains/spider_bike_1" / "schema.json",
        "qids": ["bike_01", "bike_11"],
        "results": ROOT / "results/results_spider_smoke_bike_1.jsonl",
        "provenance": ROOT / "results/results_spider_smoke_bike_1.provenance.json",
    },
}
SYSTEMS = ["network"]
SEEDS = [0]
MODEL = "claude-sonnet-4-5"

# No Spider-domain run has ever happened, so no empirical per-call token count for
# these specific domains yet. Reused from the risk domain's own results_off.jsonl
# per-cell average for network (1.00 calls/cell, single-call system) -- see
# scripts/smoke_test_employee_domain.py for the full empirical derivation.
NETWORK_TOKENS = (2040, 160)


def check_wiring() -> None:
    """Fail loudly and early if any domain's 4 files are missing or its 2 smoke
    qids aren't present -- before printing a cost estimate for calls that
    couldn't succeed anyway."""
    for domain, cfg in DOMAINS.items():
        missing = [p for p in (cfg["corpus"], cfg["gold"], cfg["register"], cfg["schema"])
                  if not p.exists()]
        if missing:
            raise SystemExit(f"ERROR: missing {domain} file(s):\n  " +
                             "\n  ".join(str(p) for p in missing))
        corpus = json.loads(cfg["corpus"].read_text())
        ids_present = {q["id"] for q in corpus["queries"]}
        gold = json.loads(cfg["gold"].read_text())
        for qid in cfg["qids"]:
            if qid not in ids_present:
                raise SystemExit(f"ERROR: {qid!r} not found in {cfg['corpus'].name}")
            if qid not in gold:
                raise SystemExit(f"ERROR: {qid!r} not found in {cfg['gold'].name}")
            if gold[qid]["tier"] == "out_of_scope_register" and gold[qid]["expected_behavior"] != "refuse":
                raise SystemExit(f"ERROR: {qid!r} tier/expected_behavior mismatch in {cfg['gold'].name}")


def print_cost_estimate() -> None:
    from evidence.eval_run.pricing import cost_usd
    n_cells = sum(len(cfg["qids"]) for cfg in DOMAINS.values()) * len(SYSTEMS) * len(SEEDS)
    in_tok, out_tok = NETWORK_TOKENS
    per_cell = cost_usd(MODEL, in_tok, out_tok)
    total = per_cell * n_cells
    print("=" * 88)
    print("COST ESTIMATE (before any live call)")
    print("=" * 88)
    print(f"  3 domains x 2 queries (1 answer + 1 refuse) x {len(SYSTEMS)} system x "
         f"{len(SEEDS)} seed = {n_cells} cells")
    print(f"  network ~{in_tok} in / ~{out_tok} out tok/cell (risk-domain ESTIMATE) "
         f"x {n_cells} cells = ~${total:.4f}")
    print("=" * 88 + "\n")


def run_smoke() -> dict:
    from anthropic import Anthropic
    from evidence.eval_run.harness import run_cells

    def factory():
        return Anthropic()

    # ONE results/provenance file PER DOMAIN (see module docstring's S40 fix) --
    # each domain has a genuinely different corpus/gold pair, so sharing one file
    # across domains trips harness.py's provenance-drift guard on the 2nd+ domain.
    summary = {"ran": 0, "skipped": 0, "aborted": False, "domain_errors": []}
    for domain, cfg in DOMAINS.items():
        print(f"\n--- {domain} ---")
        try:
            s = run_cells(
                qids=cfg["qids"], seeds=SEEDS, systems=SYSTEMS,
                client_factory=factory, model=MODEL,
                temperature=None, use_cache=True,
                corpus_path=cfg["corpus"], gold_path=cfg["gold"],
                register_path=cfg["register"], schema_path=cfg["schema"],
                results_path=cfg["results"], provenance_path=cfg["provenance"],
            )
        except SystemExit as exc:
            # harness.py's provenance-drift guard (S36 Fix 3b) raises bare
            # SystemExit -- a LOCAL, domain-specific config problem (this domain's
            # corpus/gold changed since its own recorded provenance). Isolate it
            # so it can't block the other domains, unlike the real crash this
            # script originally caused. Genuine API/transport errors raise as
            # anthropic exception types, NOT SystemExit, so they still propagate
            # and abort the whole script here, same as run_cells()'s own intent.
            print(f"  [SKIPPED — provenance guard]: {exc}")
            summary["domain_errors"].append(domain)
            continue
        summary["ran"] += s["ran"]
        summary["skipped"] += s["skipped"]
        if s.get("aborted"):
            summary["aborted"] = True
            break
    return summary


def verify_result() -> None:
    """Per domain: read THAT domain's own results file (no cross-domain file
    sharing, so no risk of reading another domain's rows), confirm the qid
    resolves in THAT domain's own gold file, confirm the refuse-query's tier
    came through as out_of_scope_register end-to-end, and confirm the shape
    matches an existing results file's keys."""
    off_path = ROOT / "results/results_off.jsonl"
    ref_keys = (set(json.loads(off_path.read_text().splitlines()[0]).keys())
               if off_path.exists() else None)

    for domain, cfg in DOMAINS.items():
        print("=" * 88)
        if not cfg["results"].exists():
            print(f"--- {domain} --- NO RESULTS FILE ({cfg['results'].name} -- skipped or not yet run)")
            continue
        rows = [json.loads(l) for l in cfg["results"].read_text().splitlines() if l.strip()]
        print(f"--- {domain} --- {len(rows)} row(s) in {cfg['results'].name}")
        answer_qid, refuse_qid = cfg["qids"]
        corpus = json.loads(cfg["corpus"].read_text())
        gold = json.loads(cfg["gold"].read_text())
        q_by_id = {q["id"]: q for q in corpus["queries"]}
        by_qid = {r["qid"]: r for r in rows}

        for qid, expect_behavior in [(answer_qid, "answer"), (refuse_qid, "refuse")]:
            r = by_qid.get(qid)
            if r is None:
                print(f"  [{qid}] MISSING")
                continue
            if ref_keys is not None:
                missing = ref_keys - set(r.keys())
                extra = set(r.keys()) - ref_keys
                if missing or extra:
                    print(f"  [{qid}] key-shape vs results_off.jsonl: missing={missing or 'none'} extra={extra or 'none'}")
            g = gold.get(qid)
            wrong_domain_gold = g is None or g["expected_behavior"] != expect_behavior
            tier_ok = (g["tier"] == "out_of_scope_register") if expect_behavior == "refuse" else (g["tier"] != "out_of_scope_register")
            print(f"  [{expect_behavior}:{qid}] {q_by_id[qid]['question'][:55]!r}")
            print(f"      status={r['status']!r} outcome_class={r['outcome_class']!r} tier={r['tier']!r} "
                 f"result_kind={r['result_kind']!r} comparator_reason={r['comparator_reason'][:70]!r}"
                 f"{'  <<< WRONG DOMAIN GOLD' if wrong_domain_gold else ''}"
                 f"{'  <<< TIER MISMATCH' if not tier_ok else ''}")
    print("=" * 88 + "\n")


def main() -> int:
    print(__doc__)
    check_wiring()
    print(f"Wiring check OK: all 3 domains' 4 files exist and contain their 2 smoke qids each.\n")
    print_cost_estimate()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        print("This script makes live calls to the Anthropic API (see the cost estimate above)")
        print("and needs a real key to do that. Set it and re-run, e.g.:")
        print("  export ANTHROPIC_API_KEY=sk-...")
        print("  python scripts/smoke_test_spider_domains.py")
        return 2

    summary = run_smoke()
    aborted_note = " — ABORTED" if summary.get("aborted") else ""
    skipped_domains = summary.get("domain_errors") or []
    guard_note = f"  (provenance guard skipped: {skipped_domains})" if skipped_domains else ""
    print(f"\n=== run_cells: {summary['ran']} ran, {summary['skipped']} skipped"
         f"{aborted_note}{guard_note} ===\n")
    verify_result()
    return 0


if __name__ == "__main__":
    sys.exit(main())
