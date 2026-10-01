#!/usr/bin/env python
"""Offline goal-replay harness (SPIDER_FIX_PLAN.md Phase 0).

Every network-system results row records the emitted (Goal, OrderSpec) in its
artifact. That makes the ENTIRE deterministic half of the network pipeline —
validate_goal -> validate_order -> lint_goal -> plan() -> execute ->
apply_order -> df_to_result -> comparator — re-runnable offline against
CURRENT code, with zero API cost, across the full recorded history. This is
the regression gate for every deterministic-side fix (satisfies() exact-match,
DISTINCT, date parsing, goal normalization): after each engine change, re-run
replay and demand that ONLY the intended rows flip.

Two independent comparison axes per row, so every discrepancy is attributable:
  RESCORE axis: current comparator applied to the RECORDED raw_result.
                Differs from the recorded outcome_class only on comparator
                drift (the file predates a comparator fix).
  REPLAY axis:  current engine re-executes the recorded goal; current
                comparator scores the fresh result. Differs from RESCORE only
                on engine behavior change.

Classification per replayed row:
  clean               replay outcome == recorded outcome, value digest equal
  value_drift         same outcome, different produced value (listed, not a flip)
  comparator_drift    outcome differs, but value identical AND rescoring the
                      recorded value under the current comparator produces the
                      same new outcome -> attributable purely to comparator
                      version, not to engine behavior
  engine_flip         outcome differs and the engine produced a different
                      value/status -> a real behavioral change (must be an
                      INTENDED fix effect, or the phase stops)

Rows that cannot be replayed are counted, never silently dropped:
  non-network rows (other systems don't emit goals), EmissionDecline /
  ParseError rows (no goal recorded), goal-unparseable rows, missing gold.

Usage:
    python scripts/replay_goals.py                      # all 7 known files
    python scripts/replay_goals.py results_employee.jsonl
    python scripts/replay_goals.py some.jsonl --domain wta_1
    python scripts/replay_goals.py --json out.json      # machine-readable dump

Pure local: no API key, no network, reads results/registers/gold only, writes
nothing unless --json is given.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from gdc.contract import lint_goal, validate_goal          # noqa: E402
from gdc.integrate import (aliased_view, execute_plan_ordered,  # noqa: E402
                               OrderApplicationError, PlanExecutionDivergence)
from gdc.order import OrderSpec, validate_order            # noqa: E402
from gdc.schema_bridge import build_bridge                 # noqa: E402
from gdc.search import Unreachable, plan                   # noqa: E402
from gdc.state import Goal, State                          # noqa: E402
from gdc.primitives import FilterTypeError                 # noqa: E402
from evidence.eval_run.adapters.base import df_to_result       # noqa: E402
from evidence.eval_run.comparator import score                 # noqa: E402
from evidence.eval_run.types import REFUSAL                    # noqa: E402


DOMAIN_CONFIGS = {
    "risk": {
        "register": ROOT / "environment" / "register.pkl",
        "schema":   ROOT / "environment" / "schema.json",
        "gold":     ROOT / "evidence" / "gold" / "gold.json",
    },
    "employee": {
        "register": ROOT / "evaluation/domains/employee" / "employees.pkl",
        "schema":   ROOT / "evaluation/domains/employee" / "schema.json",
        "gold":     ROOT / "evidence" / "gold" / "gold_employee.json",
    },
    "wta_1": {
        "register": ROOT / "evaluation/domains/spider_wta_1" / "matches.pkl",
        "schema":   ROOT / "evaluation/domains/spider_wta_1" / "schema.json",
        "gold":     ROOT / "evidence" / "gold" / "gold_spider_wta_1.json",
    },
    "world_1": {
        "register": ROOT / "evaluation/domains/spider_world_1" / "country.pkl",
        "schema":   ROOT / "evaluation/domains/spider_world_1" / "schema.json",
        "gold":     ROOT / "evidence" / "gold" / "gold_spider_world_1.json",
    },
    "bike_1": {
        "register": ROOT / "evaluation/domains/spider_bike_1" / "trip.pkl",
        "schema":   ROOT / "evaluation/domains/spider_bike_1" / "schema.json",
        "gold":     ROOT / "evidence" / "gold" / "gold_spider_bike_1.json",
    },
}

# The Phase 0 target set (SPIDER_FIX_PLAN.md): full recorded network history.
FILE_DOMAINS = {
    "results_off.rescored.jsonl":     "risk",
    "results_haiku.rescored.jsonl":   "risk",
    "results_haiku_post_fix1.jsonl":  "risk",
    "results_employee.jsonl":         "employee",
    "results_spider_wta_1.jsonl":     "wta_1",
    "results_spider_world_1.jsonl":   "world_1",
    "results_spider_bike_1.jsonl":    "bike_1",
}


class DomainContext:
    """Deterministic-pipeline context for one domain, built exactly the way
    NetworkAdapter.__init__ builds it (aliased view + bridge; the briefing /
    business-rules parts feed only the LLM prompt and are not needed here)."""

    def __init__(self, name: str):
        cfg = DOMAIN_CONFIGS[name]
        self.name = name
        register = pd.read_pickle(cfg["register"])
        self.amap, self.adf, self.base_schema = aliased_view(register)
        self.amap_obj, _schema_view, _rules = build_bridge(
            list(register.columns), schema_path=cfg["schema"])
        self.gold = json.loads(cfg["gold"].read_text())

    _cache: dict = {}

    @classmethod
    def get(cls, name: str) -> "DomainContext":
        if name not in cls._cache:
            cls._cache[name] = cls(name)
        return cls._cache[name]


def rebuild_goal(goal_dict: dict) -> Goal:
    """Invert network.py::_goal_to_dict. `where` is recorded as [col, op, val]
    triples; Goal.make wants {'col','op','val'} dicts."""
    return Goal.make(
        columns=goal_dict["columns"],
        grain=goal_dict["grain"],
        where=[{"col": c, "op": o, "val": v} for c, o, v in goal_dict["where"]],
        derive=goal_dict["derive"],
    )


def rebuild_order(order_dict: dict) -> OrderSpec:
    return OrderSpec.make(order_by=order_dict.get("order_by") or (),
                          limit=order_dict.get("limit"))


def run_deterministic(ctx: DomainContext, goal: Goal, order: OrderSpec):
    """Mirror NetworkAdapter.run()'s post-emission section exactly.
    Returns (status, result, error_label)."""
    gv = validate_goal(goal)
    if not gv.ok:
        return "refused", REFUSAL, f"ContractError(goal): {gv.reason}"
    ov = validate_order(order, goal)
    if not ov.ok:
        return "refused", REFUSAL, f"ContractError(order): {ov.reason}"
    lv = lint_goal(goal, order_cols=[c for c, _ in order.order_by])
    if not lv.ok:
        return "refused", REFUSAL, f"GoalLint: {lv.reason}"
    p = plan(State.from_base(ctx.base_schema), goal)
    if isinstance(p, Unreachable):
        return "refused", REFUSAL, f"Unreachable: {p.reason}"
    try:
        frame = execute_plan_ordered(p, ctx.adf, order)
        frame = frame.rename(columns={c: ctx.amap_obj.to_real(c)
                                      for c in frame.columns
                                      if c in ctx.amap_obj.aliases})
    except FilterTypeError as exc:
        # The class name, as NetworkAdapter.run records it (P15f), so a
        # ComputeExprError or AggregateTypeError is not labelled FilterTypeError.
        return "refused", REFUSAL, f"{type(exc).__name__}: {exc}"
    except OrderApplicationError as exc:
        return "refused", REFUSAL, f"OrderApplicationError: {exc}"
    except PlanExecutionDivergence as exc:
        return "refused", REFUSAL, f"PlanExecutionDivergence: {exc}"
    except Exception as exc:                                   # noqa: BLE001
        return "crashed", REFUSAL, f"executor: {type(exc).__name__}: {exc}"
    return "completed", df_to_result(frame), None


def logger_view(value):
    """The logger serialises records with json.dumps(default=str); recorded
    raw_result values have been through that transform (np.int64 -> "123"
    strings, etc). Apply the identical transform to fresh values so digests
    compare the same representation."""
    return json.loads(json.dumps(value, default=str))


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(logger_view(value), sort_keys=True, default=str)
        .encode("utf-8")).hexdigest()[:16]


def replay_file(path: Path, domain: str) -> dict:
    ctx = DomainContext.get(domain)
    skipped = Counter()
    classes = Counter()
    flips: dict[tuple, list] = defaultdict(list)
    details = []           # one dict per non-clean row, for the --json dump

    with open(path) as fh:
        rows = [json.loads(l) for l in fh if l.strip()]

    for r in rows:
        if r.get("system") != "network":
            skipped["non_network"] += 1
            continue
        art = r.get("artifact") or {}
        if not isinstance(art, dict) or not art.get("goal"):
            skipped["declined_or_parse_error" if art.get("declined") or art.get("error")
                    else "no_goal_other"] += 1
            continue
        qid, seed = r["qid"], r.get("seed")
        gold_rec = ctx.gold.get(qid)
        if gold_rec is None:
            skipped["missing_gold"] += 1
            continue
        try:
            goal = rebuild_goal(art["goal"])
            order = rebuild_order(art.get("order") or {})
        except Exception as exc:                               # noqa: BLE001
            skipped["goal_unparseable"] += 1
            details.append({"qid": qid, "seed": seed, "class": "goal_unparseable",
                            "error": f"{type(exc).__name__}: {exc}"})
            continue

        new_status, new_result, new_error = run_deterministic(ctx, goal, order)
        new_outcome, new_reason, _ = score(new_result, gold_rec, status=new_status)

        old_outcome = r["outcome_class"]
        old_digest = digest(r.get("raw_result"))
        new_digest = digest(new_result)

        if new_outcome == old_outcome:
            if new_digest == old_digest:
                classes["clean"] += 1
            else:
                classes["value_drift"] += 1
                details.append({"qid": qid, "seed": seed, "class": "value_drift",
                                "outcome": old_outcome,
                                "old_digest": old_digest, "new_digest": new_digest,
                                "new_error": new_error})
            continue

        # outcome changed — attribute it
        rescore_outcome, rescore_reason, _ = score(
            r.get("raw_result"), gold_rec, status=r["status"])
        if new_digest == old_digest and rescore_outcome == new_outcome:
            cls = "comparator_drift"
        else:
            cls = "engine_flip"
        classes[cls] += 1
        flips[(old_outcome, new_outcome, cls)].append(f"{qid}/s{seed}")
        details.append({"qid": qid, "seed": seed, "class": cls,
                        "old_outcome": old_outcome, "new_outcome": new_outcome,
                        "rescore_outcome": rescore_outcome,
                        "old_digest": old_digest, "new_digest": new_digest,
                        "old_status": r["status"], "new_status": new_status,
                        "old_error": r.get("error"), "new_error": new_error,
                        "new_reason": new_reason, "rescore_reason": rescore_reason})

    return {"file": str(path.name), "domain": domain, "rows": len(rows),
            "skipped": dict(skipped), "classes": dict(classes),
            "flips": {f"{o}->{n} [{c}]": qids for (o, n, c), qids in sorted(flips.items())},
            "details": details}


def print_report(rep: dict) -> None:
    replayed = sum(rep["classes"].values())
    print(f"\n=== {rep['file']}  (domain={rep['domain']}, {rep['rows']} rows, "
          f"{replayed} replayed) ===")
    if rep["skipped"]:
        print("  skipped: " + ", ".join(f"{k}={v}" for k, v in sorted(rep["skipped"].items())))
    for cls in ("clean", "value_drift", "comparator_drift", "engine_flip"):
        n = rep["classes"].get(cls, 0)
        if n or cls == "clean":
            print(f"  {cls:<18s} {n}")
    if rep["flips"]:
        print("  flip matrix:")
        for key, qids in rep["flips"].items():
            shown = ", ".join(qids[:10]) + (" ..." if len(qids) > 10 else "")
            print(f"    {key}: {len(qids)}  [{shown}]")
    else:
        print("  flip matrix: EMPTY (no outcome changes)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*",
                    help="results jsonl files (default: the 7 known history files)")
    ap.add_argument("--domain", default=None,
                    help="domain for files not in the known map "
                         f"({', '.join(DOMAIN_CONFIGS)})")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="write full per-row detail to this path")
    args = ap.parse_args(argv)

    if args.files:
        targets = []
        for f in args.files:
            p = Path(f)
            dom = FILE_DOMAINS.get(p.name) or args.domain
            if dom is None:
                ap.error(f"{p.name} is not a known results file — pass --domain")
            if dom not in DOMAIN_CONFIGS:
                ap.error(f"unknown domain {dom!r}")
            targets.append((p, dom))
    else:
        targets = [(ROOT / "results" / name, dom) for name, dom in FILE_DOMAINS.items()]

    reports = []
    total = Counter()
    for path, dom in targets:
        if not path.exists():
            note = ("risk domain data not included in this release — skipping"
                    if dom == "risk" else "MISSING — skipped")
            print(f"\n=== {path.name}: {note} ===")
            continue
        rep = replay_file(path, dom)
        print_report(rep)
        reports.append(rep)
        total.update(rep["classes"])

    print(f"\n{'='*72}\nTOTAL replayed: {sum(total.values())}   "
          + "   ".join(f"{k}={v}" for k, v in sorted(total.items())))
    unexplained = total.get("engine_flip", 0)
    print(f"engine flips (must all be intended, or the phase stops): {unexplained}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(reports, indent=1, default=str))
        print(f"full detail -> {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
