#!/usr/bin/env python
"""Live verification of the S40 REFUSE_OUT_OF_SCOPE_DATA instruction against the 3
known Spider refusal-tier failures diagnosed from the smoke test: wta_01, world_07,
bike_11 were all answered CONFIDENTLY by the network system instead of being declined,
even though each genuinely needs a table outside its domain's single-table register
(players / city / weather respectively). The diagnosis traced this to a real prompt
gap, not a grounding failure: no instruction anywhere told the model that declining
was an option for a MISSING-DATA reason (S28's REFUSE_UNSUPPORTED only covers missing
OPERATIONS, e.g. median/correlation, and was off by default anyway).

This script does NOT modify that diagnosis or re-litigate it -- it just checks whether
the new instruction (evidence/eval_run/refuse_config.py's REFUSE_OUT_OF_SCOPE_DATA
toggle) actually fixes the 3 known cases, and whether turning it on causes the model to
over-decline on queries that DON'T need missing data.

Two checks, in one run, toggle ON for both (REFUSE_UNSUPPORTED stays OFF throughout --
this measures ONLY the new instruction's effect, never S28's):

  1. TARGET check -- re-run wta_01 / world_07 / bike_11.
     PASS = EmissionDecline (status="refused", artifact.declined=True) with a reason
     that plausibly names the missing data. FAIL = still answers confidently.

  2. REGRESSION check -- re-run answer-eligible queries per domain, confirm they still
     answer (don't spuriously decline) and still score correct against gold. Two tiers
     of evidence, kept SEPARATE, not conflated:
       (a) PRIMARY = the 3 queries the smoke test already ran+confirmed correct with
           the toggle OFF (wta_02 / world_01 / bike_01) -- a TRUE regression check,
           has a known-good baseline to compare against.
       (b) SECONDARY = 4 more per domain, pulled fresh from the corpus -- these have
           NEVER been live-tested before (the smoke test only ran 2 queries/domain
           total), so there is no OFF-toggle baseline for them. A wrong VALUE on one
           of these does not unambiguously implicate the new instruction (the network
           system might just get that query wrong regardless). A DECLINE on one of
           these, however, IS directly attributable to the new instruction's mere
           presence (nothing else about the query or system changed) and is exactly
           the over-declining failure mode Step 4 of the task cares about -- reported
           as its own category, not lumped in with "wrong value".

Needs ANTHROPIC_API_KEY. This script was built, not run against a live model while
being built -- validating it with --dry-check does not require the key. Run it:

    python scripts/verify_refuse_out_of_scope.py

18 live Call-1 requests total (3 target + 3 primary-regression + 12 secondary-regression,
network system, seed 0, claude-sonnet-4-5 -- the same model/system/seed the Spider smoke
test itself used); see the cost estimate this script prints before it makes any of them.

Every row (target + primary + secondary) is persisted to results_verify_refuse_out_of_scope
.jsonl -- including the full artifact/result -- so a bug found in THIS script's own scoring
after the fact (as happened on the first live run: it read rec["raw_result"], a JSONL-log-
only key that doesn't exist on the dict adapter.run() actually returns, so every regression
row silently scored against None) can be re-diagnosed or re-scored without re-spending API
budget on the same 18 calls again.
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

from evidence.eval_run.comparator import score
from evidence.eval_run.pricing import cost_usd

MODEL = "claude-sonnet-4-5"            # same model the Spider smoke test used
SEED = 0
RESULTS_PATH = ROOT / "results/results_verify_refuse_out_of_scope.jsonl"

DOMAINS = {
    "wta_1": {
        "corpus": ROOT / "evaluation/domains/spider_wta_1/eval_corpus_spider_wta_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_wta_1.json",
        "register": ROOT / "evaluation/domains/spider_wta_1" / "matches.pkl",
        "schema": ROOT / "evaluation/domains/spider_wta_1" / "schema.json",
        "target": "wta_01",
        "primary": ["wta_02"],
        "secondary": ["wta_04", "wta_09", "wta_16", "wta_22"],
    },
    "world_1": {
        "corpus": ROOT / "evaluation/domains/spider_world_1/eval_corpus_spider_world_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_world_1.json",
        "register": ROOT / "evaluation/domains/spider_world_1" / "country.pkl",
        "schema": ROOT / "evaluation/domains/spider_world_1" / "schema.json",
        "target": "world_07",
        "primary": ["world_01"],
        "secondary": ["world_02", "world_04", "world_15", "world_19"],
    },
    "bike_1": {
        "corpus": ROOT / "evaluation/domains/spider_bike_1/eval_corpus_spider_bike_1.json",
        "gold": ROOT / "evidence" / "gold" / "gold_spider_bike_1.json",
        "register": ROOT / "evaluation/domains/spider_bike_1" / "trip.pkl",
        "schema": ROOT / "evaluation/domains/spider_bike_1" / "schema.json",
        "target": "bike_11",
        "primary": ["bike_01"],
        "secondary": ["bike_02", "bike_04", "bike_07", "bike_09"],
    },
}

# Empirical per-call size for the network system's Call-1, read directly from the
# recorded Spider smoke-test run (results_spider_smoke_*.jsonl) -- used ONLY for the cost
# estimate below. Real per-call cost varies 0.002-0.007 depending on raw_result size
# (bike_11's own unfiltered dump was ~1070 in-tok but the LARGE raw_result doesn't
# affect Call-1 pricing, only wall time) -- these are representative, not a promise.
EMPIRICAL_TOKENS = {"input": 1450, "output": 130}


def check_wiring() -> None:
    """Fail loudly and early if any domain's 4 files, target qid, or regression qids
    are missing -- before printing a cost estimate for calls that couldn't succeed."""
    for domain, cfg in DOMAINS.items():
        missing = [p for p in (cfg["corpus"], cfg["gold"], cfg["register"], cfg["schema"])
                  if not p.exists()]
        if missing:
            raise SystemExit(f"ERROR: missing {domain} file(s):\n  " +
                             "\n  ".join(str(p) for p in missing))
        corpus = json.loads(cfg["corpus"].read_text())
        ids_present = {q["id"] for q in corpus["queries"]}
        gold = json.loads(cfg["gold"].read_text())
        all_qids = [cfg["target"], *cfg["primary"], *cfg["secondary"]]
        for qid in all_qids:
            if qid not in ids_present:
                raise SystemExit(f"ERROR: {qid!r} not found in {cfg['corpus'].name}")
            if qid not in gold:
                raise SystemExit(f"ERROR: {qid!r} not found in {cfg['gold'].name}")
        if gold[cfg["target"]]["expected_behavior"] != "refuse":
            raise SystemExit(f"ERROR: target qid {cfg['target']!r} is not a refuse-case in gold")
        for qid in (*cfg["primary"], *cfg["secondary"]):
            if gold[qid]["expected_behavior"] != "answer":
                raise SystemExit(f"ERROR: regression qid {qid!r} is not an answer-case in gold")


def print_cost_estimate() -> None:
    n_target = sum(1 for cfg in DOMAINS.values())
    n_primary = sum(len(cfg["primary"]) for cfg in DOMAINS.values())
    n_secondary = sum(len(cfg["secondary"]) for cfg in DOMAINS.values())
    n_calls = n_target + n_primary + n_secondary
    per_call = cost_usd(MODEL, EMPIRICAL_TOKENS["input"], EMPIRICAL_TOKENS["output"])
    print("=" * 88)
    print("COST ESTIMATE (before any live call)")
    print("=" * 88)
    print(f"  TARGET:    {n_target} query  (wta_01, world_07, bike_11 -- expect DECLINE)")
    print(f"  PRIMARY:   {n_primary} query  (wta_02, world_01, bike_01 -- known-correct baseline)")
    print(f"  SECONDARY: {n_secondary} queries (4/domain, never live-tested before -- fresh spend)")
    print(f"  = {n_calls} live Call-1 requests x ~${per_call:.4f} (empirical, from the real smoke "
          f"test) = ~${per_call * n_calls:.4f}")
    print(f"  This is the CACHE-BLIND (worst-case) figure. All queries within a domain share the")
    print(f"  same static prefix, so with prompt caching (this script uses CachingClient, matching")
    print(f"  the harness) repeat calls within each domain should come in below this estimate.")
    print("=" * 88 + "\n")


def _persist_row(f, *, domain: str, bucket: str, qid: str, question: str,
                 out: dict, rec: dict | None) -> None:
    """Append the full row (out's summary fields + rec's artifact/result, when
    present) to RESULTS_PATH -- so a bug found in THIS script's own post-hoc
    scoring (or any future question about what a cell actually produced) can be
    re-diagnosed without re-spending API budget on the same call again."""
    row = {"domain": domain, "bucket": bucket, "qid": qid, "question": question, **out,
          "artifact": (rec or {}).get("artifact"), "result": (rec or {}).get("result")}
    f.write(json.dumps(row, default=str) + "\n")
    f.flush()


def run_query(adapter, qid: str, question: str) -> tuple[dict, dict | None]:
    """Always returns (out, rec) -- rec is None only on a script_error (e.g. a
    transport error NetworkAdapter.run() deliberately re-raises rather than
    swallowing), so callers must read fields from `out`, never `rec`, for anything
    that also needs to survive that path (status/error/cost). `rec` is for the
    completed-only extras (raw_result for scoring)."""
    try:
        rec = adapter.run(question, SEED)
    except Exception as exc:                                     # noqa: BLE001
        out = {"qid": qid, "status": "script_error", "error": f"{type(exc).__name__}: {exc}",
              "declined": None, "outcome_class": None, "comparator_reason": None,
              "cost_usd": 0.0}
        return out, None
    artifact = rec.get("artifact") or {}
    out = {"qid": qid, "status": rec.get("status"), "error": rec.get("error"),
          "declined": artifact.get("declined"), "outcome_class": None,
          "comparator_reason": None, "cost_usd": rec.get("cost_usd_cached", rec.get("cost_usd", 0.0))}
    return out, rec


def main() -> int:
    check_wiring()
    print_cost_estimate()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        print("This script makes live calls to the Anthropic API (see the cost estimate above)")
        print("and needs a real key to do that. Set it and re-run, e.g.:")
        print("  export ANTHROPIC_API_KEY=sk-...")
        print("  python scripts/verify_refuse_out_of_scope.py")
        return 2

    # Isolate the measurement to ONLY the new toggle -- if REFUSE_UNSUPPORTED happens
    # to be set in the calling shell (e.g. left over from an S28 experiment), a result
    # here would be ambiguous about which instruction caused it.
    os.environ.pop("REFUSE_UNSUPPORTED", None)
    os.environ["REFUSE_OUT_OF_SCOPE_DATA"] = "1"

    from anthropic import Anthropic
    from evidence.eval_run.logger import CachingClient
    from evidence.eval_run.adapters.network import NetworkAdapter

    target_rows, primary_rows, secondary_rows = [], [], []
    total_cost = 0.0

    # "w" (not "a") -- this is a one-shot check, not resumable like the harness; on a
    # second run, appending would duplicate rows without a skip-already-done mechanism
    # to make sense of it, so each run starts the file fresh.
    results_f = RESULTS_PATH.open("w")
    print(f"(persisting every row to {RESULTS_PATH} as it completes)\n")

    for domain, cfg in DOMAINS.items():
        print(f"--- {domain} ---")
        corpus = json.loads(cfg["corpus"].read_text())
        gold = json.loads(cfg["gold"].read_text())
        q_by_id = {q["id"]: q for q in corpus["queries"]}
        register = pd.read_pickle(cfg["register"])
        client = CachingClient(Anthropic(), cache=True)
        adapter = NetworkAdapter(name="network", client=client, register=register,
                                 briefing=None, model=MODEL, schema_path=cfg["schema"])

        # TARGET — expect EmissionDecline
        qid = cfg["target"]
        question = q_by_id[qid]["question"]
        out, rec = run_query(adapter, qid, question)
        total_cost += out["cost_usd"]
        verdict = "PASS (declined)" if out["declined"] else f"FAIL (status={out['status']!r})"
        print(f"  [TARGET  {qid}] {verdict} -- reason={out['error']!r}")
        _persist_row(results_f, domain=domain, bucket="target", qid=qid, question=question, out=out, rec=rec)
        target_rows.append(out)

        # PRIMARY regression — known-correct baseline, toggle now ON
        for qid in cfg["primary"]:
            question = q_by_id[qid]["question"]
            out, rec = run_query(adapter, qid, question)
            total_cost += out["cost_usd"]
            if out["status"] == "completed":
                oc, reason, _ = score(rec.get("result"), gold[qid], status="completed")
                out["outcome_class"], out["comparator_reason"] = oc, reason
            tag = ("DECLINED (regression!)" if out["declined"]
                   else f"{out['outcome_class']} -- {out['comparator_reason']}" if out["outcome_class"]
                   else f"status={out['status']!r}")
            print(f"  [PRIMARY {qid}] {tag}")
            _persist_row(results_f, domain=domain, bucket="primary", qid=qid, question=question, out=out, rec=rec)
            primary_rows.append(out)

        # SECONDARY regression — fresh spend, no OFF-toggle baseline
        for qid in cfg["secondary"]:
            question = q_by_id[qid]["question"]
            out, rec = run_query(adapter, qid, question)
            total_cost += out["cost_usd"]
            if out["status"] == "completed":
                oc, reason, _ = score(rec.get("result"), gold[qid], status="completed")
                out["outcome_class"], out["comparator_reason"] = oc, reason
            tag = ("DECLINED (over-decline signal!)" if out["declined"]
                   else f"{out['outcome_class']} -- {out['comparator_reason']}" if out["outcome_class"]
                   else f"status={out['status']!r}")
            print(f"  [2ndary  {qid}] {tag}")
            _persist_row(results_f, domain=domain, bucket="secondary", qid=qid, question=question, out=out, rec=rec)
            secondary_rows.append(out)
        print()

    results_f.close()

    print("=" * 88)
    print("SUMMARY")
    print("=" * 88)
    n_pass = sum(1 for r in target_rows if r["declined"])
    print(f"TARGET (expect decline): {n_pass}/{len(target_rows)} PASS")
    for r in target_rows:
        if not r["declined"]:
            print(f"  FAIL: {r['qid']} did not decline (status={r['status']!r})")

    def _bucket_counts(rows: list[dict]) -> dict[str, int]:
        """Every row lands in exactly ONE bucket, so counts always sum to len(rows) --
        no row silently missing from the printed breakdown (the gap in the first live
        run's summary: structural refusals were neither 'declined' nor scored, so
        they weren't counted anywhere)."""
        c = {"declined": 0, "correct": 0, "wrong": 0, "structural_refuse": 0, "other": 0}
        for r in rows:
            if r["declined"]:
                c["declined"] += 1
            elif r["outcome_class"] == "correct":
                c["correct"] += 1
            elif r["outcome_class"] is not None:
                c["wrong"] += 1
            elif r["status"] == "refused":            # validate_goal/order/lint/plan/FilterTypeError
                c["structural_refuse"] += 1            # -- refused, but NOT via the new instruction
            else:                                       # crashed / script_error
                c["other"] += 1
        return c

    pc = _bucket_counts(primary_rows)
    print(f"\nPRIMARY regression (known-correct baseline): {len(primary_rows)} queries")
    print(f"  {pc['correct']} still correct")
    print(f"  {pc['declined']} newly DECLINED via the new instruction  <- should be 0")
    print(f"  {pc['wrong']} answered but scored non-correct (not a decline)")
    print(f"  {pc['structural_refuse']} refused via a PRE-EXISTING structural gate "
          f"(validate_goal/order/lint/plan) -- unrelated to this toggle")
    print(f"  {pc['other']} crashed / script error")

    sc = _bucket_counts(secondary_rows)
    print(f"\nSECONDARY regression (fresh spend, no baseline): {len(secondary_rows)} queries")
    print(f"  {sc['correct']} correct")
    print(f"  {sc['declined']} DECLINED via the new instruction  <- directly attributable "
          f"over-declining signal, should be 0")
    print(f"  {sc['wrong']} answered but scored non-correct  <- NOT necessarily this fix's fault; "
          f"these queries were never live-tested with the toggle OFF either")
    print(f"  {sc['structural_refuse']} refused via a PRE-EXISTING structural gate -- unrelated to "
          f"this toggle (would refuse the same way with it OFF)")
    print(f"  {sc['other']} crashed / script error")

    print(f"\nActual spend this run: ${total_cost:.4f}")
    print(f"Full rows (incl. artifact/result) persisted to: {RESULTS_PATH}")
    print("=" * 88)

    overall = (n_pass == len(target_rows) and pc["declined"] == 0 and sc["declined"] == 0)
    print(f"\n{'PASS' if overall else 'NEEDS REVIEW'}: target fix "
          f"{'confirmed, no over-declining (via the new instruction) detected' if overall else 'incomplete or over-declining seen — see rows above'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
