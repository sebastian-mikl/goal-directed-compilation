#!/usr/bin/env python
"""Measure A* search cost across the full corpus (offline; no LLM, no API).

Replays every RECORDED network goal through plan() with the opt-in `stats`
instrumentation (gdc/search.py) and records, per goal:
  - nodes_expanded : states popped from the frontier and expanded
  - max_frontier   : peak priority-queue size
  - search_ms      : wall-clock of plan() ONLY (excludes the LLM emission and the
                     data-execution step) — min of N repeats to denoise
plus the query's tier, domain, and the register's ROW COUNT (for the
data-independence claim).

Search cost is a function of the GOAL, not the data: plan() consumes only the
start State's column descriptors — the DataFrame never enters it. This script
proves that empirically by comparing matched-complexity goals across the
150-row employee table and the ~10k-row bike_1 table.

Deterministic metrics (nodes_expanded, max_frontier, plan length) are measured
once per UNIQUE (domain, goal-fingerprint); timing repeats R times and takes the
min. Per-emission aggregates expand the cache back over every recorded emission,
so median/max reflect the real distribution of searches the system performed.

    python scripts/measure_search_cost.py            # full corpus, all 5 domains
    python scripts/measure_search_cost.py --repeats 50
"""
from __future__ import annotations

import argparse
import platform
import statistics as stats_mod
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from gdc.search import plan                              # noqa: E402
from gdc.state import State                              # noqa: E402
from scripts.replay_goals import DomainContext, rebuild_goal  # noqa: E402
from scripts import paper_inputs as pi                         # noqa: E402

# The goals measured are the GDC emissions the paper's numbers are built from,
# read as scripts/build_paper_numbers.py reads them (paper_inputs.network_cells:
# same files, temperature-0 overlays under PARITY_TEMP0=1, tier of record). risk:
# T1-T4/hv/algebra_boundary from results_off, T5 (the deepest chains, the worst
# case the paper cites) from results_t5; Spider: the post-fix Phase R runs,
# s40off and s40on. (Was every network row of results_off AND results_t5, which
# counted risk's non-T5 cells twice, and Spider's pre-fix five-system files.)
DOMAINS = [d for d in ("risk", "employee", "wta_1", "world_1", "bike_1") if d in pi.GOLD]   # risk is withheld here
REGISTER = {
    "risk":     ROOT / "environment" / "register.pkl",
    "employee": ROOT / "evaluation/domains/employee" / "employees.pkl",
    "wta_1":    ROOT / "evaluation/domains/spider_wta_1" / "matches.pkl",
    "world_1":  ROOT / "evaluation/domains/spider_world_1" / "country.pkl",
    "bike_1":   ROOT / "evaluation/domains/spider_bike_1" / "trip.pkl",
}


def register_rows(domain: str) -> int:
    return len(pd.read_pickle(REGISTER[domain]))


def measure_goal(ctx: DomainContext, goal, repeats: int) -> dict:
    """Deterministic metrics once; timing = min of `repeats` runs."""
    start = State.from_base(ctx.base_schema)
    best_ms = float("inf")
    st = {}
    for _ in range(repeats):
        s = {}
        res = plan(start, goal, stats=s)
        best_ms = min(best_ms, s["search_ms"])
        st = s
    reachable = st["reachable"]
    return {"nodes_expanded": st["nodes_expanded"], "max_frontier": st["max_frontier"],
            "search_ms": best_ms, "reachable": reachable,
            "plan_len": (len(res) if reachable else None)}


def collect(repeats: int) -> list[dict]:
    per_emission = []
    cache: dict = {}
    n_rows = n_goal = n_unparse = 0
    for domain in DOMAINS:
        ctx = DomainContext.get(domain)
        rrows = register_rows(domain)
        for _fname, r in pi.network_cells(domain):
            n_rows += 1
            art = r.get("artifact") or {}
            if not isinstance(art, dict) or not art.get("goal"):
                continue
            try:
                goal = rebuild_goal(art["goal"])
                fp = goal.fingerprint
            except Exception:                            # noqa: BLE001
                n_unparse += 1
                continue
            n_goal += 1
            key = (domain, fp)
            if key not in cache:
                cache[key] = measure_goal(ctx, goal, repeats)
            m = cache[key]
            per_emission.append({"domain": domain, "tier": r["tier"],
                                 "register_rows": rrows, **m})
    print(f"scanned {n_rows} network rows: {n_goal} with a parseable goal "
          f"({len(cache)} unique goals), {n_unparse} unparseable.\n")
    return per_emission


def _summ(vals):
    return (round(stats_mod.median(vals), 4), max(vals)) if vals else (0, 0)


def report(rows: list[dict]) -> None:
    exp = [r["nodes_expanded"] for r in rows]
    fro = [r["max_frontier"] for r in rows]
    ms = [r["search_ms"] for r in rows]
    print("=" * 84)
    print(f"WHOLE CORPUS  (n={len(rows)} emissions)")
    print("=" * 84)
    me, mxe = _summ(exp); mf, mxf = _summ(fro); mm, mxm = _summ(ms)
    print(f"  nodes_expanded : median {me:>6}   max {mxe}")
    print(f"  max_frontier   : median {mf:>6}   max {mxf}")
    print(f"  search_ms      : median {mm:>8.4f} ms   max {mxm:.4f} ms")

    print("\n" + "-" * 84 + "\n  BY TIER (nodes_expanded median/max | search_ms median/max)")
    by = defaultdict(list)
    for r in rows:
        by[r["tier"]].append(r)
    for tier in sorted(by):
        sub = by[tier]
        e = [r["nodes_expanded"] for r in sub]; t = [r["search_ms"] for r in sub]
        print(f"    {tier:<26} n={len(sub):<4} nodes {stats_mod.median(e):>5.0f}/{max(e):<4} "
              f"ms {stats_mod.median(t):.4f}/{max(t):.4f}")

    # T5 worst case isolated
    t5 = [r for r in rows if r["tier"] == "T5_high_complexity"]
    if t5:
        e = [r["nodes_expanded"] for r in t5]; t = [r["search_ms"] for r in t5]
        f = [r["max_frontier"] for r in t5]
        print("\n" + "-" * 84 + f"\n  DEEPEST CHAINS — T5_high_complexity ONLY (n={len(t5)}, the worst case)")
        print(f"    nodes_expanded : median {stats_mod.median(e):.0f}   max {max(e)}")
        print(f"    max_frontier   : median {stats_mod.median(f):.0f}   max {max(f)}")
        print(f"    search_ms      : median {stats_mod.median(t):.4f} ms   max {max(t):.4f} ms")

    print("\n" + "-" * 84 + "\n  BY DOMAIN (register rows | nodes median/max | search_ms median/max)")
    byd = defaultdict(list)
    for r in rows:
        byd[r["domain"]].append(r)
    for d in ["risk", "employee", "wta_1", "world_1", "bike_1"]:
        sub = byd.get(d, [])
        if not sub:
            continue
        e = [r["nodes_expanded"] for r in sub]; t = [r["search_ms"] for r in sub]
        print(f"    {d:<9} rows={sub[0]['register_rows']:>5}  n={len(sub):<4} "
              f"nodes {stats_mod.median(e):>5.0f}/{max(e):<4} ms {stats_mod.median(t):.4f}/{max(t):.4f}")

    # ── DATA-INDEPENDENCE: matched complexity across table sizes ──
    print("\n" + "=" * 84)
    print("  DATA-INDEPENDENCE CHECK — nodes_expanded by PLAN LENGTH x DOMAIN")
    print("  (same plan length => same nodes regardless of register size => search is")
    print("   data-independent; bike_1 is ~10k rows vs employee ~150)")
    print("=" * 84)
    grp = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["plan_len"] is not None:
            grp[r["plan_len"]][r["domain"]].append(r["nodes_expanded"])
    print(f"    {'plan_len':<9}" + "".join(f"{d[:8]:>12}" for d in ["risk","employee","wta_1","world_1","bike_1"]))
    for pl in sorted(grp):
        cells = []
        for d in ["risk","employee","wta_1","world_1","bike_1"]:
            v = grp[pl].get(d)
            cells.append(f"{int(stats_mod.median(v))}" if v else "-")
        print(f"    {pl:<9}" + "".join(f"{c:>12}" for c in cells))
    print("    (each cell = median nodes_expanded for that plan length in that domain;")
    print("     equal across columns = data size does not affect search)")

    # hardware
    print("\n" + "-" * 84)
    print("  ENVIRONMENT (timing is hardware-dependent — cite this in the paper):")
    print(f"    platform : {platform.platform()}")
    print(f"    processor: {platform.processor() or '(unspecified)'}")
    print(f"    cpu_count: {__import__('os').cpu_count()}")
    print(f"    python   : {platform.python_version()}")
    print("    note     : run in the Claude Code remote/CI container, not a pinned")
    print("               benchmark host — treat ms as order-of-magnitude, node")
    print("               counts as exact/hardware-independent.")
    print("-" * 84)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repeats", type=int, default=25, help="timing repeats per unique goal (min taken)")
    args = ap.parse_args(argv)
    rows = collect(args.repeats)
    if not rows:
        print("no goals collected — are the results files present?")
        return 1
    report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
