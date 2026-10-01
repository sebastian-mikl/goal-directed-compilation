#!/usr/bin/env python
"""Extract + auto-triage every UNCAUGHT silent error for GDC/network (offline).

A silent error is a goal that passed ALL static checks and executed to a wrong
answer. We take the locked pipeline (current engine, s47) via the same replay
used everywhere: for each recorded network emission we rebuild the goal, run the
deterministic pipeline, score, and keep the cases scored `silent_wrong`.
Deduplicated by (domain, qid) — temperature=0 makes reps identical (verified).

For each unique silent error we emit, for hand-classification into
  A grounding (schema-valid but wrong intent — uncatchable),
  B schema-violation (a static schema-aware check could flag it), or
  C other/bug,
the fields needed to decide, PLUS an automatic schema-compliance triage:
  - every where-predicate mapped alias->real column, checked against the real
    column's value domain (for ==/!=/in on non-numeric cols) and dtype (for
    </<=/>/>= — numeric column required),
  - every aggregate derive checked for numeric operand.
The auto-flag SURFACES category-B candidates; final A/B/C is decided by hand.

Pure local: no API. Usage: python scripts/uncaught_silent_extract.py [--json out]
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import pandas as pd                                            # noqa: E402
import ablate_checks as A                                     # noqa: E402
import replay_goals as rg                                     # noqa: E402
from evidence.eval_run.comparator import score                # noqa: E402

NUMERIC_OPS = {"<", "<=", ">", ">="}
EQ_OPS = {"==", "!=", "in"}


def domain_schema(domain):
    """Real-column value domains + numeric flags from the register."""
    cfg = rg.DOMAIN_CONFIGS[domain]
    reg = pd.read_pickle(cfg["register"])
    val_domain, is_num = {}, {}
    for col in reg.columns:
        s = reg[col]
        is_num[col] = bool(pd.api.types.is_numeric_dtype(s))
        # value domain as strings (executor compares s.astype(str) == str(val))
        try:
            val_domain[col] = set(s.dropna().astype(str).unique())
        except Exception:                                      # noqa: BLE001
            val_domain[col] = set()
    return reg, val_domain, is_num


def triage_goal(ctx, goal_dict, val_domain, is_num):
    """Return list of schema-compliance flags (empty => schema-valid)."""
    flags = []
    to_real = ctx.amap_obj.to_real
    aliases = ctx.amap_obj.aliases
    def real(c):
        return to_real(c) if c in aliases else c
    # where predicates: [col, op, val]
    for trip in goal_dict.get("where", []):
        try:
            col, op, val = trip
        except Exception:                                      # noqa: BLE001
            continue
        rc = real(str(col))
        if rc not in val_domain:
            flags.append(f"where col {col!r}->{rc!r} NOT IN SCHEMA")
            continue
        if op in NUMERIC_OPS and not is_num[rc]:
            flags.append(f"numeric op {op} on non-numeric col {rc!r}")
        if op in EQ_OPS and not is_num[rc]:
            vals = val if isinstance(val, (list, tuple)) else [val]
            for v in vals:
                if str(v) not in val_domain[rc]:
                    flags.append(f"value {v!r} NOT IN domain of {rc!r} "
                                 f"(matches 0 rows)")
    # derive aggregates over text columns
    for name, expr in goal_dict.get("derive", []):
        e = str(expr)
        for fn in ("avg", "sum", "mean"):
            if e.startswith(fn + "(") and e.endswith(")"):
                inner = e[len(fn) + 1:-1].strip()
                rc = real(inner)
                if rc in is_num and not is_num[rc]:
                    flags.append(f"{fn}() over non-numeric col {rc!r}")
    return flags


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)

    # question text per (domain,qid) from the raw files
    qtext = {}
    # collect silent errors via baseline replay, keep per-rep to verify dedup
    per_qid = defaultdict(list)   # (domain,qid) -> list of rep dicts
    sch = {}

    # the ablation's emissions, read as the builder reads them (overlays, tier of record)
    for fname, domain, ctx, r in A.network_rows():
        if domain not in sch:
            sch[domain] = domain_schema(domain)
        _reg, val_domain, is_num = sch[domain]
        tier = r.get("tier")
        art = r.get("artifact") or {}
        if not isinstance(art, dict) or not art.get("goal"):
            continue
        qid, seed = r["qid"], r.get("seed")
        gold_rec = ctx.gold.get(qid)
        if gold_rec is None:
            continue
        try:
            goal = rg.rebuild_goal(art["goal"]); order = rg.rebuild_order(art.get("order") or {})
        except Exception:                                  # noqa: BLE001
            continue
        status, result, reason = A.run_det(ctx, goal, order, "baseline")
        outcome, creason, _ = score(result, gold_rec, status=status)
        if outcome != "silent_wrong":
            continue
        qtext[(domain, qid)] = r.get("query")
        per_qid[(domain, qid)].append({
            "seed": seed, "tier": tier, "goal": art["goal"],
            "produced": rg.logger_view(result), "gold": gold_rec.get("value", gold_rec),
            "comparator_reason": creason,
            "schema_flags": triage_goal(ctx, art["goal"], val_domain, is_num)})

    # dedupe: one entry per (domain,qid); note if reps disagree on schema_flags
    cases = []
    for (domain, qid), reps in sorted(per_qid.items()):
        goals = {json.dumps(x["goal"], sort_keys=True) for x in reps}
        rep0 = reps[0]
        cases.append({
            "domain": domain, "qid": qid, "tier": rep0["tier"],
            "question": qtext[(domain, qid)],
            "n_reps_silent": len(reps), "reps_goal_identical": len(goals) == 1,
            "goal": rep0["goal"], "produced": rep0["produced"], "gold": rep0["gold"],
            "comparator_reason": rep0["comparator_reason"],
            "schema_flags": rep0["schema_flags"]})

    # summary
    n = len(cases)
    with_flags = [c for c in cases if c["schema_flags"]]
    by_dom = defaultdict(int)
    for c in cases:
        by_dom[c["domain"]] += 1
    print(f"UNCAUGHT silent errors (unique domain,qid): {n}")
    print("  by domain:", dict(by_dom))
    print(f"  auto schema-compliance flags (category-B candidates): {len(with_flags)}")
    print(f"  non-identical reps: {sum(1 for c in cases if not c['reps_goal_identical'])}")
    print("\n=== per-case (question | tier | flags) ===")
    for c in cases:
        f = "; ".join(c["schema_flags"]) if c["schema_flags"] else "(schema-valid)"
        print(f"  [{c['domain']}/{c['qid']} {c['tier']}] {str(c['question'])[:70]}")
        print(f"      goal.where={c['goal'].get('where')}  derive={c['goal'].get('derive')}")
        print(f"      -> {f}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(cases, indent=1, default=str))
        print(f"\nwrote {args.json_out}  ({n} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
