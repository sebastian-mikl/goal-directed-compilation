#!/usr/bin/env python
"""S24 — full evaluation run: 5 systems × N seeds × M queries over the frozen
corpus, streamed + scored to results.jsonl, with an aggregate report.

This is the paper run. It is RESUMABLE and CRASH-SAFE: each completed cell is
appended to results.jsonl the moment it finishes (and skipped on restart), and a
transport/API failure mid-run (credits exhausted, rate limit, connection drop)
ABORTS cleanly WITHOUT recording the in-flight cell — so re-running the SAME
command tops up exactly where it stopped and retries the failed cell, never
double-paying for completed cells and never silently skipping the failed one.

Usage:
  python -m evidence.eval_run.run                       # all 80 q × 5 seeds × 5 systems (LIVE)
  python -m evidence.eval_run.run --seeds 0             # one seed (chunked; bounded blast radius)
  python -m evidence.eval_run.run --systems network planner
  python -m evidence.eval_run.run --tiers T1_single_op  # one tier
  python -m evidence.eval_run.run --queries t1_07 t4_06
  python -m evidence.eval_run.run --no-cache            # pre-caching baseline
  python -m evidence.eval_run.run --report-only         # re-render the report from results.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                                              # noqa: BLE001
    pass

from evidence.eval_run.harness import run_cells, load_corpus, load_gold, DEFAULTS  # noqa: E402
from evidence.eval_run.comparator import score  # noqa: E402
from evidence.eval_run.types import REFUSAL  # noqa: E402

SYSTEMS = ("network", "planner", "react", "code_agent", "text_to_sql")
DEFAULT_SEEDS = (0, 1, 2, 3, 4)


def select_qids(corpus: dict, *, queries=None, tiers=None) -> list[str]:
    """The qids to run: explicit --queries, or all qids in the given --tiers, or
    ALL 80 (corpus order)."""
    if queries:
        missing = [q for q in queries if q not in corpus]
        if missing:
            raise SystemExit(f"unknown query ids: {missing}")
        return list(queries)
    qids = list(corpus.keys())
    if tiers:
        tset = set(tiers)
        qids = [q for q in qids if corpus[q].get("tier") in tset]
        if not qids:
            raise SystemExit(f"no queries in tiers {tiers}")
    return qids


def _read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


# The out_of_range "correct = refuse" rule mis-scores general-tool baselines
# (code_agent/text_to_sql can legitimately do most oor ops), so they are EXCLUDED
# from the boundary sub-study to keep the headline silent-error metric clean.
PRIMITIVE_SYSTEMS = ("network", "planner", "react")

# S41 — tiers whose gold marks expected_behavior="refuse": "correct" means the
# system declined. Each gets its OWN dedicated sub-study section below (never
# merged with another, even though the reporting shape is identical), because the
# tier design's whole point is that the underlying refusal MECHANISM differs per
# tier (out_of_range = an unsupported OPERATION the 4-primitive Goal contract can't
# express, e.g. median/correlation; out_of_scope_register = data outside the
# domain's single visible register table). Both restricted to PRIMITIVE_SYSTEMS
# here, mirroring the original out_of_range convention. Worth reconsidering for
# out_of_scope_register specifically before it's relied on at scale: the exclusion
# rationale is that code_agent/text_to_sql have a genuine operation-availability
# escape hatch out_of_range's boundary lacks (ordinary pandas/SQL built-ins) — but
# out_of_scope_register's boundary is DATA availability, and code_agent/text_to_sql
# see the exact same register.pkl as every other system, so there's no analogous
# mis-scoring risk to exclude them for. Left as the safe default pending an
# explicit decision, not changed unilaterally here.
REFUSAL_BOUNDARY_TIERS = ("out_of_range", "out_of_scope_register")

# S41 — tiers whose gold stays expected_behavior="answer" but where the Goal/plan
# contract the primitive systems (network/planner/react) rely on may be structurally
# unable to express a correct goal at all (out_of_contract_aggregate: HAVING-style
# post-aggregate filters / aggregate subqueries / multiple simultaneous aggregates —
# see the diagnosis). NOT part of the refusal-vs-in-range binary — the finding here
# is the refused/silently_wrong/correct SPLIT itself (per-query behavior varies), so
# each gets a dedicated 3-way breakdown instead of one folded-in accuracy number.
# ALL systems present are shown, deliberately not restricted to PRIMITIVE_SYSTEMS:
# code_agent/text_to_sql have no Goal/plan contract at all, so scoring them the same
# as any other answer-tier query is exactly the comparison this tier exists to make
# (expect them to route around the gap; expect network/planner/react to rarely if
# ever score correct here, by construction).
ARCHITECTURAL_GAP_TIERS = ("out_of_contract_aggregate",)


# S36 FIX 3a — T5 is a separate high-complexity sub-study (S34: 15 in-range
# queries, complexity_ops 5–8) and must NOT be folded into the in-range headline,
# which is the T1–T4 + house_vocab story all five systems were designed around.
# Conservative match: anything starting with "T5" is the T5 axis.
def _is_t5(tier: str) -> bool:
    return isinstance(tier, str) and tier.startswith("T5")


def _special_tier(tier: str) -> bool:
    """True for any tier reported in its own dedicated sub-study rather than folded
    into the plain in-range headline block — the single membership check both the
    in-range filter and (implicitly, via the per-tier loops below) every sub-study
    section are built from, so a new tier only needs adding to ONE of the two
    registries above, never a third hardcoded string comparison."""
    return tier in REFUSAL_BOUNDARY_TIERS or tier in ARCHITECTURAL_GAP_TIERS or _is_t5(tier)


def headline_report(results_path: Path) -> dict:
    """Headline (S36, extended S41):
      • IN-RANGE (T1–T4 + house_vocab, or a domain's equivalent ordinary tiers) —
        accuracy + silent-error, ALL 5 systems. Everything not in one of the
        registries above and not T5-prefixed.
      • one REFUSAL-BOUNDARY sub-study PER tier in REFUSAL_BOUNDARY_TIERS present
        in the data — restricted to {network, planner, react} (where
        'correct = refuse' is meaningful).
      • one ARCHITECTURAL-GAP sub-study PER tier in ARCHITECTURAL_GAP_TIERS present
        in the data — ALL systems, refused/silently_wrong/correct 3-way.
      • T5 SUB-STUDY — high-complexity tier (S34, complexity_ops 5–8), rendered
        only when T5 cells are present, all 5 systems."""
    recs = _read_jsonl(results_path)
    if not recs:
        print("\n(no results yet for headline)")
        return {}

    def _block(rows):
        by = defaultdict(lambda: Counter())
        for r in rows:
            by[r["system"]][r["outcome_class"]] += 1
        out = {}
        for s in sorted(by):
            c = by[s]; n = sum(c.values())
            out[s] = {"n": n, "correct": c["correct"], "silent_wrong": c["silent_wrong"],
                      "acc": c["correct"] / n if n else 0.0,
                      "silent_err": c["silent_wrong"] / n if n else 0.0,
                      "classes": dict(c)}
        return out

    def _gap_block(rows):
        """refused/silently_wrong/correct 3-way for an ARCHITECTURAL_GAP_TIERS
        section — the outcome_class -> bucket mapping the s37 comparator already
        produces for an expected_behavior='answer' query, just relabeled for this
        tier's specific question (does the system refuse honestly, answer wrong
        confidently, or find a way to answer right)."""
        by = defaultdict(lambda: Counter())
        for r in rows:
            by[r["system"]][r["outcome_class"]] += 1
        out = {}
        for s in sorted(by):
            c = by[s]; n = sum(c.values())
            refused = c["refused_should_answer"]
            other = n - c["correct"] - c["silent_wrong"] - refused
            out[s] = {"n": n, "correct": c["correct"], "silent_wrong": c["silent_wrong"],
                      "refused": refused, "other": other, "classes": dict(c)}
        return out

    in_range = [r for r in recs if not _special_tier(r["tier"])]
    t5 = [r for r in recs if _is_t5(r["tier"])]

    print("\n" + "=" * 78)
    print("HEADLINE — IN-RANGE (T1–T4 + house_vocab), all systems")
    print("=" * 78)
    print(f"  {'system':<12s} {'n':>4s} {'acc':>7s} {'silent_err':>11s}   classes")
    hr = _block(in_range)
    for s, d in hr.items():
        print(f"  {s:<12s} {d['n']:>4d} {d['acc']:>7.1%} {d['silent_err']:>11.1%}   {d['classes']}")
    out = {"in_range": hr}

    for boundary_tier in REFUSAL_BOUNDARY_TIERS:
        oor = [r for r in recs if r["tier"] == boundary_tier and r["system"] in PRIMITIVE_SYSTEMS]
        if not oor:
            continue
        print("\n" + "-" * 78)
        print(f"BOUNDARY SUB-STUDY — {boundary_tier}, primitive systems {{network, planner, react}}")
        print("  (code_agent / text_to_sql EXCLUDED: 'correct=refuse' mis-scores general tools)")
        print("-" * 78)
        print(f"  {'system':<12s} {'n':>4s} {'acc(refuse)':>12s} {'silent_wrong':>13s}")
        orr = _block(oor)
        for s, d in orr.items():
            print(f"  {s:<12s} {d['n']:>4d} {d['acc']:>12.1%} {d['silent_wrong']:>13d}")
        out[boundary_tier] = orr

    for gap_tier in ARCHITECTURAL_GAP_TIERS:
        gap_rows = [r for r in recs if r["tier"] == gap_tier]
        if not gap_rows:
            continue
        print("\n" + "-" * 78)
        print(f"ARCHITECTURAL-GAP SUB-STUDY — {gap_tier}, all systems")
        print("  (gold stays expected_behavior=answer; refused/silently_wrong/correct IS the")
        print("   finding — network/planner/react should rarely score correct here by")
        print("   construction, code_agent/text_to_sql have no such Goal/plan contract limit)")
        print("-" * 78)
        print(f"  {'system':<12s} {'n':>4s} {'correct':>8s} {'refused':>8s} "
              f"{'silent_wrong':>13s} {'other':>6s}")
        gb = _gap_block(gap_rows)
        for s, d in gb.items():
            print(f"  {s:<12s} {d['n']:>4d} {d['correct']:>8d} {d['refused']:>8d} "
                  f"{d['silent_wrong']:>13d} {d['other']:>6d}")
        out[gap_tier] = gb

    if t5:
        print("\n" + "-" * 78)
        print("T5 SUB-STUDY — high-complexity (S34, complexity_ops 5–8), all systems")
        print("-" * 78)
        print(f"  {'system':<12s} {'n':>4s} {'acc':>7s} {'silent_err':>11s}   classes")
        t5b = _block(t5)
        for s, d in t5b.items():
            print(f"  {s:<12s} {d['n']:>4d} {d['acc']:>7.1%} {d['silent_err']:>11.1%}   {d['classes']}")
        out["t5"] = t5b
    return out


def report(results_path: Path) -> None:
    """Aggregate report over EVERY record in results.jsonl (the full run so far):
    per-system correctness / calls / tokens / $ + outcome-class + per-tier breakdown."""
    recs = _read_jsonl(results_path)
    print("\n" + "=" * 78)
    print(f"FULL-RUN REPORT — {len(recs)} cells in {results_path}")
    print("=" * 78)
    if not recs:
        print("  (no results yet)")
        return

    seeds = sorted({r["seed"] for r in recs})
    qids = sorted({r["qid"] for r in recs})
    print(f"  systems: {sorted({r['system'] for r in recs})}")
    print(f"  seeds: {seeds}   queries: {len(qids)}")

    # ── per-system aggregate ─────────────────────────────────────────────────
    print(f"\n  {'system':<12s} {'cells':>5s} {'corr':>5s} {'acc':>6s} "
          f"{'calls/q':>8s} {'raw_in/q':>9s} {'out/q':>7s} {'$cache':>8s} {'$raw':>8s}")
    print("  " + "-" * 74)
    by_sys: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_sys[r["system"]].append(r)
    tot = {"cells": 0, "corr": 0, "cache": 0.0, "raw": 0.0}
    for s in sorted(by_sys):
        rs = by_sys[s]
        n = len(rs)
        corr = sum(1 for r in rs if r["outcome_class"] == "correct")
        calls = sum(r["llm_calls"] for r in rs) / n
        rawin = sum(r["input_tokens"] for r in rs) / n
        outt = sum(r["output_tokens"] for r in rs) / n
        cache = sum(r.get("cost_usd_cached", r["cost_usd"]) for r in rs)
        raw = sum(r.get("cost_usd_raw", r["cost_usd"]) for r in rs)
        print(f"  {s:<12s} {n:>5d} {corr:>5d} {corr / n:>6.1%} "
              f"{calls:>8.2f} {rawin:>9.0f} {outt:>7.0f} {cache:>8.3f} {raw:>8.3f}")
        tot["cells"] += n; tot["corr"] += corr; tot["cache"] += cache; tot["raw"] += raw
    print("  " + "-" * 74)
    print(f"  {'TOTAL':<12s} {tot['cells']:>5d} {tot['corr']:>5d} "
          f"{(tot['corr'] / tot['cells'] if tot['cells'] else 0):>6.1%} "
          f"{'':>8s} {'':>9s} {'':>7s} {tot['cache']:>8.3f} {tot['raw']:>8.3f}")

    # ── outcome-class breakdown per system ───────────────────────────────────
    print("\n  outcome classes per system:")
    for s in sorted(by_sys):
        cc = Counter(r["outcome_class"] for r in by_sys[s])
        print(f"    {s:<12s} {dict(cc)}")

    # ── per-tier accuracy (system × tier) ────────────────────────────────────
    tiers = sorted({r["tier"] for r in recs})
    print(f"\n  accuracy by tier:")
    print(f"    {'system':<12s} " + " ".join(f"{t[:10]:>11s}" for t in tiers))
    for s in sorted(by_sys):
        cells = {(r["qid"], r["seed"]): r for r in by_sys[s]}
        line = f"    {s:<12s} "
        for t in tiers:
            tr = [r for r in by_sys[s] if r["tier"] == t]
            if tr:
                acc = sum(1 for r in tr if r["outcome_class"] == "correct") / len(tr)
                line += f"{acc:>10.0%} "
            else:
                line += f"{'-':>10s} "
        print(line)

    # ── completion vs the requested grid (if known) ──────────────────────────
    print(f"\n  NOTE: paper cost metrics = calls + RAW tokens (cache-blind, $raw); "
          f"$cache is the practical bill.")

    # ── headline (in-range, all systems) + boundary sub-study (oor, primitives) ──
    headline_report(results_path)


def _sha256(path: Path) -> str:
    import hashlib
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:                                          # noqa: BLE001
        return "<unavailable>"


def rescore(results_path: Path, gold_path: Path, *, out_path: Path | None = None,
            provenance_path: Path = DEFAULTS["provenance_path"]) -> dict:
    """Recompute outcome_class + comparator verdict for EVERY record in an existing
    results.jsonl using the CURRENT gold.json + CURRENT classifier (status-aware),
    WITHOUT re-running anything (no API). Writes a NEW file (never overwrites the
    source). Crashed/timeout cells classify as crash/timeout and are flagged
    needs_rerun. Records OLD (from provenance) + NEW gold SHA-256 (V5)."""
    recs = _read_jsonl(results_path)
    gold = load_gold(gold_path)
    out_path = Path(out_path) if out_path else Path(str(results_path).replace(".jsonl", "") + ".rescored.jsonl")

    old_gold_sha = "<unknown>"
    if Path(provenance_path).exists():
        try:
            old_gold_sha = json.loads(Path(provenance_path).read_text()).get("gold_sha256", "<unknown>")
        except Exception:                                      # noqa: BLE001
            pass
    new_gold_sha = _sha256(gold_path)

    changed, needs_rerun = 0, []
    # per-system flip ledger (S36): correct->non-correct cells carry their NEW class +
    # reason (each one is a previously inflated score — triage material); the reverse
    # direction carries the OLD class (a previously deflated score now recognised).
    flips: dict[str, dict[str, list]] = defaultdict(lambda: {"correct_to_wrong": [],
                                                             "wrong_to_correct": []})
    out_recs = []
    for r in recs:
        g = gold.get(r["qid"])
        new = dict(r)
        if g is None:
            new["rescore_note"] = "qid not in gold"
            out_recs.append(new); continue
        verdict, reason, tag = score(r.get("raw_result"), g, status=r.get("status", "completed"))
        old = r.get("outcome_class")
        if verdict != old:
            changed += 1
            if old == "correct" and verdict != "correct":
                flips[r["system"]]["correct_to_wrong"].append(
                    {"qid": r["qid"], "seed": r["seed"], "new": verdict, "reason": reason})
            elif old != "correct" and verdict == "correct":
                flips[r["system"]]["wrong_to_correct"].append(
                    {"qid": r["qid"], "seed": r["seed"], "old": old})
        new.update({"comparator_verdict": verdict, "comparator_reason": reason,
                    "outcome_class": verdict, "failure_tag": tag,
                    "gold_value": g["value"], "needs_rerun": verdict in ("crash", "timeout")})
        if new["needs_rerun"]:
            needs_rerun.append((r["system"], r["qid"], r["seed"]))
        out_recs.append(new)

    with Path(out_path).open("w") as f:
        for r in out_recs:
            f.write(json.dumps(r, default=str) + "\n")
    prov = {"rescored_from": str(results_path), "rescored_to": str(out_path),
            "old_gold_sha256": old_gold_sha, "new_gold_sha256": new_gold_sha,
            "n_records": len(recs), "n_outcome_changed": changed, "n_needs_rerun": len(needs_rerun),
            "flips_per_system": {s: dict(f) for s, f in sorted(flips.items())}}
    Path(str(out_path).replace(".jsonl", "") + ".provenance.json").write_text(json.dumps(prov, indent=2))

    print(f"\n=== RESCORE: {len(recs)} records -> {out_path} (no API) ===")
    print(f"  gold OLD sha256: {old_gold_sha}")
    print(f"  gold NEW sha256: {new_gold_sha}  ({'CHANGED' if old_gold_sha != new_gold_sha else 'same'})")
    print(f"  outcome_class changed on rescore: {changed} / {len(recs)}")
    print("\n  cell flips per system (old verdicts vs CURRENT comparator):")
    if not flips:
        print("    (none)")
    for s in sorted(flips):
        f = flips[s]
        print(f"    {s:<12s} correct->wrong: {len(f['correct_to_wrong']):>3d}   "
              f"wrong->correct: {len(f['wrong_to_correct']):>3d}")
        for c in f["correct_to_wrong"]:                  # every revoked win is listed
            print(f"      - {c['qid']} s{c['seed']} -> {c['new']}: {c['reason'][:90]}")
        for c in f["wrong_to_correct"][:10]:
            print(f"      + {c['qid']} s{c['seed']} (was {c['old']})")
        if len(f["wrong_to_correct"]) > 10:
            print(f"      … (+{len(f['wrong_to_correct']) - 10} more wrong->correct)")
    print(f"\n  needs re-run (crash/timeout — no stored answer): {len(needs_rerun)}")
    for s, q, sd in needs_rerun[:20]:
        print(f"    - {s} {q} s{sd}")
    if len(needs_rerun) > 20:
        print(f"    … (+{len(needs_rerun) - 20} more)")
    report(out_path)
    return prov


def lint_rescore(results_path: Path, *, out_path: Path | None = None) -> dict:
    """S35 — re-apply the goal validity lints (dead derive + order closure) to the
    RECORDED artifact.goal/artifact.order of an existing results file, WITHOUT
    re-running anything (no API). Only records that carry an emitted goal (the
    network's) are lintable; others pass through untouched.

    A lint-violated cell is reclassified to what the live path would now produce:
    the goal is refused BEFORE execution, so the cell becomes status=refused and is
    re-scored as a refusal against gold (in-range -> refused_should_answer — honest,
    NOT correct; out-of-range -> correct). The report shows, per system, the
    silent_wrong -> refused reclassifications (the safety gain) AND any currently-
    correct cells the lint would have blocked (the cost) — measured both ways."""
    from gdc.contract import lint_goal, CONTRACT_VERSION
    from gdc.order import OrderSpec, validate_order
    from gdc.state import Goal

    recs = _read_jsonl(results_path)
    out_path = Path(out_path) if out_path else Path(str(results_path).replace(".jsonl", "") + ".lint_rescored.jsonl")
    summary: dict[str, dict] = {}
    out_recs = []
    for r in recs:
        sys_name = r["system"]
        s = summary.setdefault(sys_name, {"cells": 0, "with_goal": 0, "lint_violations": 0,
                                          "silent_wrong_to_refused": 0, "correct_hit_by_lint": 0,
                                          "by_prior_class": Counter(), "violation_reasons": []})
        s["cells"] += 1
        art = r.get("artifact") or {}
        goal_d = art.get("goal")
        if not isinstance(goal_d, dict):
            out_recs.append(r); continue
        s["with_goal"] += 1
        try:
            goal = Goal.make(
                columns=[tuple(c) if isinstance(c, (list, tuple)) else c for c in goal_d["columns"]],
                grain=goal_d["grain"],
                where=[{"col": w[0], "op": w[1], "val": w[2]} if isinstance(w, (list, tuple)) else w
                       for w in goal_d["where"]],
                derive=goal_d["derive"])
        except Exception as exc:                               # noqa: BLE001
            new = dict(r); new["lint_note"] = f"goal not rebuildable: {exc}"
            out_recs.append(new); continue
        order_d = art.get("order") or {}
        spec = OrderSpec.make(order_by=order_d.get("order_by") or [], limit=order_d.get("limit"))
        lv = lint_goal(goal, order_cols=[c for c, _ in spec.order_by])
        ov = validate_order(spec, goal)
        if lv.ok and ov.ok:
            out_recs.append(r); continue

        reason = lv.reason if not lv.ok else ov.reason
        prior = r.get("outcome_class")
        s["lint_violations"] += 1
        s["by_prior_class"][prior] += 1
        s["violation_reasons"].append(f"{r['qid']} s{r.get('seed')}: [{prior}] {reason}")
        if prior == "correct":
            s["correct_hit_by_lint"] += 1
        if prior == "silent_wrong":
            s["silent_wrong_to_refused"] += 1
        gold_rec = {"expected_behavior": r["expected_behavior"],
                    "result_kind": r["result_kind"], "value": r["gold_value"]}
        verdict, vreason, tag = score(REFUSAL, gold_rec, status="refused")
        new = dict(r)
        new.update({"status": "refused", "raw_result": REFUSAL,
                    "outcome_class": verdict, "comparator_verdict": verdict,
                    "comparator_reason": f"goal lint ({CONTRACT_VERSION}): {reason}",
                    "failure_tag": tag, "error": f"GoalLint: {reason}",
                    "lint_reclassified": True, "prior_outcome_class": prior})
        out_recs.append(new)

    with Path(out_path).open("w") as f:
        for r in out_recs:
            f.write(json.dumps(r, default=str) + "\n")

    print(f"\n=== LINT-RESCORE ({results_path} -> {out_path}; no API) ===")
    print(f"  {'system':<12s} {'cells':>5s} {'goals':>5s} {'lint_viol':>9s} "
          f"{'sw->refused':>11s} {'correct_hit':>11s}   prior classes of violations")
    for sys_name in sorted(summary):
        s = summary[sys_name]
        s["by_prior_class"] = dict(s["by_prior_class"])        # JSON/print friendly
        print(f"  {sys_name:<12s} {s['cells']:>5d} {s['with_goal']:>5d} {s['lint_violations']:>9d} "
              f"{s['silent_wrong_to_refused']:>11d} {s['correct_hit_by_lint']:>11d}   {s['by_prior_class']}")
    for sys_name in sorted(summary):
        for line in summary[sys_name]["violation_reasons"][:20]:
            print(f"    - {sys_name}: {line}")
    print("  NOTE: a reclassified in-range cell becomes refused_should_answer — an honest")
    print("  refusal, NOT a correct answer. 'correct_hit' counts answers the lint would")
    print("  have blocked (the cost side); report both transparently.")
    return summary


# ── gold audit: cross-system agreement that disagrees with gold ──────────────

_AUDIT_SYSTEMS = ("network", "code_agent", "text_to_sql")


def _agree(a, b, kind: str) -> bool:
    """Shape-tolerant equality of two produced results, via the comparator (treat b
    as gold)."""
    return score(a, {"result_kind": kind, "value": b, "expected_behavior": "answer"})[0] == "correct"


def _correct_vs_gold(a, gold_rec) -> bool:
    return score(a, gold_rec, status="completed")[0] == "correct"


def gold_audit(results_path: Path, gold_path: Path,
               out_path: Path = Path("gold_audit_report.txt")) -> list[str]:
    """Flag qids where >=2 of {network, code_agent, text_to_sql} produce mutually-
    agreeing COMPLETED answers that DISAGREE with gold — gold-bug candidates.
    Excludes refusals/crashes/timeouts. (Sparse until a full multi-system run.)"""
    recs = _read_jsonl(results_path)
    gold = load_gold(gold_path)
    by_qid: dict[str, dict[str, list]] = {}
    for r in recs:
        if r.get("system") not in _AUDIT_SYSTEMS:
            continue
        if r.get("status") != "completed" or r.get("raw_result") in (None, REFUSAL):
            continue
        by_qid.setdefault(r["qid"], {}).setdefault(r["system"], []).append(r["raw_result"])

    flagged, lines = [], ["GOLD AUDIT — cross-system agreement that disagrees with gold",
                          "(>=2 of {network, code_agent, text_to_sql} agree with each other but",
                          " not with gold -> gold-bug candidate; refusals/crashes excluded)", ""]
    for qid in sorted(by_qid):
        g = gold.get(qid)
        if not g or g.get("expected_behavior") != "answer":
            continue
        kind = g["result_kind"]
        # one representative completed result per system (first seed)
        sysres = {s: v[0] for s, v in by_qid[qid].items()}
        systems = list(sysres)
        agree_group = None
        for i in range(len(systems)):
            for j in range(i + 1, len(systems)):
                si, sj = systems[i], systems[j]
                if _agree(sysres[si], sysres[sj], kind) and not _correct_vs_gold(sysres[si], g):
                    agree_group = (si, sj)
                    break
            if agree_group:
                break
        if agree_group:
            flagged.append(qid)
            si, sj = agree_group
            lines.append(f"[{qid}] {si} & {sj} agree with each other but NOT gold")
            lines.append(f"    {si}: {str(sysres[si])[:120]}")
            lines.append(f"    gold: {str(g['value'])[:120]}")
            lines.append("")
    if not flagged:
        lines.append("No gold-bug candidates found "
                     f"({len(by_qid)} qids had >=1 completed result from the audit systems).")
    Path(out_path).write_text("\n".join(lines))
    print(f"\n=== GOLD AUDIT -> {out_path} ===")
    print(f"  gold-bug candidates: {flagged if flagged else 'none'} "
          f"({len(by_qid)} qids audited)")
    return flagged


def main():
    ap = argparse.ArgumentParser(description="S24 full evaluation run (resumable, crash-safe)")
    ap.add_argument("--seeds", nargs="+", type=int, default=list(DEFAULT_SEEDS))
    ap.add_argument("--systems", nargs="+", default=list(SYSTEMS))
    ap.add_argument("--queries", nargs="+", default=None, help="explicit qids (else all/by-tier)")
    ap.add_argument("--tiers", nargs="+", default=None,
                    help="restrict to these gold tiers (e.g. T1_single_op out_of_range)")
    ap.add_argument("--model", default=DEFAULTS["model"])
    ap.add_argument("--emission-model", default=None,
                    help="S33 downsizing probe: Call-1 goal/plan emission model for the "
                         "network+planner only (default: --model). Overrides EMISSION_MODEL env.")
    ap.add_argument("--no-cache", action="store_true", help="disable prompt caching")
    ap.add_argument("--results", default=str(DEFAULTS["results_path"]))
    ap.add_argument("--provenance", default=str(DEFAULTS["provenance_path"]))
    ap.add_argument("--report-only", action="store_true",
                    help="don't run; just re-render the aggregate report from results.jsonl")
    ap.add_argument("--rescore", action="store_true",
                    help="don't run; recompute verdicts from stored raw_result vs the CURRENT "
                         "gold + classifier into a NEW file (no API)")
    ap.add_argument("--lint-rescore", action="store_true",
                    help="don't run; re-apply the S35 goal validity lints to recorded "
                         "artifact.goal and report silent_wrong->refused reclassifications "
                         "into a NEW file (no API)")
    ap.add_argument("--audit", action="store_true",
                    help="don't run; write gold_audit_report.txt (cross-system agreement vs gold)")
    ap.add_argument("--gold", default=str(DEFAULTS["gold_path"]), help="gold.json path")
    ap.add_argument("--register", default=str(DEFAULTS["register_path"]), help="register.pkl path")
    ap.add_argument("--schema", default=str(DEFAULTS["schema_path"]), help="schema.json path")
    ap.add_argument("--out", default=None, help="output path for --rescore / --audit")
    args = ap.parse_args()

    results_path = Path(args.results)
    if args.report_only:
        report(results_path)
        return
    if args.rescore:
        rescore(results_path, Path(args.gold),
                out_path=Path(args.out) if args.out else None,
                provenance_path=Path(args.provenance))
        return
    if args.lint_rescore:
        lint_rescore(results_path, out_path=Path(args.out) if args.out else None)
        return
    if args.audit:
        gold_audit(results_path, Path(args.gold),
                   out_path=Path(args.out) if args.out else Path("gold_audit_report.txt"))
        return

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: the full run is LIVE and needs ANTHROPIC_API_KEY. Aborting.")
        sys.exit(2)

    corpus = load_corpus(DEFAULTS["corpus_path"])
    qids = select_qids(corpus, queries=args.queries, tiers=args.tiers)
    n_cells = len(qids) * len(args.seeds) * len(args.systems)
    print(f"Full run: {len(args.systems)} systems × {len(args.seeds)} seeds × {len(qids)} queries "
          f"= {n_cells} cells (caching {'OFF' if args.no_cache else 'ON'}).")
    print(f"  systems={args.systems}  seeds={args.seeds}")
    print(f"  results -> {results_path}  (resumable: already-logged cells are skipped)")

    from anthropic import Anthropic

    def factory(): return Anthropic()

    summary = run_cells(
        qids=qids, seeds=list(args.seeds), systems=list(args.systems),
        client_factory=factory, model=args.model, emission_model=args.emission_model,
        use_cache=not args.no_cache,
        results_path=results_path, provenance_path=Path(args.provenance),
        register_path=Path(args.register), schema_path=Path(args.schema),
    )
    print(f"\n=== run_cells: {summary['ran']} ran, {summary['skipped']} skipped"
          f"{' — ABORTED' if summary.get('aborted') else ''} ===")
    report(results_path)
    if summary.get("aborted"):
        ab = summary["abort"]
        print(f"\n*** RUN ABORTED at [{ab['system']} {ab['qid']} s{ab['seed']}]: {ab['error']}")
        print("*** Completed cells are saved. Re-run the SAME command to resume "
              "(the aborted cell retries; finished cells are skipped).")
        sys.exit(3)


if __name__ == "__main__":
    main()
