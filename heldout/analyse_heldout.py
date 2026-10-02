#!/usr/bin/env python
"""The pre-registered analysis of the held-out run (heldout/PREREGISTRATION.md, section 7).

Offline, no API. Reads heldout/results/heldout_<db>.jsonl, re-scores every cell with
the frozen comparator (strict, evidence/eval_run/comparator.score) and the tie-tolerant
rule (strict, else a match against a data-verified tied row in
heldout/tie_alternatives_heldout.json), and computes, per set and system, the
silent-error rate and accuracy (decline rate on out_of_scope_register) with a
question-cluster bootstrap 95% CI, and, for GDC minus each alternative, the paired
difference with its cluster-bootstrap CI, an exact sign test, an exact sign-flip
permutation test and Holm's adjustment over the four alternatives. The statistical
functions are those of the paper's question-clustered statistics
(question_clustered_stats.py: 10,000 resamples, stratified by database, percentile
interval; "resolved" = CI excludes 0 and exact permutation p < 0.05).

Refuses to analyse an incomplete run: every planned cell must be present exactly once.

    python heldout/analyse_heldout.py [--results DIR]
Outputs: <DIR>/analysis/{rates.csv, comparisons.csv, per_question.csv, gdc_out_of_contract.csv, summary.txt}
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from evidence.eval_run.comparator import COMPARATOR_VERSION, score  # noqa: E402
from run_heldout import DOMAINS, OOCA, OOSR, build_plan, tier_class  # noqa: E402

N_BOOT = 10_000
BASE_SEED = 20261001
GDC = "network"
ALTS = ("planner", "react", "code_agent", "text_to_sql")
SYSTEMS = (GDC,) + ALTS
TIES = json.loads((HERE / "tie_alternatives_heldout.json").read_text())


def score_tt(produced, gold_record, qid, status):
    v, reason, _ = score(produced, gold_record, status=status)
    if v == "correct":
        return v
    for alt in (TIES.get(qid) or {}).get("alt_values", []):
        if score(produced, {**gold_record, "value": alt}, status=status)[0] == "correct":
            return "correct"
    return v


def load_cells(results: Path) -> list[dict]:
    plan = {(r["domain"], r["system"], r["qid"], r["seed"]): r for r in build_plan()}
    seen, cells = Counter(), []
    for dom, cfg in DOMAINS.items():
        gold = json.loads(Path(cfg["gold"]).read_text())
        p = results / f"heldout_{dom}.jsonl"
        for line in (p.read_text().splitlines() if p.exists() else []):
            if not line.strip():
                continue
            r = json.loads(line)
            key = (dom, r["system"], r["qid"], int(r["seed"]))
            seen[key] += 1
            g = gold[r["qid"]]
            strict = score(r["raw_result"], g, status=r["status"])[0]
            cells.append({"dom": dom, "system": r["system"], "qid": r["qid"], "seed": int(r["seed"]),
                          "tier": tier_class(g["tier"]), "strict": strict,
                          "tolerant": score_tt(r["raw_result"], g, r["qid"], r["status"]),
                          "recorded": r.get("outcome_class"), "cost": r.get("cost_usd") or 0.0,
                          "calls": r.get("llm_calls") or 0})
    missing = sorted(set(plan) - set(seen))
    extra = sorted(set(seen) - set(plan))
    dup = sorted(k for k, n in seen.items() if n > 1)
    if missing or extra or dup:
        raise SystemExit(f"INCOMPLETE OR INCONSISTENT RUN: {len(missing)} missing, {len(extra)} unplanned, "
                         f"{len(dup)} duplicated cells. Per the pre-registration, an incomplete run is not "
                         f"reported (O1 is dropped).")
    return cells


# ── the statistics (as in question_clustered_stats.py) ──────────────────────

def boot_index(strata, rng):
    n = len(strata)
    idx = np.empty((N_BOOT, n), dtype=np.int64)
    for h in sorted(set(strata)):
        pos = np.where(strata == h)[0]
        idx[:, pos] = pos[rng.integers(0, len(pos), size=(N_BOOT, len(pos)))]
    return idx


def sign_test(d):
    npos, nneg = int((d > 0).sum()), int((d < 0).sum())
    N = npos + nneg
    if N == 0:
        return npos, nneg, 1.0
    tail = sum(math.comb(N, i) for i in range(min(npos, nneg) + 1)) / 2 ** N
    return npos, nneg, min(1.0, 2 * tail)


def permutation_p(dint):
    a = [int(abs(x)) for x in dint if x != 0]
    T = abs(int(dint.sum()))
    if not a:
        return 1.0
    off = sum(a)
    dist = np.zeros(2 * off + 1)
    dist[off] = 1.0
    for x in a:
        nd = np.zeros_like(dist)
        nd[x:] += 0.5 * dist[:len(dist) - x]
        nd[:len(dist) - x] += 0.5 * dist[x:]
        dist = nd
    vals = np.arange(-off, off + 1)
    return float(min(1.0, dist[np.abs(vals) >= T].sum()))


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - r) * ps[i]))
        adj[i] = run
    return adj


def analyse(name, cells, rule, metrics):
    """metrics: [(metric name, outcome class counted)]."""
    pq = defaultdict(lambda: defaultdict(list))
    for c in cells:
        pq[c["system"]][(c["dom"], c["qid"])].append(c[rule])
    keys = sorted(pq[GDC])
    for s in SYSTEMS:
        if sorted(pq[s]) != keys:
            raise SystemExit(f"{name}: question sets differ between {GDC} and {s}")
    m = np.array([len(pq[GDC][k]) for k in keys])
    strata = np.array([k[0] for k in keys])
    rng = np.random.default_rng(BASE_SEED + zlib.crc32(name.encode()))
    idx = boot_index(strata, rng)
    M, L = float(m.sum()), math.lcm(*[int(x) for x in set(m)])
    Mb = m[idx].sum(1)
    rates, comps = [], []
    for metric, cls in metrics:
        K = {s: np.array([pq[s][k].count(cls) for k in keys]) for s in SYSTEMS}
        for s in SYSTEMS:
            k = K[s]
            bt = k[idx].sum(1) / Mb
            rates.append({"set": name, "rule": rule, "metric": metric, "system": s, "k": int(k.sum()),
                          "n_cells": int(M), "n_questions": len(keys), "rate_pct": round(100 * k.sum() / M, 2),
                          "ci_lo_pct": round(100 * np.percentile(bt, 2.5), 2),
                          "ci_hi_pct": round(100 * np.percentile(bt, 97.5), 2),
                          "questions_with_any": int((k > 0).sum())})
        ps, block = [], []
        for a in ALTS:
            dk = K[GDC] - K[a]
            bt = dk[idx].sum(1) / Mb
            lo, hi = np.percentile(bt, 2.5), np.percentile(bt, 97.5)
            drate = dk / m
            npos, nneg, psign = sign_test(drate)
            pperm = permutation_p((dk * (L // m)).astype(np.int64))
            block.append({"set": name, "rule": rule, "metric": metric, "alt": a,
                          "gdc_pct": round(100 * K[GDC].sum() / M, 2), "alt_pct": round(100 * K[a].sum() / M, 2),
                          "diff_pp": round(100 * dk.sum() / M, 2), "ci_lo_pp": round(100 * lo, 2),
                          "ci_hi_pp": round(100 * hi, 2), "excludes_0": bool(lo > 1e-12 or hi < -1e-12),
                          "n_questions": len(keys), "n_discordant_q": npos + nneg, "q_gdc_higher": npos,
                          "q_alt_higher": nneg, "p_sign": psign, "p_perm": pperm})
            ps.append(pperm)
        for c, ph in zip(block, holm(ps)):
            c["p_perm_holm"] = ph
            c["verdict"] = ("resolved" if c["excludes_0"] and c["p_perm"] < 0.05 else
                            "bootstrap-only" if c["excludes_0"] else
                            "exact-test-only" if c["p_perm"] < 0.05 else "unresolved")
            c["resolved_after_holm"] = bool(c["excludes_0"] and ph < 0.05)
        comps += block
    return rates, comps


def write_csv(path, rows):
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(HERE / "results"))
    a = ap.parse_args(argv)
    res = Path(a.results)
    cells = load_cells(res)
    moved = sum(c["strict"] != c["recorded"] for c in cells)
    ans = [c for c in cells if c["tier"] == "answer"]
    oosr = [c for c in cells if c["tier"] == OOSR]
    rates, comps = [], []
    for rule in ("tolerant", "strict"):                  # tolerant first: the paper's primary rule
        r, c = analyse(f"heldout_answer_{rule}", ans, rule,
                       [("silent_error", "silent_wrong"), ("accuracy", "correct"),
                        ("refused_should_answer", "refused_should_answer")])
        rates += r
        comps += c
    # PRIMARY (pre-registration section 7): answer-anyway rate = silent_wrong share on
    # out_of_scope_register; the decline rate is reported beside it.
    r, c = analyse("heldout_out_of_scope_register", oosr, "strict",
                   [("answer_anyway", "silent_wrong"), ("declined", "correct")])
    rates += r
    comps += c
    for dom in DOMAINS:                                  # per database, descriptive
        sub = [x for x in ans if x["dom"] == dom]
        r, _ = analyse(f"heldout_answer_tolerant_{dom}", sub, "tolerant", [("silent_error", "silent_wrong")])
        rates += r
    out = res / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "rates.csv", rates)
    write_csv(out / "comparisons.csv", comps)
    perq = defaultdict(lambda: Counter())
    for x in cells:
        perq[(x["dom"], x["qid"], x["tier"], x["system"])][x["tolerant"]] += 1
    classes = ("correct", "silent_wrong", "refused_should_answer", "crash", "timeout")
    write_csv(out / "per_question.csv", [{"domain": k[0], "qid": k[1], "tier": k[2], "system": k[3],
                                          **{c: v.get(c, 0) for c in classes}}
                                         for k, v in sorted(perq.items())])
    ooca = Counter(x["strict"] for x in cells if x["tier"] == OOCA)
    write_csv(out / "gdc_out_of_contract.csv", [{"system": GDC, **{c: ooca.get(c, 0) for c in classes}}])
    cost = defaultdict(float)
    for x in cells:
        cost[x["system"]] += x["cost"]
    L = [f"Held-out analysis (comparator {COMPARATOR_VERSION}, tie-tolerant {COMPARATOR_VERSION}t; "
         f"ties {sorted(TIES) or 'none'}); {len(cells)} cells; {moved} cell(s) whose re-score differs "
         f"from the run-time verdict.", ""]
    for x in rates:
        L.append(f"  {x['set']:<45} {x['metric']:<22} {x['system']:<12} {x['rate_pct']:6.1f}%  "
                 f"[{x['ci_lo_pct']:5.1f}, {x['ci_hi_pct']:5.1f}]  {x['k']}/{x['n_cells']} cells, "
                 f"{x['n_questions']} questions")
    L.append("")
    for x in comps:
        L.append(f"  {x['set']:<45} {x['metric']:<22} GDC-{x['alt']:<12} {x['diff_pp']:+6.1f} pp "
                 f"[{x['ci_lo_pp']:+6.1f}, {x['ci_hi_pp']:+6.1f}]  discordant {x['n_discordant_q']} "
                 f"({x['q_gdc_higher']}/{x['q_alt_higher']})  p_perm {x['p_perm']:.3g} (Holm {x['p_perm_holm']:.3g})"
                 f"  {x['verdict']}")
    L.append("")
    L.append(f"  GDC on out_of_contract_aggregate (unscored): {dict(ooca)}")
    L.append("  billed $ by system: " + ", ".join(f"{s} {cost[s]:.2f}" for s in SYSTEMS))
    (out / "summary.txt").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
