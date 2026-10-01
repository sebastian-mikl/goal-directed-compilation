#!/usr/bin/env python
"""S17 portability proof (LIVE) — the SAME engine on two domains; only DB + schema.json
differ. Read-only; no engine code is touched or branched per domain.

  V1 (risk)     : the FULL S14 8-query risk set (apples-to-apples vs S14's 8/8 under
                  the prior prompt) — so any regression caused by the generic prompt
                  is directly visible.
  V2 (employee) : 5 employee questions LIVE incl. the business-rule query
                  ("current employees ..." → Terminated EXCLUDED).
  V4            : both runs call the identical solve()/run_goal_plan() — the only
                  difference is the (db, schema.json) pair.

Captures per-query emitted goal + order + reason → results/portability_live/{risk,
employee}_emissions.json (so failures are diagnosable without another re-run).

Needs ANTHROPIC_API_KEY (LLM1). Run:  python scripts/portability_live.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "queries" / "oracle"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

import pandas as pd  # noqa: E402

from gdc.integrate import aliased_view, execute_plan_ordered  # noqa: E402
from gdc.schema_bridge import build_bridge, build_briefing  # noqa: E402
from gdc.llm import llm1  # noqa: E402
from gdc.search import plan as plan_search, Unreachable  # noqa: E402
from gdc.state import State  # noqa: E402
from gdc.contract import ContractError, ParseError, validate_goal  # noqa: E402
from gdc.order import validate_order  # noqa: E402
from gdc.probe import goal_to_dict  # noqa: E402
from evidence.correctness import values_equal  # noqa: E402

OUT = ROOT / "results" / "portability_live"


def _order_to_dict(o):
    return {"order_by": [{"col": c, "dir": d} for c, d in o.order_by], "limit": o.limit}


def _emp_oracles(reg):
    return {
        "employees in the Engineering department":
            reg[reg.Department == "Engineering"][["Employee ID", "Name", "Annual Salary"]],
        "for each employee, salary minus bonus target":
            reg.assign(gap=reg["Annual Salary"] - reg["Bonus Target"])[["Employee ID", "gap"]],
        "average annual salary per department":
            reg.groupby("Department")["Annual Salary"].mean().reset_index(),
        "the 5 highest-paid employees":
            reg.nlargest(5, "Annual Salary")[["Employee ID", "Annual Salary"]],
        "average annual salary per department for current employees":
            reg[reg.Status != "Terminated"].groupby("Department")["Annual Salary"].mean().reset_index(),
    }


def _risk_oracles(reg):
    """The full join-free S14 set: q01,q02,q03,q04,q08,q11,q13,q14 → (NL, oracle)."""
    import yaml
    from oracles import ORACLES
    corpus = {q["id"]: q for q in yaml.safe_load(
        (ROOT / "queries" / "complex.yaml").read_text())["queries"]}
    return {corpus[q]["query"]: ORACLES[q](reg)
            for q in ("q01", "q02", "q03", "q04", "q08", "q11", "q13", "q14")}


def run_domain(name, db_path, schema_path, oracle_map, client):
    reg = pd.read_pickle(db_path)
    _, adf, schema = aliased_view(reg)                              # dict-amap + aliased frame
    amap, schema_view, rules = build_bridge(list(reg.columns), schema_path=schema_path)
    domain = build_briefing(schema_path, amap)                     # ADVISORY context (S18)
    print(f"\n===== {name}: {len(reg)} rows; "
          f"rules={ {k: v['allowed'] for k, v in rules.items()} }; "
          f"briefing={'yes' if domain else 'none'} =====")

    n_ok = 0
    emissions = {}
    for nl, oracle in oracle_map.items():
        rec = {"correct": 0, "failure_mode": "", "reason": "",
               "emitted_goal": None, "emitted_order": None}
        try:
            goal, order = llm1(nl, client=client, base_schema=schema,
                               schema_view=schema_view, business_rules=rules, domain=domain)
        except ParseError as exc:
            rec.update(failure_mode="ParseError", reason=str(exc)[:200])
            emissions[nl] = rec
            print(f"  [ParseError] {nl!r}: {exc}")
            continue
        rec["emitted_goal"] = goal_to_dict(goal)
        rec["emitted_order"] = _order_to_dict(order)

        gv = validate_goal(goal); ov = validate_order(order, goal)
        if not gv.ok:
            rec.update(failure_mode="ContractError(goal)", reason=gv.reason)
            print(f"  [ContractError(goal)] {nl!r}: {gv.reason}")
            emissions[nl] = rec; continue
        if not ov.ok:
            rec.update(failure_mode="ContractError(order)", reason=ov.reason)
            print(f"  [ContractError(order)] {nl!r}: {ov.reason}")
            emissions[nl] = rec; continue
        p = plan_search(State.from_base(schema), goal)
        if isinstance(p, Unreachable):
            rec.update(failure_mode="Unreachable", reason=p.reason)
            print(f"  [Unreachable] {nl!r}: {p.reason}")
            emissions[nl] = rec; continue
        frame = execute_plan_ordered(p, adf, order)
        frame = frame.rename(columns={c: amap.to_real(c) for c in frame.columns if c in amap.aliases})
        ok, detail = values_equal(frame, oracle, tolerate_extra_cols=True)
        rec["correct"] = int(bool(ok))
        rec["reason"] = "ok" if ok else detail[:200]
        if not ok:
            rec["failure_mode"] = "wrong_values"
        emissions[nl] = rec
        n_ok += int(ok)
        print(f"  {'✓' if ok else '✗'} {nl!r}  ({len(frame)} rows)  "
              f"{'' if ok else '['+detail[:60]+']'}")
    print(f"  -> {n_ok}/{len(oracle_map)} value-correct")
    return n_ok, len(oracle_map), emissions


def main(seeds: int = 1):
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("portability_live needs ANTHROPIC_API_KEY (LLM1).")
    from anthropic import Anthropic
    from statistics import mean
    client = Anthropic()
    OUT.mkdir(parents=True, exist_ok=True)

    domains = [
        ("V1 RISK (full S14 join-free 8-query set)", "risk",
         ROOT / "environment" / "register.pkl", ROOT / "environment" / "schema.json", _risk_oracles),
        ("V2 EMPLOYEE", "employee",
         ROOT / "evaluation/domains/employee" / "employees.pkl", ROOT / "evaluation/domains/employee" / "schema.json", _emp_oracles),
    ]
    summary = {}
    for title, key, db, sch, oracles_fn in domains:
        reg = pd.read_pickle(db)
        per_seed_ok, per_seed_em = [], {}
        for s in range(seeds):
            n_ok, n, em = run_domain(f"{title} [seed {s}]", db, sch, oracles_fn(reg), client)
            per_seed_ok.append(n_ok)
            per_seed_em[f"seed{s}"] = em
        (OUT / f"{key}_emissions.json").write_text(json.dumps(per_seed_em, indent=2, default=str))
        summary[key] = {"per_seed": per_seed_ok, "n": n,
                        "mean": round(mean(per_seed_ok), 2) if per_seed_ok else 0}

    print("\n===== VERDICT (seeds=%d) =====" % seeds)
    for key, label in (("risk", "V1 risk    "), ("employee", "V2 employee")):
        s = summary[key]
        print(f"  {label}: per-seed {s['per_seed']} /{s['n']}  mean {s['mean']}/{s['n']}")
    print("  V4: both ran on the SAME engine (solve / run_goal_plan) — only DB + schema.json differed.")
    print(f"\nper-seed emissions -> {OUT}/{{risk,employee}}_emissions.json")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="S17/S18/S19 portability proof (LIVE, two-domain)")
    ap.add_argument("--seeds", type=int, default=1)
    main(ap.parse_args().seeds)
