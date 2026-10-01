#!/usr/bin/env python
"""Phase L — single batched LIVE verification of the prompt-side Spider fixes.

Prompt-side fixes cannot be verified by offline replay (replay re-executes the
OLD recorded goals; a prompt fix changes what NEW goals get emitted), so every
prompt-side change verifies here, in one batched network-only Sonnet-only run.

Currently covered:

  FIX 1 (order-by column omission, prompt version s43) — the Call-1 contract now
  requires every order_by column to appear in the goal's columns or derive.
  * targets: the 5 answer-tier qids that failed 5/5 seeds with "order_by column
    not in goal output" — wta_16, wta_17, world_05, world_13, bike_02. Two
    levels are checked separately: (1) CLOSURE — the emitted goal now includes
    the ranking column; (2) OUTCOME vs gold.

    Per-target expectations, pre-validated offline against the real
    deterministic pipeline + comparator:
      wta_17    -> correct: ordered_list gold tolerates the ranking column.
      wta_16, world_05, bike_02 -> EXPECTED-PARTIAL "scalar shape": closure
                forces the ranking column into the output (exact-column
                satisfaction), so a scalar-gold argmax answers as a 1-row x
                2-col table that the comparator's scalar path does not unwrap.
      world_13  -> EXPECTED-PARTIAL "tie": 7 countries tie at Population=0;
                Spider's gold SQL pins Antarctica by SQLite insertion order,
                our executor picks another tie member. A benchmark-semantics
                ambiguity, reported as a characterized residual.
    The gate demands: closure satisfied on all 5, wta_17 correct, and the other
    four landing on their EXPECTED partial reason — anything else fails.

  FINDING 7 (implicit-ranking instruction, prompt version s44) — a SEPARATE
  Call-1 rule from Fix 1: Fix 1 fixes "ranks but forgets to output the column",
  Finding 7 fixes "does not recognize a superlative implies ranking". It has
  zero recorded failing instances, so its section is over-trigger isolation only.
  * stressors: 4 scalar-aggregate superlative queries, correct on every recorded
    seed via the aggregate route (max/min derive, NO order spec) — risk t1_09,
    employee t1_09, employee hv_06, wta_06 ("highest rank" = min(loser_rank),
    direction-inverted). Expected: still correct AND still the aggregate route.
    An emitted order spec is the over-trigger signature — reported as WARN even
    when the outcome survives, because the route change is the regression signal.

  FIX 2 (count_distinct, prompt version s45) — the engine executes
  count_distinct(col) (nunique threading; count(DISTINCT col) canonicalises at
  parse time) and the derive doc declares the spelling. Recorded emissions show
  the model naming distinct-intent while emitting plain count(col), so the
  prompt line is what these targets verify.
  * targets: the 4 answer-tier COUNT(DISTINCT) qids, silent_wrong 5/5 in the
    full run — wta_08 (278 vs 49), wta_24 (3 vs 2), world_09 (grouped table vs
    scalar distinct count), bike_04 (9959 vs 530). Expected: correct.
  * bike_09 (bare SELECT DISTINCT) is deliberately ABSENT: retiered
    out_of_contract_aggregate / distinct_projection — the language has no
    projection-dedupe and refuses honestly, which is the designed behavior.
  * stressors (over-trigger guard): 3 plain-count queries, correct on every
    recorded seed via count(col) — wta_02 (count over heavily-duplicated
    match_num: a spurious distinct collapses the count), wta_10 (count over year
    with ~2 distinct values under its filter — maximally sensitive), world_18
    (count over the UNIQUE code column — value-invisible, so ROUTE is the
    signal). A count_distinct in the emitted derive is the over-trigger
    signature: FAIL when the value breaks, WARN when it survives.
  * regression set (over-inclusion guard): 7 currently-passing ordered queries —
    wta_23, world_17, bike_01, bike_08 (Spider order_by tier, correct 5/5),
    risk t3_09 + t3_10 and employee t3_10 (ordered_list gold ordered by a
    DERIVED column, guarding the derive-only ordering path). Expected: still
    correct.
  * boundary set: wta_11, wta_12 (out_of_scope_register — they ask for
    players-table facts this matches-register does not carry). These do NOT hit
    Unreachable: the model PROXIES players-table concepts onto real matches
    columns (first name->winner_name, country code->winner_ioc, oldest->
    winner_age desc limit 1) and answers a players-table question with match
    data. The goal is well-formed and reachable, so Unreachable cannot catch it;
    the only mechanism that refuses it is REFUSE_OUT_OF_SCOPE_DATA, which is OFF
    here for baseline comparability. So with the toggle off the expected outcome
    is silent_wrong — a characterized residual, refused correctly by Phase R's
    toggle-on variant. If either query comes back refused with the toggle off,
    that is flagged too: it would mean something unexpectedly caught it.

Needs ANTHROPIC_API_KEY:

    python scripts/verify_spider_fixes_live.py

14 live Call-1 requests (5 targets + 7 regression + 2 boundary), Sonnet only,
1 repetition each (the target failures were deterministic 5/5). Cost estimate
prints before any call. Results land in results_verify_spider_fixes.jsonl
(overwritten each run, never appended).
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

import pandas as pd

from evidence.eval_run.briefing import load_briefing
from evidence.eval_run.comparator import score
from evidence.eval_run.logger import CachingClient
from evidence.eval_run.pricing import cost_usd, min_cacheable_tokens

MODEL = "claude-sonnet-4-5"            # Sonnet only — Spider was never run under Haiku
SEED = 0

DOMAINS = {
    "risk": {
        "corpus": ROOT / "eval_corpus.json",
        "gold": ROOT / "evidence" / "gold" / "gold.json",
        "register": ROOT / "environment" / "register.pkl",
        "schema": ROOT / "environment" / "schema.json",
    },
    "employee": {
        "corpus": ROOT / "evaluation/domains/employee/eval_corpus_employee.json",
        "gold": ROOT / "evidence" / "gold" / "gold_employee.json",
        "register": ROOT / "evaluation/domains/employee" / "employees.pkl",
        "schema": ROOT / "evaluation/domains/employee" / "schema.json",
    },
    "wta_1": {
        "corpus": ROOT / "evaluation/domains/spider_wta_1/eval_corpus_spider_wta_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_wta_1.json",
        "register": ROOT / "evaluation/domains/spider_wta_1" / "matches.pkl",
        "schema": ROOT / "evaluation/domains/spider_wta_1" / "schema.json",
    },
    "world_1": {
        "corpus": ROOT / "evaluation/domains/spider_world_1/eval_corpus_spider_world_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_world_1.json",
        "register": ROOT / "evaluation/domains/spider_world_1" / "country.pkl",
        "schema": ROOT / "evaluation/domains/spider_world_1" / "schema.json",
    },
    "bike_1": {
        "corpus": ROOT / "evaluation/domains/spider_bike_1/eval_corpus_spider_bike_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_bike_1.json",
        "register": ROOT / "evaluation/domains/spider_bike_1" / "trip.pkl",
        "schema": ROOT / "evaluation/domains/spider_bike_1" / "schema.json",
    },
}

# (domain, qid, group, expected). Groups drive the summary; `expected` is the
# calibrated per-entry expectation (see docstring):
#   "correct"             — closure satisfied AND comparator verdict correct
#   "partial_scalar_shape"— closure satisfied; scalar gold vs 1-row table whose
#                           extra column is the mandated ranking column
#   "partial_tie"         — closure satisfied; gold pins one member of a tie
#   "silent_wrong_s40off" — S40 table-coverage RESIDUAL: an out_of_scope_register
#                           query the model answers with proxied real columns;
#                           only REFUSE_OUT_OF_SCOPE_DATA (S40) refuses it, and
#                           S40 is OFF here for baseline comparability, so
#                           silent_wrong IS the understood outcome. Resolved
#                           (correctly refused) by Phase R's S40-on variant.
ENTRIES = [
    ("wta_1",    "wta_16",   "fix1_target", "partial_scalar_shape"),
    ("wta_1",    "wta_17",   "fix1_target", "correct"),
    ("world_1",  "world_05", "fix1_target", "partial_scalar_shape"),
    ("world_1",  "world_13", "fix1_target", "partial_tie"),
    ("bike_1",   "bike_02",  "fix1_target", "partial_scalar_shape"),
    ("wta_1",    "wta_23",   "regression",  "correct"),
    ("world_1",  "world_17", "regression",  "correct"),
    ("bike_1",   "bike_01",  "regression",  "correct"),
    ("bike_1",   "bike_08",  "regression",  "correct"),
    ("risk",     "t3_09",    "regression",  "correct"),
    ("risk",     "t3_10",    "regression",  "correct"),
    ("employee", "t3_10",    "regression",  "correct"),
    # boundary = the S40 table-coverage residual (see docstring): with S40 OFF
    # the honest expectation is silent_wrong; Phase R's S40-on variant refuses
    # these correctly. NOT lowered-until-green — the mechanism is understood.
    ("wta_1",    "wta_11",   "boundary",    "silent_wrong_s40off"),
    ("wta_1",    "wta_12",   "boundary",    "silent_wrong_s40off"),
    ("risk",     "t1_09",    "finding7_stressor", "correct"),
    ("employee", "t1_09",    "finding7_stressor", "correct"),
    ("employee", "hv_06",    "finding7_stressor", "correct"),
    ("wta_1",    "wta_06",   "finding7_stressor", "correct"),
    ("wta_1",    "wta_08",   "fix2_target", "correct"),
    ("wta_1",    "wta_24",   "fix2_target", "correct"),
    ("world_1",  "world_09", "fix2_target", "correct"),
    ("bike_1",   "bike_04",  "fix2_target", "correct"),
    ("wta_1",    "wta_02",   "fix2_stressor", "correct"),
    ("wta_1",    "wta_10",   "fix2_stressor", "correct"),
    ("world_1",  "world_18", "fix2_stressor", "correct"),
]

# Empirical mean Call-1 tokens per network cell, computed from this repo's own
# recorded results files (results_off.rescored / results_employee /
# results_spider_*). Used ONLY for the cost estimate.
EMPIRICAL_TOKENS = {
    "risk":     (2040, 160),   # mean over 390 recorded network cells
    "employee": (1410, 155),   # mean over 273 recorded network cells
    "wta_1":    (1426, 177),   # mean over 150 recorded network cells
    "world_1":  (1426, 171),   # mean over 150 recorded network cells
    "bike_1":   (1072, 191),   # mean over 125 recorded network cells
}

OUT_RESULTS_PATH = ROOT / "results/results_verify_spider_fixes.jsonl"


def load_domain(name: str):
    cfg = DOMAINS[name]
    missing = [p for p in cfg.values() if not p.exists()]
    if missing:
        raise SystemExit(f"ERROR: {name}: missing file(s): " +
                         ", ".join(str(p) for p in missing))
    corpus = {q["id"]: q for q in json.loads(cfg["corpus"].read_text())["queries"]}
    gold = json.loads(cfg["gold"].read_text())
    return cfg, corpus, gold


def check_wiring() -> None:
    """Every entry's qid must exist in its domain's corpus AND gold — fail loudly
    before printing a cost estimate for calls that couldn't succeed."""
    problems = []
    for domain, qid, group, expected in ENTRIES:
        _cfg, corpus, gold = load_domain(domain)
        if qid not in corpus:
            problems.append(f"{domain}/{qid}: not in corpus")
        if qid not in gold:
            problems.append(f"{domain}/{qid}: not in gold")
    if problems:
        raise SystemExit("ERROR: wiring check failed:\n  " + "\n  ".join(problems))


def print_plan_and_cost() -> None:
    print("=" * 88)
    print(f"PHASE L VERIFICATION PLAN — {len(ENTRIES)} live Call-1 requests, "
          f"{MODEL}, 1 repetition each")
    print("=" * 88)
    for domain, qid, group, expected in ENTRIES:
        _cfg, corpus, _gold = load_domain(domain)
        print(f"  [{group:<11s}] {domain:<9s} {qid:<9s} expect={expected:<20s} "
              f"{corpus[qid]['question'][:44]}")
    print()
    total = 0.0
    by_domain: dict[str, int] = {}
    for domain, _qid, _group, _expected in ENTRIES:
        by_domain[domain] = by_domain.get(domain, 0) + 1
    for domain, n in by_domain.items():
        tin, tout = EMPIRICAL_TOKENS[domain]
        per_call = cost_usd(MODEL, tin, tout)
        total += per_call * n
        print(f"  {domain:<9s} {n} call(s) x ~${per_call:.4f} "
              f"(~{tin} in / ~{tout} out tok, empirical)")
    print(f"\n  TOTAL estimated cost: ~${total:.4f}  (cache-blind ceiling; prompt "
          f"caching engages per domain\n  above the {min_cacheable_tokens(MODEL)}-token "
          f"floor, so the real bill should come in at or below this)")
    print("=" * 88 + "\n")


def order_closure_check(artifact: dict) -> tuple[bool | None, list[str]]:
    """(closure_ok, order_cols): does every order_by column appear in the emitted
    goal's columns or derive names? None when there is no goal / no ordering."""
    goal = artifact.get("goal")
    order = artifact.get("order") or {}
    order_cols = [e["col"] for e in (order.get("order_by") or [])]
    if goal is None or not order_cols:
        return None, order_cols
    out = {c[0] for c in goal["columns"]} | {d["name"] for d in goal["derive"]}
    return all(c in out for c in order_cols), order_cols


def run_entry(adapter, domain: str, qid: str, group: str, expected: str,
              corpus: dict, gold: dict) -> dict:
    q = corpus[qid]["question"]
    rec = adapter.run(q, SEED)
    verdict, reason, tag = score(rec["result"], gold[qid], status=rec["status"])
    closure, order_cols = order_closure_check(rec.get("artifact") or {})
    goal = (rec.get("artifact") or {}).get("goal")
    return {
        "domain": domain, "qid": qid, "group": group, "expected": expected, "query": q,
        "status": rec["status"], "error": rec.get("error"),
        "outcome_class": verdict, "comparator_reason": reason,
        "order_cols": order_cols, "order_closure_ok": closure,
        "emitted_columns": [c[0] for c in goal["columns"]] if goal else None,
        "emitted_derive": [d["name"] for d in goal["derive"]] if goal else None,
        "artifact": rec.get("artifact"),
        "raw_result": rec.get("result"),
        "gold_value": gold[qid]["value"],
        "model": MODEL, "seed": SEED,
        "prompt_version_note": ("s45 (Fix 1 order-closure + Finding 7 "
                                "implicit-ranking + Fix 2 count_distinct)"),
    }


def verdict_for(row: dict) -> str:
    """PASS / EXPECTED-PARTIAL are gate-clean; FAIL and UNEXPECTED are not.
    Judged against the entry's calibrated `expected` (see docstring)."""
    g, exp, oc = row["group"], row["expected"], row["outcome_class"]
    reason = row.get("comparator_reason") or ""
    if g == "fix1_target":
        if row["order_closure_ok"] is False:
            return "FAIL (closure still violated — ranking column still omitted)"
        if oc == "correct":
            return ("PASS (closure satisfied, answer correct)" if exp == "correct"
                    else f"PASS (closure satisfied, answer correct — better than expected {exp})")
        if exp == "partial_scalar_shape" and "scalar mismatch" in reason:
            return ("EXPECTED-PARTIAL (closure satisfied; scalar-shape: gold value "
                    "present beside the mandated ranking column — comparator "
                    "follow-up pending)")
        if exp == "partial_tie" and "key mismatch" in reason:
            return ("EXPECTED-PARTIAL (closure satisfied; gold pins one member of "
                    "a tie — characterized residual)")
        return (f"UNEXPECTED (closure satisfied but outcome={oc}, reason="
                f"{reason[:70]!r} — does not match expected {exp}; diagnose)")
    if g == "regression":
        return ("PASS (still correct)" if oc == "correct"
                else f"FAIL (regressed to {oc}: {reason[:60]})")
    if g == "fix2_target":
        if oc == "correct":
            return "PASS (count_distinct emitted and executed to the gold value)"
        return (f"FAIL (still {oc}: {reason[:60]} — emitted derive="
                f"{row['emitted_derive']}, see artifact for the expr)")
    if g == "fix2_stressor":
        goal = (row.get("artifact") or {}).get("goal") or {}
        distinct_emitted = any("count_distinct" in d.get("expr", "")
                               for d in goal.get("derive", []))
        if oc == "correct" and not distinct_emitted:
            return "PASS (plain count preserved, no spurious distinct)"
        if oc == "correct" and distinct_emitted:
            return ("WARN (outcome survived but count_distinct OVER-TRIGGERED on a "
                    "plain-count query — value-invisible route change)")
        return (f"FAIL (stressor regressed to {oc}"
                f"{' with spurious count_distinct' if distinct_emitted else ''}"
                f": {reason[:60]})")
    if g == "finding7_stressor":
        over_trigger = bool(row["order_cols"])          # any order spec = route change
        if oc == "correct" and not over_trigger:
            return "PASS (still correct via the aggregate route, no order emitted)"
        if oc == "correct" and over_trigger:
            return ("WARN (outcome survived but Finding 7 OVER-TRIGGERED: order "
                    f"emitted on a scalar-aggregate query, order_by={row['order_cols']})")
        return (f"FAIL (stressor regressed to {oc}"
                f"{' with over-trigger order ' + str(row['order_cols']) if over_trigger else ''}"
                f": {reason[:60]})")
    # boundary = the S40 table-coverage residual (S40 OFF here). The honest
    # expectation is silent_wrong: the model proxies players-table concepts onto
    # real matches columns, produces a reachable goal, and answers — Unreachable
    # cannot catch it, only S40 can, and S40 is off for baseline comparability.
    # This is verified-as-refused by Phase R's S40-on variant. A refusal here
    # (oc==correct) is BETTER than expected and reported as such, not as failure.
    if g == "boundary":
        if oc == "silent_wrong" and exp == "silent_wrong_s40off":
            return ("EXPECTED-S40-RESIDUAL (out_of_scope_register answered via "
                    "proxied real columns; S40 OFF — refused correctly by Phase R's "
                    "S40-on variant; NOT a regression)")
        if oc == "correct":
            return (f"PASS/BETTER (refused with S40 off — something caught it: "
                    f"{str(row['error'])[:50]})")
        return (f"UNEXPECTED (boundary query {oc}, expected {exp}: {reason[:50]} "
                f"— diagnose, do not wave through)")


def main() -> int:
    check_wiring()
    print_plan_and_cost()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        print("This script makes live calls to the Anthropic API (see the cost estimate")
        print("above) and needs a real key to do that. Set it and re-run:")
        print("  export ANTHROPIC_API_KEY=sk-...")
        print("  python scripts/verify_spider_fixes_live.py")
        return 2

    from anthropic import Anthropic
    from evidence.eval_run.adapters.network import NetworkAdapter

    rows = []
    by_domain: dict[str, list] = {}
    for domain, qid, group, expected in ENTRIES:
        by_domain.setdefault(domain, []).append((qid, group, expected))

    for domain, items in by_domain.items():      # domain-outer: cache stays warm
        cfg, corpus, gold = load_domain(domain)
        register = pd.read_pickle(cfg["register"])
        briefing = load_briefing(cfg["schema"], cfg["register"])
        client = CachingClient(Anthropic(), cache=True)
        adapter = NetworkAdapter(name="network", client=client, register=register,
                                 briefing=briefing, model=MODEL,
                                 schema_path=cfg["schema"])
        for qid, group, expected in items:
            row = run_entry(adapter, domain, qid, group, expected, corpus, gold)
            rows.append(row)
            print(f"  [{group:<11s}] {domain:<9s} {qid:<9s} -> {verdict_for(row)}")

    with open(OUT_RESULTS_PATH, "w") as fh:      # overwrite, never append
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")

    print("\n" + "=" * 88)
    print("SUMMARY")
    print("=" * 88)
    for group, label in [("fix1_target", "FIX 1 TARGETS"),
                         ("fix2_target", "FIX 2 TARGETS (count_distinct)"),
                         ("fix2_stressor", "FIX 2 — PLAIN-COUNT OVER-TRIGGER STRESSORS"),
                         ("finding7_stressor", "FINDING 7 — OVER-TRIGGER STRESSORS"),
                         ("regression", "REGRESSION SET"),
                         ("boundary", "BOUNDARY SET")]:
        sub = [r for r in rows if r["group"] == group]
        n_ok = sum(1 for r in sub if verdict_for(r).startswith(
            ("PASS", "EXPECTED-PARTIAL", "EXPECTED-S40-RESIDUAL")))
        print(f"\n  {label}: {n_ok}/{len(sub)} at-or-above expectation")
        for r in sub:
            print(f"    {r['domain']:<9s} {r['qid']:<9s} {verdict_for(r)}")
            if r["group"] == "fix1_target":
                print(f"      order_by={r['order_cols']}  emitted columns={r['emitted_columns']}"
                      f"  derive={r['emitted_derive']}")
    n_bad = sum(1 for r in rows if verdict_for(r).startswith(("FAIL", "UNEXPECTED")))
    n_warn = sum(1 for r in rows if verdict_for(r).startswith("WARN"))
    if n_warn:
        print(f"\n  WARNINGS: {n_warn} over-trigger route change(s) — gate-clean but "
              f"review before Phase R (see FINDING 7 section above)")
    print(f"\n  GATE: {'PASS — every entry at or above its calibrated expectation' if n_bad == 0 else f'{n_bad} FAIL/UNEXPECTED — resolve before Phase R'}")
    print(f"  Full rows -> {OUT_RESULTS_PATH.name}")
    print("=" * 88)
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
