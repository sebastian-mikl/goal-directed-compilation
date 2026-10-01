#!/usr/bin/env python
"""Post-Fix-1 re-run of the T4/T5 "network" x Haiku benchmark cells.

Context: the diagnosed finding was 26/26 "network" silent-error rows at T4 under
Haiku, all caused by a record_type over-filter on generic phrasing. Fix 1
(business_rules `aliases` on schema.json, rendered generically by
gdc/schema_bridge.py + gdc/llm.py) removes that filter from the rendered
prompt on both Sonnet and Haiku.

SCOPE (deliberately narrow — see the docstring of `main` for the full rationale):
  system  = network only
  model   = claude-haiku-4-5 (Call-1 emission model) only
  tiers   = T4_business_rule_multi_op + T5_high_complexity only
  seeds   = 0-4 (matching results_haiku.jsonl's existing shape)
That's 30 qids x 5 seeds = 150 (qid, seed) cells = 150 live API calls.

Writes to results_haiku_post_fix1.jsonl — a NEW file; no existing results file
is read for anything other than the before/after comparison, and none is
modified or overwritten.

Needs ANTHROPIC_API_KEY:

    python scripts/rerun_t4_t5_haiku_post_fix1.py
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

from evidence.eval_run.harness import DEFAULTS, load_corpus
from evidence.eval_run.run import select_qids
from evidence.eval_run.pricing import cost_usd

TIERS = ["T4_business_rule_multi_op", "T5_high_complexity"]
SYSTEMS = ["network"]
SEEDS = [0, 1, 2, 3, 4]

# Matches provenance_haiku.json exactly (the config the ORIGINAL Haiku run used) --
# a run default of Sonnet, with the Call-1 emission model overridden to Haiku for
# the (network, planner) EMISSION_SYSTEMS. Only "network" is in SYSTEMS above, so
# `model` is never actually exercised here, but it's kept identical for provenance
# parity with the original run.
MODEL = "claude-sonnet-4-5"
EMISSION_MODEL = "claude-haiku-4-5"

OUT_RESULTS_PATH = ROOT / "results/results_haiku_post_fix1.jsonl"
OUT_PROVENANCE_PATH = ROOT / "results/results_haiku_post_fix1.provenance.json"

# Empirical per-call size for the "network" system under Haiku, read directly from
# this repo's own results_haiku.jsonl per-call token ledger. Used ONLY for the cost
# estimate below.
EMPIRICAL_INPUT_TOKENS = 2026
EMPIRICAL_OUTPUT_TOKENS = 176


def find_canonical_before_file() -> Path:
    """The current canonical pre-Fix-1 Haiku results file, verified at runtime
    rather than assumed: results_haiku.rescored.jsonl is canonical IFF it exists,
    has the same row count as results_haiku.jsonl, and scores identically to it
    (0 outcome_class differences on every shared (system, qid, seed) key) -- i.e.
    the rescore made no changes, so either file is equally valid and the rescored
    one is preferred as the more explicitly s36-verified of the two. If that
    parity check fails, this prints exactly what differs and falls back to the
    raw file with a clear warning, rather than silently trusting a stale premise.
    """
    raw = ROOT / "results/results_haiku.jsonl"
    rescored = ROOT / "results/results_haiku.rescored.jsonl"
    if not raw.exists():
        raise SystemExit(f"ERROR: {raw} not found -- cannot establish a before-baseline.")
    if not rescored.exists():
        print(f"NOTE: {rescored} not found; using {raw} as the before-baseline directly.")
        return raw

    def load(path):
        d = {}
        with path.open() as f:
            for line in f:
                r = json.loads(line)
                d[(r["system"], r["qid"], r["seed"])] = r["outcome_class"]
        return d

    a, b = load(raw), load(rescored)
    diffs = sum(1 for k in a if k in b and a[k] != b[k])
    if len(a) == len(b) and diffs == 0:
        print(f"Canonical before-file check: {rescored.name} matches {raw.name} exactly "
              f"({len(a)} rows, 0 outcome_class differences) -- using {rescored.name}.")
        return rescored
    print(f"WARNING: {rescored.name} and {raw.name} are NOT identical "
          f"(rows: {len(a)} vs {len(b)}, {diffs} outcome_class differences).")
    print(f"         Falling back to {raw.name} -- verify which file is actually canonical "
          f"before trusting the before/after comparison below.")
    return raw


def load_before_rows(before_path: Path) -> dict[tuple[str, int], dict]:
    """{(qid, seed): full record} for tier in TIERS, system == 'network', from the
    canonical before-file."""
    out = {}
    with before_path.open() as f:
        for line in f:
            r = json.loads(line)
            if r["system"] == "network" and r["tier"] in TIERS:
                out[(r["qid"], r["seed"])] = r
    return out


def print_cost_estimate(n_cells: int) -> None:
    print("=" * 88)
    print("COST ESTIMATE (before any live call)")
    print("=" * 88)
    print(f"  {len(select_qids_preview())} qids (15 T4 + 15 T5) x {len(SEEDS)} seeds x "
          f"{len(SYSTEMS)} system = {n_cells} live Call-1 requests")
    print(f"  (NOT 750 -- 150 is already qids x seeds; there is only one system, network, in scope)\n")
    per_call = cost_usd(EMISSION_MODEL, EMPIRICAL_INPUT_TOKENS, EMPIRICAL_OUTPUT_TOKENS)
    total = per_call * n_cells
    print(f"  {EMISSION_MODEL}: ~{EMPIRICAL_INPUT_TOKENS} in / ~{EMPIRICAL_OUTPUT_TOKENS} out tok/call "
          f"(empirical, from results_haiku.jsonl's own per-call token ledger)")
    print(f"  {n_cells} calls x ~${per_call:.4f} = ~${total:.4f} TOTAL")
    print(f"  Cache-blind (worst-case) figure -- the system prompt is byte-identical across all")
    print(f"  {n_cells} calls, so with prompt caching engaged (this reuses run_cells(), which already")
    print(f"  wraps the client in CachingClient) the real bill should come in below this estimate.")
    print("=" * 88 + "\n")


def select_qids_preview() -> list[str]:
    corpus = load_corpus(DEFAULTS["corpus_path"])
    return select_qids(corpus, tiers=TIERS)


def run_post_fix1() -> dict:
    """Live run: T4+T5, network only, Haiku emission model, seeds 0-4. Reuses
    run_cells() exactly as the harness's own CLI would invoke it -- same corpus,
    gold, register, schema defaults, same bounds/cache config as the original
    Haiku run (see provenance_haiku.json). Resumable: if OUT_RESULTS_PATH already
    has some cells logged (e.g. a prior partial run), run_cells() skips them and
    tops up -- it will not re-pay for or duplicate a completed cell."""
    from anthropic import Anthropic
    from evidence.eval_run.harness import run_cells

    corpus = load_corpus(DEFAULTS["corpus_path"])
    qids = select_qids(corpus, tiers=TIERS)

    def factory():
        return Anthropic()

    return run_cells(
        qids=qids, seeds=SEEDS, systems=SYSTEMS,
        client_factory=factory, model=MODEL, emission_model=EMISSION_MODEL,
        temperature=None, use_cache=True,
        results_path=OUT_RESULTS_PATH, provenance_path=OUT_PROVENANCE_PATH,
    )


def load_after_rows(path: Path) -> dict[tuple[str, int], dict]:
    out = {}
    with path.open() as f:
        for line in f:
            r = json.loads(line)
            out[(r["qid"], r["seed"])] = r
    return out


def print_comparison(before: dict[tuple[str, int], dict], after: dict[tuple[str, int], dict]) -> None:
    print("=" * 88)
    print("BEFORE / AFTER -- tier summary")
    print("=" * 88)
    print(f"{'tier':<28s} {'system':<9s} {'model':<18s} {'old acc%':>9s} {'old serr%':>10s} "
          f"{'new acc%':>9s} {'new serr%':>10s} {'n_flip->ok':>11s} {'n_regressed':>12s}")
    print("-" * 88)
    for tier in TIERS:
        old_rows = [v for (qid, seed), v in before.items() if v["tier"] == tier]
        new_rows = [after[k] for k in before if before[k]["tier"] == tier and k in after]
        n_old = len(old_rows)
        n_new = len(new_rows)
        old_acc = 100.0 * sum(1 for r in old_rows if r["outcome_class"] == "correct") / n_old if n_old else float("nan")
        old_serr = 100.0 * sum(1 for r in old_rows if r["outcome_class"] == "silent_wrong") / n_old if n_old else float("nan")
        new_acc = 100.0 * sum(1 for r in new_rows if r["outcome_class"] == "correct") / n_new if n_new else float("nan")
        new_serr = 100.0 * sum(1 for r in new_rows if r["outcome_class"] == "silent_wrong") / n_new if n_new else float("nan")

        n_flipped_to_ok = 0
        n_regressed = 0
        still_failing = []
        for (qid, seed), old_r in before.items():
            if old_r["tier"] != tier:
                continue
            new_r = after.get((qid, seed))
            if new_r is None:
                continue
            old_oc, new_oc = old_r["outcome_class"], new_r["outcome_class"]
            if old_oc == "silent_wrong" and new_oc == "correct":
                n_flipped_to_ok += 1
            elif old_oc == "correct" and new_oc != "correct":
                n_regressed += 1
            if new_oc == "silent_wrong":
                still_failing.append(qid)

        print(f"{tier:<28s} {'network':<9s} {EMISSION_MODEL:<18s} {old_acc:>9.1f} {old_serr:>10.1f} "
              f"{new_acc:>9.1f} {new_serr:>10.1f} {n_flipped_to_ok:>11d} {n_regressed:>12d}")
        if still_failing:
            print(f"    still silent_wrong after the re-run: {sorted(set(still_failing))}")
    print()


def print_known_26_check(before: dict[tuple[str, int], dict], after: dict[tuple[str, int], dict]) -> None:
    """The specific number the paper needs to cite: of the rows the ORIGINAL
    diagnosis flagged as the record_type-over-filter failure (T4, network, Haiku,
    outcome_class == silent_wrong in the canonical before-file), how many are
    outcome_class == "correct" now? This is computed from the before-file at
    runtime, not a hardcoded qid list, so it stays correct even if the canonical
    file's contents ever change (at the time this script was built: 26 rows)."""
    known_bad = {k: v for k, v in before.items()
                 if v["tier"] == "T4_business_rule_multi_op" and v["outcome_class"] == "silent_wrong"}
    print("=" * 88)
    print(f"THE 26-ROW CHECK (T4, network, Haiku, previously-diagnosed record_type over-filter)")
    print("=" * 88)
    print(f"  Found {len(known_bad)} rows matching the diagnosis in the before-file "
          f"{'(matches the paper-cited count of 26)' if len(known_bad) == 26 else '-- NOTE: expected 26, verify before citing'}.")
    n_now_pass = 0
    still_bad = []
    missing = []
    for (qid, seed) in known_bad:
        new_r = after.get((qid, seed))
        if new_r is None:
            missing.append((qid, seed))
            continue
        if new_r["outcome_class"] == "correct":
            n_now_pass += 1
        else:
            still_bad.append((qid, seed, new_r["outcome_class"]))
    print(f"  Now PASS (outcome_class == correct): {n_now_pass} / {len(known_bad)}")
    if still_bad:
        print(f"  Still NOT correct: {still_bad}")
    if missing:
        print(f"  Missing from the re-run output (unexpected -- investigate): {missing}")
    print("=" * 88 + "\n")


def main() -> int:
    print(__doc__)

    before_path = find_canonical_before_file()
    before = load_before_rows(before_path)
    n_t4 = sum(1 for v in before.values() if v["tier"] == "T4_business_rule_multi_op")
    n_t5 = sum(1 for v in before.values() if v["tier"] == "T5_high_complexity")
    print(f"Loaded before-baseline: {before_path.name} -- {n_t4} T4 rows + {n_t5} T5 rows "
          f"(network system) = {len(before)} total.\n")

    qids = select_qids_preview()
    n_cells = len(qids) * len(SEEDS) * len(SYSTEMS)
    print(f"Will run: {len(qids)} qids ({sum(1 for q in qids if q.startswith('t4'))} T4 + "
          f"{sum(1 for q in qids if q.startswith('t5'))} T5) x {len(SEEDS)} seeds x "
          f"{len(SYSTEMS)} system ({SYSTEMS[0]}, {EMISSION_MODEL}) = {n_cells} cells.")
    print(f"Output -> {OUT_RESULTS_PATH.name} (new file; nothing existing is modified).\n")

    print_cost_estimate(n_cells)

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        print("This script makes live calls to the Anthropic API (see the cost estimate above)")
        print("and needs a real key to do that. Set it and re-run, e.g.:")
        print("  export ANTHROPIC_API_KEY=sk-...")
        print("  python scripts/rerun_t4_t5_haiku_post_fix1.py")
        return 2

    summary = run_post_fix1()
    print(f"\n=== run_cells: {summary['ran']} ran, {summary['skipped']} skipped"
          f"{' — ABORTED' if summary.get('aborted') else ''} ===\n")

    after = load_after_rows(OUT_RESULTS_PATH)
    print_comparison(before, after)
    print_known_26_check(before, after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
