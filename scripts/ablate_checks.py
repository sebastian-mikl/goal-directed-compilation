#!/usr/bin/env python
"""Component ablation of the static checks (REPLAY-only, no API calls).

Each recorded network emission carries its (Goal, OrderSpec) in `artifact`, so
the entire deterministic half of the pipeline is re-runnable offline. This
harness replays EVERY recorded network emission across all 5 domains through
the current engine (a) with all checks ON (baseline), and (b) with exactly ONE
check disabled at a time, re-scoring under the current comparator (s48). Comparing each
ablation to the baseline isolates that one check's contribution.

The five checks (from §3.2 + satisfies()):
  contract   validate_goal   — aggregate/grain consistency
  order      validate_order  — every ranking column is an output column
  lint       lint_goal       — dead-derive (a derived value nothing consumes)
  exact_col  satisfies()     — EXACT (not subset) column equality in the search
                               satisfaction test  (monkeypatched to subset)
  type_guard FilterTypeError — runtime type guard on filters (ablated = the
                               typed refusal is not caught, so it surfaces as a
                               crash instead — models "no honest-refusal guard").
                               Only the FilterTypeError root is the type guard; its
                               subclasses are reported under their own class name
                               (`typed:<Class>`) and are not ablated.
  ('contract' is validate_goal as a whole: since s49 that includes the
   undeclared-function and compute-grammar rules.)
Plus one combined run: all three declarative lints (contract+order+lint) off.

Attribution per gate check X (contract/order/lint):
  - a goal refused in baseline with proximate reason X: when X is off, does it
    (a) get re-caught by a LATER check (SHARED — X was redundant there), or
    (b) proceed to a produced answer (UNIQUE — only X was stopping it)?
  - of the unique ones: silent_wrong (the check prevented a silent error —
    load-bearing) vs correct (the check was over-refusing — false-positive cost).

exact_col and type_guard don't map onto a proximate-reason label the same way,
so for those the FULL baseline->ablated outcome flip matrix is reported.

Pure local: no API key, no network. Reuses scripts/replay_goals.py machinery.

In this release the replayed emissions are the employee and Spider ones (the risk files are
withheld, so the risk domain contributes none). reports/ablation_data.json is written by the
--json command below, not by run_all.sh; it holds the employee and Spider part of the
paper's ablation, so its totals are smaller than those of a run that also has the risk data.

Usage:  python scripts/ablate_checks.py            # prints tables, writes JSON
        python scripts/ablate_checks.py --json reports/ablation_data.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import gdc.search as search                              # noqa: E402
from gdc.contract import lint_goal, validate_goal        # noqa: E402
from gdc.integrate import (execute_plan_ordered,         # noqa: E402
                               OrderApplicationError, PlanExecutionDivergence)
from gdc.order import validate_order                     # noqa: E402
from gdc.search import Unreachable, plan                 # noqa: E402
from gdc.state import (Goal, State, satisfies as _exact_satisfies,  # noqa: E402
                           effective_output_columns, _canon_where, _canon_derive)
from gdc.primitives import FilterTypeError               # noqa: E402
from evidence.eval_run.adapters.base import df_to_result     # noqa: E402
from evidence.eval_run.comparator import score               # noqa: E402
from evidence.eval_run.types import REFUSAL                  # noqa: E402

import replay_goals as rg                                    # noqa: E402
from scripts import paper_inputs as pi                       # noqa: E402


# Network-emission files matching the paper's provenance (paper_numbers.md +
# build_paper_numbers.py syn_rows): risk network = results_off for every tier
# EXCEPT T5, and results_t5 for T5 ONLY (results_t5 is a superset of results_off
# for the non-T5 tiers — 61 of its goals differ — so taking all of it would
# double-count; the builder takes T5 from it alone). employee = replay of
# results_employee. Spider = post-fix Phase R, s40off (the variant that ATTEMPTS
# out-of-scope queries, so the static checks' behaviour on boundary goals is
# visible rather than pre-empted by the S40 table-coverage decline).
# Each file is read as the builder reads it (scripts/paper_inputs.py): with the
# temperature-0 overlays under PARITY_TEMP0=1 and with the tier of record. The
# risk files are withheld from this release and skipped.
# Each entry: (filename, domain, tier_filter or None-for-all).
ABLATION_FILES = [
    ("results_off.rescored.jsonl",                  "risk",     "not_T5"),
    ("results_t5.rescored.jsonl",                   "risk",     "T5_high_complexity"),
    ("results_employee.jsonl",                      "employee", None),
    ("results_spider_wta_1_postfix_s40off.jsonl",   "wta_1",    None),
    ("results_spider_world_1_postfix_s40off.jsonl", "world_1",  None),
    ("results_spider_bike_1_postfix_s40off.jsonl",  "bike_1",   None),
]

VARIANTS = ["baseline", "no_contract", "no_order", "no_lint",
            "no_exact_col", "no_type_guard", "all_lints_off"]


def _subset_satisfies(state: State, goal: Goal) -> bool:
    """satisfies() with the column test relaxed from EXACT equality to SUBSET
    (the pre-Fix-3 behaviour). grain/where/derive tests are unchanged."""
    if (goal.grouped_by is None) != (state.grouped_by is None):
        return False
    if goal.grouped_by is not None:
        if set(map(str, goal.grouped_by)) != set(map(str, state.grouped_by)):
            return False
    if not (effective_output_columns(goal) <= state.column_names):   # subset, not ==
        return False
    sw = {tuple(t) for t in _canon_where(state.where)}
    gw = {tuple(t) for t in _canon_where(goal.where)}
    if not gw <= sw:
        return False
    sd = {tuple(t) for t in _canon_derive(state.derive)}
    gd = {tuple(t) for t in _canon_derive(goal.derive)}
    return gd <= sd


def run_det(ctx, goal: Goal, order, variant: str):
    """Replay the deterministic pipeline with ONE check disabled (or none).
    Returns (status, result, reason) where reason is the proximate refusal
    category for baseline attribution."""
    order_cols = [c for c, _ in order.order_by]
    drop = {variant} if variant != "all_lints_off" else {"no_contract", "no_order", "no_lint"}

    if "no_contract" not in drop:
        if not validate_goal(goal).ok:
            return "refused", REFUSAL, "contract"
    if "no_order" not in drop:
        if not validate_order(order, goal).ok:
            return "refused", REFUSAL, "order"
    if "no_lint" not in drop:
        if not lint_goal(goal, order_cols=order_cols).ok:
            return "refused", REFUSAL, "lint"

    if variant == "no_exact_col":
        search.satisfies = _subset_satisfies
    try:
        p = plan(State.from_base(ctx.base_schema), goal)
    finally:
        search.satisfies = _exact_satisfies
    if isinstance(p, Unreachable):
        return "refused", REFUSAL, "unreachable"

    try:
        frame = execute_plan_ordered(p, ctx.adf, order)
        frame = frame.rename(columns={c: ctx.amap_obj.to_real(c)
                                      for c in frame.columns
                                      if c in ctx.amap_obj.aliases})
    except FilterTypeError as exc:
        # The type guard is the FilterTypeError root: a filter comparison the
        # column cannot take. Its subclasses (absent column, compute, aggregate,
        # join, sort, op) are other fail-closed checks; without them the old engine
        # failed open rather than crashed, so ablating the type guard leaves them
        # in place and they keep their own reason.
        if type(exc) is not FilterTypeError:
            return "refused", REFUSAL, f"typed:{type(exc).__name__}"
        if variant == "no_type_guard":
            # guard ablated: the typed refusal is gone -> surfaces as a crash
            return "crashed", REFUSAL, "crash_from_filter_type"
        return "refused", REFUSAL, "filter_type"
    except OrderApplicationError:
        return "refused", REFUSAL, "order_app"
    except PlanExecutionDivergence:
        return "refused", REFUSAL, "plan_divergence"
    except Exception as exc:                                   # noqa: BLE001
        return "crashed", REFUSAL, f"crash:{type(exc).__name__}"
    return "completed", df_to_result(frame), None


def network_rows():
    """(file, domain, ctx, row) for every network row of ABLATION_FILES, read as the
    builder reads it (paper_inputs: overlays, tier of record), tier filter applied."""
    for fname, domain, tier_filter in ABLATION_FILES:
        if not (ROOT / "results" / fname).exists():
            continue
        ctx = rg.DomainContext.get(domain)
        for r in pi.retier(pi.load(fname), pi.GOLD[domain]):
            if r.get("system") != "network":
                continue
            tier = r.get("tier")
            if tier_filter == "not_T5" and tier == "T5_high_complexity":
                continue
            if tier_filter not in (None, "not_T5") and tier != tier_filter:
                continue
            yield fname, domain, ctx, r


def replay_all(variant: str) -> dict:
    """Replay every network emission under one variant. Returns
    {(file,qid,seed): {'domain','tier','status','outcome','reason'}}."""
    out = {}
    for fname, domain, ctx, r in network_rows():
        art = r.get("artifact") or {}
        if not isinstance(art, dict) or not art.get("goal"):
            continue
        qid, seed = r["qid"], r.get("seed")
        gold_rec = ctx.gold.get(qid)
        if gold_rec is None:
            continue
        try:
            goal = rg.rebuild_goal(art["goal"])
            order = rg.rebuild_order(art.get("order") or {})
        except Exception:                                  # noqa: BLE001
            continue
        status, result, reason = run_det(ctx, goal, order, variant)
        outcome, _reason, _ = score(result, gold_rec, status=status)
        out[(fname, qid, seed)] = {
            "domain": domain, "tier": r.get("tier"),
            "status": status, "outcome": outcome, "reason": reason}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)

    print("Replaying variants (no API calls) ...")
    runs = {}
    for v in VARIANTS:
        runs[v] = replay_all(v)
        n = len(runs[v])
        oc = Counter(x["outcome"] for x in runs[v].values())
        print(f"  {v:15s} {n} emissions   outcomes={dict(oc)}")

    base = runs["baseline"]
    keys = list(base.keys())

    # Baseline breakdown per domain
    print("\n=== BASELINE outcome breakdown per domain (all checks on) ===")
    dom_break = defaultdict(Counter)
    for k, v in base.items():
        dom_break[v["domain"]][v["outcome"]] += 1
    for dom in ["risk", "employee", "wta_1", "world_1", "bike_1"]:
        print(f"  {dom:9s} {dict(dom_break[dom])}")
    print("  baseline proximate refusal reasons:",
          dict(Counter(v["reason"] for v in base.values() if v["reason"])))

    # Per-check attribution (gate checks: contract/order/lint)
    print("\n=== PER-CHECK ATTRIBUTION ===")
    gate = {"no_contract": "contract", "no_order": "order", "no_lint": "lint"}
    table = {}
    for variant, reason in gate.items():
        abl = runs[variant]
        attributed = [k for k in keys if base[k]["reason"] == reason]
        became_silent = became_correct = shared = 0
        for k in attributed:
            a = abl[k]
            if a["status"] == "refused":
                shared += 1                      # re-caught by a later check
            elif a["outcome"] == "silent_wrong":
                became_silent += 1
            elif a["outcome"] == "correct":
                became_correct += 1
            else:
                shared += 1                      # crashed etc. -> still not an answer
        unique = became_silent + became_correct
        table[reason] = dict(attributed=len(attributed), unique=unique,
                             silent=became_silent, correct=became_correct, shared=shared)
        print(f"  {reason:9s} attributed={len(attributed):3d}  unique={unique:3d}  "
              f"(->silent={became_silent}, ->correct={became_correct})  shared={shared}")

    # exact_col and type_guard: full baseline->ablated flip matrix
    print("\n=== exact_col / type_guard: baseline -> ablated outcome flips ===")
    flip = {}
    for variant in ["no_exact_col", "no_type_guard", "all_lints_off"]:
        abl = runs[variant]
        fm = Counter()
        for k in keys:
            b, a = base[k]["outcome"], abl[k]["outcome"]
            if b != a:
                fm[(b, a)] += 1
        flip[variant] = {f"{b}->{a}": n for (b, a), n in sorted(fm.items())}
        new_silent = sum(n for (b, a), n in fm.items() if a == "silent_wrong")
        print(f"  {variant:15s} new_silent_wrong={new_silent:3d}  flips={flip[variant]}")

    # Per-domain net effect of each ablation (correct/silent/refused counts)
    def breakdown(run):
        d = defaultdict(Counter)
        for k, v in run.items():
            cat = ("silent" if v["outcome"] == "silent_wrong"
                   else "correct" if v["outcome"] == "correct"
                   else "refused" if str(v["outcome"]).startswith("refused")
                   else v["status"])
            d[v["domain"]][cat] += 1
        return d

    if args.json_out:
        payload = {
            "files": ABLATION_FILES,
            "baseline_domain_breakdown": {d: dict(c) for d, c in dom_break.items()},
            "baseline_refusal_reasons": dict(Counter(
                v["reason"] for v in base.values() if v["reason"])),
            "attribution": table,
            "flip_matrices": flip,
            "variant_totals": {v: dict(Counter(x["outcome"] for x in runs[v].values()))
                               for v in VARIANTS},
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=1, default=str))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
