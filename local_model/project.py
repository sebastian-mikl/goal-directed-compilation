#!/usr/bin/env python
"""O2.b projection of the full O2 run time from the smoke-test throughput.

Scope (fixed, not narrowed): public domains only, ONE seed, all five systems --
employee: every qid of the reported cells (91); Spider wta_1 / world_1 / bike_1: every
reported qid across the answer, out_of_contract_aggregate and out_of_scope_register
tiers (30 / 30 / 25). 176 qids x 5 systems = 880 cells.

Three projections:
  A  smoke mean wall time per cell, per system, x the number of cells (the checklist's
     "from the smoke-test throughput");
  B  A, with react and code_agent scaled per domain by the recorded (Sonnet) mean calls
     per cell in that domain relative to the recorded mean on the five smoke questions,
     and the local model's mean wall time per call;
  C  worst case: every react cell runs its 15-iteration bound and every code_agent cell
     its 13 calls, at the slowest per-call latency the smoke test measured for that
     system; network / planner / text_to_sql at their slowest smoke cell.
"""
from __future__ import annotations

import csv
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent
TREE = OUT / "tree"
SMOKE_Q = ("t1_01", "t2_01", "t3_01", "t4_01", "t5_01")
SYSTEMS = ("network", "planner", "react", "code_agent", "text_to_sql")
SPIDER_TIERS_ANSWER_EXCLUDED = {"out_of_contract_aggregate", "out_of_scope_register"}


def load(p):
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]


def scope() -> dict[str, list[str]]:
    rows = [r for r in csv.DictReader(open(TREE / "docs/audit/work/manifest/cell_manifest.csv"))
            if r["domain"] in ("employee", "wta_1", "world_1", "bike_1")]
    q = defaultdict(set)
    for r in rows:
        q[r["domain"]].add(r["qid"])
    corp = {"employee": "eval_corpus_employee.json", "wta_1": "eval_corpus_spider_wta_1.json",
            "world_1": "eval_corpus_spider_world_1.json", "bike_1": "eval_corpus_spider_bike_1.json"}
    out = {}
    for d, fn in corp.items():
        order = [x["id"] for x in json.loads((TREE / fn).read_text())["queries"]]
        missing = q[d] - set(order)
        assert not missing, (d, missing)
        out[d] = [x for x in order if x in q[d]]
    return out


def recorded_calls():
    rows = [r for r in csv.DictReader(open(TREE / "docs/audit/work/manifest/cell_manifest.csv"))
            if r["domain"] in ("employee", "wta_1", "world_1", "bike_1")]
    seen = {}
    for r in rows:
        seen.setdefault((r["domain"], r["system"], r["qid"], r["seed"]), r)
    by, smoke = defaultdict(list), defaultdict(list)
    for (d, s, q, _), r in seen.items():
        try:
            n = float(r["llm_calls"])
        except ValueError:
            continue
        by[(d, s)].append(n)
        if d == "employee" and q in SMOKE_Q:
            smoke[s].append(n)
    return {k: st.mean(v) for k, v in by.items()}, {k: st.mean(v) for k, v in smoke.items()}


def main(tag="smoke"):
    d = OUT / tag
    rows = load(d / "results_o2_smoke_employee.jsonl")
    calls = load(d / "calls.jsonl")
    sc = scope()
    n_q = {k: len(v) for k, v in sc.items()}
    total_q = sum(n_q.values())
    wall = {s: [r["wall_time_s"] for r in rows if r["system"] == s] for s in SYSTEMS}
    ncall = {s: [r["llm_calls"] for r in rows if r["system"] == s] for s in SYSTEMS}
    lat = {s: [c["latency_s"] for c in calls if c["system"] == s] for s in SYSTEMS}
    per_call = {s: sum(wall[s]) / max(1, sum(ncall[s])) for s in SYSTEMS}
    rec, rec_smoke = recorded_calls()

    print(f"scope: {n_q} = {total_q} qids x 5 systems = {5 * total_q} cells, one seed")
    A = sum(st.mean(wall[s]) * total_q for s in SYSTEMS)
    print("\nA  smoke mean wall/cell x cells")
    for s in SYSTEMS:
        print(f"   {s:<12} {st.mean(wall[s]):6.2f} s/cell x {total_q} = {st.mean(wall[s]) * total_q / 3600:5.2f} h")
    print(f"   TOTAL {A / 3600:.2f} h")

    B = 0.0
    print("\nB  react/code_agent scaled by recorded calls per cell (domain / smoke questions)")
    for dom, n in n_q.items():
        sub = 0.0
        for s in SYSTEMS:
            if s in ("react", "code_agent"):
                ratio = rec[(dom, s)] / rec_smoke[s]
                local_calls = st.mean(ncall[s]) * ratio
                t = local_calls * per_call[s]
            else:
                ratio, t = 1.0, st.mean(wall[s])
            sub += t * n
            if s in ("react", "code_agent"):
                print(f"   {dom:<9} {s:<11} recorded calls {rec[(dom, s)]:.2f} vs smoke-q {rec_smoke[s]:.2f} "
                      f"-> x{ratio:.2f}: {t:6.1f} s/cell")
        B += sub
        print(f"   {dom:<9} subtotal {sub / 3600:5.2f} h ({n} qids)")
    print(f"   TOTAL {B / 3600:.2f} h")

    bound = {"react": 15, "code_agent": 13}
    C = 0.0
    print("\nC  worst case (react 15 calls, code_agent 13 calls per cell at the slowest smoke call)")
    for s in SYSTEMS:
        t = bound[s] * max(lat[s]) if s in bound else max(wall[s])
        C += t * total_q
        print(f"   {s:<12} {t:6.1f} s/cell -> {t * total_q / 3600:5.2f} h")
    print(f"   TOTAL {C / 3600:.2f} h")
    json.dump({"scope": sc, "cells": 5 * total_q, "A_hours": A / 3600, "B_hours": B / 3600,
               "C_hours": C / 3600}, open(d / "projection.json", "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])
