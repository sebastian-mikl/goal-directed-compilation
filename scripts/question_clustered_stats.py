#!/usr/bin/env python
"""Question-clustered uncertainty for the GDC paper's employee and Spider comparisons (Section 5.6).

Seeds at temperature 0 are near-copies, so the unit of analysis is the QUESTION, not the
cell. This script:

  1. runs scripts/build_paper_numbers.py with PARITY_TEMP0=1 in-process (runpy), so the
     builder's own row sets, selection helpers and scorers are in hand (it rewrites
     reports/paper_numbers.csv with the bytes run_all.sh step 4 already wrote);
  2. re-derives the per-cell verdict of every cell behind the groups analysed here with the
     builder's own functions (scored_input / outcome / score / score_tt / score_tt_syn /
     oos_class / syn_rows), and REFUSES to continue (non-zero exit) unless every
     outcome-class count and denominator equals the builder's emitted row and the CSV it
     wrote;
  3. computes, per set and system, the silent-error and accuracy (declined, on the decline
     tiers) rates with a question-cluster bootstrap CI, the design-effect effective n and
     seed agreement; and, for GDC (`network`) minus each alternative, the paired difference
     with a 95% percentile CI from a question-level cluster bootstrap (10,000 resamples,
     fixed seed; stratified by domain in pooled sets), an exact two-sided sign test and an
     exact two-sided sign-flip permutation test on per-question rate differences,
     Holm-adjusted over the four alternatives, and, for contrast, the naive Newcombe
     interval that treats cells as independent.

Verdict per comparison: "resolved" = the bootstrap CI excludes 0 AND the exact permutation
p < 0.05; "bootstrap-only" / "exact-test-only" when they disagree. The percentile bootstrap
is anti-conservative when few questions are discordant: four discordant questions out of
27, all one way, exclude 0 from the bootstrap CI, but no exact test on four questions can go
below p = 0.125.

Sets (employee and Spider only): employee in-contract strict and tolerant (Table2), employee
T3 strict and tolerant and T4 (Table1), Spider answer tier strict and tolerant
(Fig1/Fig2/Table3), the Table5 decline tiers (Spider out_of_scope_register per database and
pooled; employee out_of_range) and the pooled decline set. The paper's risk sets and its
pooled in-scope sets include the withheld risk domain and are not regenerated here.

Beside them, set decline_spider_oosr_pooled_s40on: GDC told it may decline, i.e. its Spider
out_of_scope_register cells with the table-coverage instruction on
(results/results_spider_<db>_postfix_s40on.jsonl, the cells of the builder's Fig3/Fig4
out_of_scope_register_s40on rows), classed by the same oos_class(). They take the place of
GDC's cells in decline_spider_oosr_pooled and are analysed under that set's name, so their
intervals use the resamples of every interval of that set. Only GDC's two rate rows are
written (declined, silent_wrong); no comparison is computed.

    python scripts/question_clustered_stats.py

Outputs, under reports/stats/: verification.txt, rates.csv, comparisons.csv,
seed_agreement.csv, per_question.csv, results_summary.txt, run_info.json. They are
deterministic: a second run writes the same bytes.

Adaptations of the development tree's script (question_clustered_stats.py, which also
builds a scratch copy of the worktree and analyses the risk sets): no scratch copy and no
--refresh/--compare; the risk and pooled-with-risk sets are dropped; run_info.json holds no
timestamp or commit.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import math
import os
import runpy
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "stats"
N_BOOT = 10_000
BASE_SEED = 20261001
GDC = "network"
ALTS = ("planner", "react", "code_agent", "text_to_sql")
SYSTEMS = (GDC,) + ALTS
CLASSES = ("correct", "silent_wrong", "refused_should_answer", "crash", "timeout")
S40ON_BASE = "decline_spider_oosr_pooled"      # the set whose cells and resamples the s40on rows share
S40ON = S40ON_BASE + "_s40on"                  # GDC told it may decline (table-coverage instruction on)


# ── 1. builder ───────────────────────────────────────────────────────────────
def run_builder() -> dict:
    os.environ["PARITY_TEMP0"] = "1"
    sys.dont_write_bytecode = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        G = runpy.run_path(str(ROOT / "scripts" / "build_paper_numbers.py"), run_name="__main__")
    return G


# ── 3. per-cell verdicts through the builder's own functions ────────────────
def canon(res) -> str:
    """Canonical text of a scored result, for seed-identity checks."""
    try:
        import pandas as pd
        if isinstance(res, pd.DataFrame):
            return "DF:" + res.to_csv()
        if isinstance(res, pd.Series):
            return "S:" + res.to_csv()
        return json.dumps(res, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        return repr(res)


def derive(G) -> tuple[dict, dict]:
    """-> (cells, spec). cells[set] = list of cell dicts; spec[set] = how to find
    the builder's aggregate rows for that set."""
    pi = G["pi"]; score = G["score"]; score_tt = G["score_tt"]; scored_input = G["scored_input"]
    outcome = G["outcome"]; syn_rows = G["syn_rows"]; _load = G["_load"]
    SPIDER = G["SPIDER"]; GOLD_SPIDER = G["GOLD_SPIDER"]; SB = G["SPIDER_BOUNDARY"]
    IC = G["IN_CONTRACT_SYN"]; VS = dict(G["VOCAB_SPLIT"])

    def temp0(dom, r):
        # Every overlay file is checked by parity_overlay to record temperature 0.
        # GDC and planner hard-code temperature=0 in their own calls, as do the employee and
        # Spider runs that descend from them.
        if r.get("overlay_source"):
            return True
        return r.get("system") in pi.REPLAYED

    def cell(dom, r, cls, res, stratum, status):
        return {"system": r["system"], "dom": dom, "qid": r["qid"], "seed": r["seed"], "cls": cls,
                "res": f"{status}|{canon(res)}", "temp0": temp0(dom, r), "stratum": stratum,
                "src": r.get("overlay_source") or ""}

    cells: dict = defaultdict(list); spec: dict = {}

    # Spider in-contract answer tier, strict s48 and tolerant s48t (Fig1/Fig2/Table3)
    for s in SYSTEMS:
        for d in SPIDER:
            for r in _load(pi.spider_file(d, s)):
                if r.get("system") != s:
                    continue
                g = GOLD_SPIDER[d].get(r["qid"])
                if g is None or g["tier"] in SB:
                    continue
                res, status = scored_input(d, r)
                sv = score(res, g, status=status)[0]
                tv = score_tt(res, g, r["qid"], status=status)[0]
                cells["spider_answer_strict"].append(cell(d, r, sv, res, "spider", status))
                cells["spider_answer_tolerant"].append(cell(d, r, tv, res, "spider", status))
    spec["spider_answer_strict"] = ("Fig1/Fig2/Table3", "spider_pooled", "in_contract_answer", "strict", "answer")
    spec["spider_answer_tolerant"] = ("Fig1/Fig2/Table3", "spider_pooled", "in_contract_answer", "tolerant", "answer")

    # employee in-contract (Table2)
    keep = VS["in_contract_answer"]
    for s in SYSTEMS:
        for tier in [t for t in IC if keep(t)]:
            rows, gold, _ = syn_rows("employee", s, tier)
            for r in rows:
                g = gold.get(r["qid"])
                if g is None:
                    continue
                res, st = scored_input("employee", r)
                cells["employee_in_contract"].append(cell("employee", r, outcome("employee", r, g), res, "employee", st))
    spec["employee_in_contract"] = ("Table2", "employee", "in_contract_answer", "strict", "answer")

    # employee T4 (Table1) — the tier §5.6 cites
    for s in SYSTEMS:
        rows, gold, _ = syn_rows("employee", s, "T4_business_rule_multi_op")
        for r in rows:
            g = gold.get(r["qid"])
            if g is None:
                continue
            res, st = scored_input("employee", r)
            cells["employee_T4"].append(cell("employee", r, outcome("employee", r, g), res, "employee", st))
    spec["employee_T4"] = ("Table1", "employee", "T4_business_rule_multi_op", "strict", "answer")

    # m4 ADAPTATION B1 (D9): employee tolerant beside strict, scored by the builder's own score_tt_syn
    score_tt_syn = G["score_tt_syn"]
    for s in SYSTEMS:
        for tier in [t for t in IC if VS["in_contract_answer"](t)]:
            rows, gold, _ = syn_rows("employee", s, tier)
            for r in rows:
                g = gold.get(r["qid"])
                if g is None:
                    continue
                res, st = scored_input("employee", r)
                tv = score_tt_syn("employee", res, g, r["qid"], status=st)[0]
                cells["employee_in_contract_tolerant"].append(cell("employee", r, tv, res, "employee", st))
                if tier == "T3_three_ops":
                    cells["employee_T3_strict"].append(cell("employee", r, outcome("employee", r, g), res, "employee", st))
                    cells["employee_T3_tolerant"].append(cell("employee", r, tv, res, "employee", st))
    spec["employee_in_contract_tolerant"] = ("Table2", "employee", "in_contract_answer", "tolerant", "answer")
    spec["employee_T3_strict"] = ("Table1", "employee", "T3_three_ops", "strict", "answer")
    spec["employee_T3_tolerant"] = ("Table1", "employee", "T3_three_ops", "tolerant", "answer")

    # m2b ADAPTATION A1 (D8, one decline rule): the builder's Table 5 class is oos_class(),
    # not outcome(). A refusal counts toward `declined` only when it is an explicit or a
    # typed decline; any other refusal (SQL error, unparseable plan with no decline text,
    # empty result) is the class "failed". Here `declined` keeps the class name "correct"
    # (so analyse() is unchanged); the channel is kept for the declined_explicit /
    # declined_typed check in verify().
    oos_class = G["oos_class"]

    def dcell(dom, r, g, res, stratum, status):
        oc = oos_class(dom, r, g)
        c = cell(dom, r, "correct" if oc.startswith("declined_") else oc, res, stratum, status)
        c["chan"] = oc[len("declined_"):] if oc.startswith("declined_") else ""
        return c

    # decline tiers (Table5): Spider out_of_scope_register per database and pooled; employee out_of_range
    for s in SYSTEMS:
        for d in SPIDER:
            for r in _load(pi.spider_file(d, s)):
                if r.get("system") != s or GOLD_SPIDER[d].get(r["qid"], {}).get("tier") != "out_of_scope_register":
                    continue
                res, st = scored_input(d, r)
                c = dcell(d, r, GOLD_SPIDER[d][r["qid"]], res, "spider", st)            # m2b A1
                cells[f"decline_spider_oosr_{d}"].append(c)
                cells["decline_spider_oosr_pooled"].append(c)
        rows, gold, _ = syn_rows("employee", s, "out_of_range")
        for r in rows:
            g = gold.get(r["qid"])
            if g is None:
                continue
            res, st = scored_input("employee", r)
            cells["decline_employee_oor"].append(dcell("employee", r, g, res, "employee", st))   # m2b A1
    for d in SPIDER:
        spec[f"decline_spider_oosr_{d}"] = ("Table5", d, "out_of_scope_register", "strict", "decline")
    spec["decline_spider_oosr_pooled"] = ("Table5", "spider_pooled", "out_of_scope_register", "strict", "decline")
    spec["decline_employee_oor"] = ("Table5", "employee", "out_of_range", "strict", "decline")

    # GDC told it may decline: the same tier from GDC's instruction-on files, classed by the same
    # oos_class(). GDC only, and not in spec: verify_s40on() checks it and main() analyses it.
    for d in SPIDER:
        for r in _load(pi.spider_file(d, GDC, "on")):
            if r.get("system") != GDC or GOLD_SPIDER[d].get(r["qid"], {}).get("tier") != "out_of_scope_register":
                continue
            res, st = scored_input(d, r)
            cells[S40ON].append(dcell(d, r, GOLD_SPIDER[d][r["qid"]], res, "spider", st))
    return dict(cells), spec


POOLED = {
    "pooled_decline": ("decline_spider_oosr_pooled", "decline_employee_oor"),
}
ORDER = ["employee_in_contract", "employee_in_contract_tolerant", "employee_T3_strict", "employee_T3_tolerant",
         "employee_T4", "spider_answer_strict", "spider_answer_tolerant",
         "decline_spider_oosr_pooled", "decline_spider_oosr_wta_1", "decline_spider_oosr_world_1",
         "decline_spider_oosr_bike_1", "decline_employee_oor"] + list(POOLED)
SUPPLEMENTARY = {"employee_T4", "employee_T3_strict", "employee_T3_tolerant", "decline_spider_oosr_wta_1",
                 "decline_spider_oosr_world_1", "decline_spider_oosr_bike_1"}


def verify(G, cells, spec) -> list[str]:
    """Every outcome-class count and denominator must equal the builder's row and the CSV."""
    rows = {(r["fig_table"], r["domain"], r["system"], r["tier_group"], r["scoring"], r["metric"]):
            (int(r["numerator"]), int(r["denominator"])) for r in G["ROWS"]}
    with open(ROOT / "reports" / "paper_numbers.csv", newline="", encoding="utf-8") as fh:
        disk = {(r["fig_table"], r["domain"], r["system"], r["tier_group"], r["scoring"], r["metric"]):
                (int(r["numerator"]), int(r["denominator"])) for r in csv.DictReader(fh)}
    if rows != disk:
        raise SystemExit("builder ROWS differ from the paper_numbers.csv it wrote")
    if G["pi"].HAS_RISK:
        raise SystemExit("risk data is present: this script analyses the employee and Spider sets only")
    log, bad = [], []
    for name, (ft, dom, tg, scoring, kind) in spec.items():
        for s in SYSTEMS:
            cs = [c for c in cells[name] if c["system"] == s]
            cnt = Counter(c["cls"] for c in cs); n = len(cs)
            log_extra = ""                                                    # m2b A2
            if kind == "answer":
                metrics = [("accuracy", "correct")] + [(c, c) for c in CLASSES[1:]]
            else:
                # m2b ADAPTATION A2 (D8): Table 5 also has `failed`, and `declined` splits into
                # declined_explicit + declined_typed; all three are checked against the builder.
                metrics = [("declined", "correct"), ("silent_wrong", "silent_wrong"), ("crash", "crash"),
                           ("timeout", "timeout"), ("failed", "failed")]
                if set(cnt) - {"correct", "silent_wrong", "crash", "timeout", "failed"}:
                    bad.append(f"{name}/{s}: class outside the Table5 partition: {dict(cnt)}")
                for metric, ch in (("declined_explicit", "explicit"), ("declined_typed", "typed")):
                    key = (ft, dom, s, tg, scoring, metric)
                    kc = sum(1 for c in cs if c.get("chan") == ch)
                    if key not in rows:
                        bad.append(f"{name}/{s}: no builder row {key}")
                    elif rows[key] != (kc, n):
                        bad.append(f"{name}/{s}/{metric}: derived {kc}/{n} != builder {rows[key]}")
                    log_extra += f" {metric}={kc}"
            for metric, cls in metrics:
                key = (ft, dom, s, tg, scoring, metric)
                if key not in rows:
                    bad.append(f"{name}/{s}: no builder row {key}")
                    continue
                if rows[key] != (cnt.get(cls, 0), n):
                    bad.append(f"{name}/{s}/{metric}: derived {cnt.get(cls, 0)}/{n} != builder {rows[key]}")
            if sum(cnt.values()) != n:
                bad.append(f"{name}/{s}: classes do not partition the cells")
            log.append(f"OK  {name:<30} {s:<12} n={n:<4} " + " ".join(f"{k}={v}" for k, v in sorted(cnt.items()))
                       + log_extra)                                           # m2b A2
    if bad:
        raise SystemExit("VERIFICATION FAILED:\n  " + "\n  ".join(bad))
    return log


def verify_s40on(G, cells) -> list[str]:
    """GDC's instruction-on cells must have the keys of its decline_spider_oosr_pooled cells, carry no
    overlay, and give per database the builder's Fig3/Fig4 out_of_scope_register_s40on counts: its
    `correct` (any refusal) equals the declines here, so no refusal is a failure."""
    on = cells[S40ON]
    off = [c for c in cells[S40ON_BASE] if c["system"] == GDC]
    key = lambda c: (c["dom"], c["qid"], c["seed"])                      # noqa: E731
    bad = []
    if sorted(map(key, on)) != sorted(map(key, off)) or len(set(map(key, on))) != len(on):
        bad.append(f"{S40ON}: cell keys differ from GDC's {S40ON_BASE} cells")
    if any(c["src"] for c in on):
        bad.append(f"{S40ON}: an overlay replaced an instruction-on cell")
    rows = {(r["fig_table"], r["domain"], r["system"], r["tier_group"], r["scoring"], r["metric"]):
            (int(r["numerator"]), int(r["denominator"])) for r in G["ROWS"]}
    log = [f"{S40ON}: GDC told it may decline (table-coverage instruction on, "
           f"results_spider_<db>_postfix_s40on.jsonl), {len(on)} cells over "
           f"{len({(c['dom'], c['qid']) for c in on})} questions, the keys of GDC's {S40ON_BASE} cells, "
           "no overlay; per database the declines (correct=) and silent_wrong equal the builder's Fig3/Fig4 "
           "out_of_scope_register_s40on correct (any refusal) and silent_wrong, so no refusal is a failure:"]
    for d in list(G["SPIDER"]) + ["pooled"]:
        cs = on if d == "pooled" else [c for c in on if c["dom"] == d]
        cnt = Counter(c["cls"] for c in cs); n = len(cs)
        if set(cnt) - {"correct", "silent_wrong", "crash", "timeout", "failed"}:
            bad.append(f"{S40ON}/{d}: class outside the Table5 partition: {dict(cnt)}")
        if d != "pooled":
            for metric in ("correct", "silent_wrong"):
                k = ("Fig3/Fig4", d, GDC, "out_of_scope_register_s40on", "strict", metric)
                if k not in rows:
                    bad.append(f"{S40ON}/{d}: no builder row {k}")
                elif rows[k] != (cnt.get(metric, 0), n):
                    bad.append(f"{S40ON}/{d}/{metric}: derived {cnt.get(metric, 0)}/{n} != builder {rows[k]}")
        ch = Counter(c["chan"] for c in cs if c["cls"] == "correct")
        log.append(f"  {d:<8} {GDC:<12} n={n:<4} " + " ".join(f"{k}={v}" for k, v in sorted(cnt.items()))
                   + f" declined_explicit={ch.get('explicit', 0)} declined_typed={ch.get('typed', 0)}")
    if bad:
        raise SystemExit("VERIFICATION FAILED:\n  " + "\n  ".join(bad))
    return log


# ── 4. statistics ────────────────────────────────────────────────────────────
def per_question(cs: list) -> dict:
    """system -> {(dom, qid): {seeds, classes, results, temp0, stratum}}"""
    out: dict = defaultdict(dict)
    for c in cs:
        q = out[c["system"]].setdefault((c["dom"], c["qid"]), {"seeds": [], "cls": [], "res": [], "temp0": [],
                                                                  "stratum": c["stratum"]})
        q["seeds"].append(c["seed"]); q["cls"].append(c["cls"]); q["res"].append(c["res"]); q["temp0"].append(c["temp0"])
    return out


def matrices(pq: dict, cls: str):
    """Aligned per-question arrays: keys, m (seeds), stratum, k[system] (cells of class cls)."""
    keys = sorted(pq[GDC])
    for s in SYSTEMS:
        if sorted(pq[s]) != keys:
            raise SystemExit(f"question sets differ between {GDC} and {s}")
        for k in keys:
            if sorted(pq[s][k]["seeds"]) != sorted(pq[GDC][k]["seeds"]):
                raise SystemExit(f"seed sets differ for {k} between {GDC} and {s}")
            if len(set(pq[s][k]["seeds"])) != len(pq[s][k]["seeds"]):
                raise SystemExit(f"duplicate seeds for {k} {s}")
    m = np.array([len(pq[GDC][k]["seeds"]) for k in keys])
    strata = np.array([pq[GDC][k]["stratum"] for k in keys])
    K = {s: np.array([pq[s][k]["cls"].count(cls) for k in keys]) for s in SYSTEMS}
    return keys, m, strata, K


def boot_index(strata: np.ndarray, rng) -> np.ndarray:
    n = len(strata); idx = np.empty((N_BOOT, n), dtype=np.int64)
    for h in sorted(set(strata)):
        pos = np.where(strata == h)[0]
        idx[:, pos] = pos[rng.integers(0, len(pos), size=(N_BOOT, len(pos)))]
    return idx


def cluster_var(z: np.ndarray, strata: np.ndarray, M: float) -> float:
    """Linearised (stratified) cluster variance of a ratio estimator; z_q = residual numerator."""
    v = 0.0
    for h in sorted(set(strata)):
        zh = z[strata == h]; nh = len(zh)
        if nh > 1:
            v += nh / (nh - 1) * float(((zh - zh.mean()) ** 2).sum())
    return v / M ** 2


def wilson(k: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (math.nan, math.nan)
    p = k / n; den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def newcombe(k1, k2, n):
    p1, p2 = k1 / n, k2 / n; (l1, u1), (l2, u2) = wilson(k1, n), wilson(k2, n); d = p1 - p2
    return (d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


def sign_test(d: np.ndarray) -> tuple[int, int, float]:
    npos, nneg = int((d > 0).sum()), int((d < 0).sum()); N = npos + nneg
    if N == 0:
        return npos, nneg, 1.0
    tail = sum(math.comb(N, i) for i in range(min(npos, nneg) + 1)) / 2 ** N
    return npos, nneg, min(1.0, 2 * tail)


def permutation_p(dint: np.ndarray) -> float:
    """Exact two-sided sign-flip permutation p of sum(dint) (integer per-question differences)."""
    a = [int(abs(x)) for x in dint if x != 0]
    T = abs(int(dint.sum()))
    if not a:
        return 1.0
    off = sum(a); dist = np.zeros(2 * off + 1); dist[off] = 1.0
    for x in a:
        nd = np.zeros_like(dist)
        nd[x:] += 0.5 * dist[:len(dist) - x]
        nd[:len(dist) - x] += 0.5 * dist[x:]
        dist = nd
    vals = np.arange(-off, off + 1)
    return float(min(1.0, dist[np.abs(vals) >= T].sum()))


def holm(ps: list[float]) -> list[float]:
    order = sorted(range(len(ps)), key=lambda i: ps[i]); adj = [0.0] * len(ps); run = 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - r) * ps[i])); adj[i] = run
    return adj


def analyse(name: str, cs: list, kind: str):
    pq = per_question(cs)
    rng = np.random.default_rng(BASE_SEED + zlib.crc32(name.encode()))
    keys, m, strata, _ = matrices(pq, "correct")
    idx = boot_index(strata, rng)                  # one resample set per set: common to every system/metric
    M = float(m.sum()); L = math.lcm(*[int(x) for x in set(m)])
    rates, comps = [], []
    for metric, cls in (("silent_wrong", "silent_wrong"), ("declined" if kind == "decline" else "accuracy", "correct")):
        _, _, _, K = matrices(pq, cls)
        Mb = m[idx].sum(1)
        for s in SYSTEMS:
            k = K[s]; p = k.sum() / M
            bt = k[idx].sum(1) / Mb
            v = cluster_var(k - p * m, strata, M)
            rates.append({"set": name, "metric": metric, "system": s, "k": int(k.sum()), "n_cells": int(M),
                          "n_questions": len(keys), "rate_pct": 100 * p,
                          "ci_lo_pct": 100 * np.percentile(bt, 2.5), "ci_hi_pct": 100 * np.percentile(bt, 97.5),
                          "n_eff": (p * (1 - p) / v) if v > 0 else math.nan,
                          "questions_with_any": int((k > 0).sum())})
        ps = []
        for a in ALTS:
            dk = K[GDC] - K[a]; diff = dk.sum() / M
            bt = dk[idx].sum(1) / Mb
            lo, hi = np.percentile(bt, 2.5), np.percentile(bt, 97.5)
            drate = dk / m                                       # per-question rate differences
            npos, nneg, psign = sign_test(drate)
            pperm = permutation_p((dk * (L // m)).astype(np.int64))
            pG, pA = K[GDC].sum() / M, K[a].sum() / M
            vclu = cluster_var(dk - diff * m, strata, M); vnaive = (pG * (1 - pG) + pA * (1 - pA)) / M
            nlo, nhi = newcombe(int(K[GDC].sum()), int(K[a].sum()), int(M))
            by_h = " ".join(f"{h}:{int((drate[strata == h] > 0).sum())}/{int((drate[strata == h] < 0).sum())}"
                            for h in sorted(set(strata)))
            comps.append({"set": name, "metric": metric, "alt": a,
                          "gdc_pct": 100 * pG, "alt_pct": 100 * pA, "diff_pp": 100 * diff,
                          "ci_lo_pp": 100 * lo, "ci_hi_pp": 100 * hi,
                          "excludes_0": bool(lo > 1e-12 or hi < -1e-12),
                          "diff_qweighted_pp": 100 * float(drate.mean()),
                          "n_questions": len(keys), "n_cells": int(M),
                          "n_discordant_q": npos + nneg, "q_gdc_higher": npos, "q_alt_higher": nneg,
                          "p_sign": psign, "p_perm": pperm,
                          "discordant_by_stratum_gdc_higher/alt_higher": by_h,
                          "n_eff_diff_vs_naive_unpaired": (int(M) * vnaive / vclu) if vclu > 0 else math.nan,
                          "naive_cell_ci_lo_pp": 100 * nlo, "naive_cell_ci_hi_pp": 100 * nhi,
                          "naive_excludes_0": bool(nlo > 1e-12 or nhi < -1e-12)})
            ps.append(pperm)
        for c, ph in zip(comps[-len(ALTS):], holm(ps)):
            c["p_perm_holm"] = ph
            # The percentile bootstrap is anti-conservative when few questions are discordant
            # (4 one-sided discordant questions of 27 exclude 0, though no exact test can reach
            # p < 0.125 on 4); a difference counts as resolved only when both agree.
            c["verdict"] = ("resolved" if c["excludes_0"] and c["p_perm"] < 0.05 else
                            "bootstrap-only" if c["excludes_0"] else
                            "exact-test-only" if c["p_perm"] < 0.05 else "unresolved")
            c["resolved_after_holm"] = bool(c["excludes_0"] and ph < 0.05)
    return rates, comps, pq


def seed_agreement(name: str, pq: dict) -> list[dict]:
    out = []
    for s in SYSTEMS:
        qs = list(pq[s].values())
        for label, sel in (("all", lambda q: True), ("temp0", lambda q: all(q["temp0"])),
                           ("default_temp", lambda q: not any(q["temp0"]))):
            sub = [q for q in qs if sel(q)]
            if not sub:
                continue
            pairs = agree = 0
            for q in sub:
                c = q["cls"]
                for i in range(len(c)):
                    for j in range(i + 1, len(c)):
                        pairs += 1; agree += c[i] == c[j]
            out.append({"set": name, "system": s, "cells": label, "n_questions": len(sub),
                        "seeds_per_q": "/".join(str(x) for x in sorted({len(q['cls']) for q in sub})),
                        "class_unanimous_pct": 100 * sum(len(set(q["cls"])) == 1 for q in sub) / len(sub),
                        "result_identical_pct": 100 * sum(len(set(q["res"])) == 1 for q in sub) / len(sub),
                        "pairwise_class_agree_pct": 100 * agree / pairs if pairs else math.nan,
                        "temp0_cells_pct": 100 * sum(sum(q["temp0"]) for q in sub) / sum(len(q["temp0"]) for q in sub)})
    return out


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()})


def fmt_p(p):
    return f"{p:.4f}" if p >= 1e-4 else f"{p:.1e}"


def summary(comps, rates, agree, info) -> str:
    L = [f"Question-clustered comparisons, employee and Spider (engine {info['engine_sha256'][:12]}, "
         f"batch2 files: {', '.join(info['batch2_present']) or 'none'})",
         f"Cluster bootstrap: {N_BOOT} resamples of questions (stratified by domain in pooled sets), "
         f"seed {BASE_SEED}+crc32(set); percentile 95% CI. diff = GDC minus alternative, percentage points.",
         "p_sign: exact two-sided sign test over questions; p_perm: exact two-sided sign-flip permutation test "
         "on per-question rate differences; Holm over the four alternatives per set and metric.",
         "naive = Newcombe interval treating every cell as independent (what a per-cell Wilson analysis implies).", ""]
    for name in ORDER:
        cs = [c for c in comps if c["set"] == name]
        if not cs:
            continue
        ne = {r["system"]: r["n_eff"] for r in rates if r["set"] == name and r["metric"] == "silent_wrong"}
        L.append(f"== {name}{' (supplementary)' if name in SUPPLEMENTARY else ''}: "
                 f"{cs[0]['n_questions']} questions, {cs[0]['n_cells']} cells per system; design-effect n_eff "
                 f"(silent rate): " + ", ".join(f"{s} {'-' if math.isnan(v) else round(v)}" for s, v in ne.items()))
        for metric in dict.fromkeys(c["metric"] for c in cs):
            g = next(r for r in rates if r["set"] == name and r["metric"] == metric and r["system"] == GDC)
            L.append(f"  {metric}: GDC {g['rate_pct']:.1f}% [{g['ci_lo_pct']:.1f}, {g['ci_hi_pct']:.1f}] "
                     f"({g['k']}/{g['n_cells']}; {g['questions_with_any']} questions with any)")
            for c in (c for c in cs if c["metric"] == metric):
                L.append(f"    vs {c['alt']:<11} alt {c['alt_pct']:5.1f}%  diff {c['diff_pp']:+6.1f} "
                         f"[{c['ci_lo_pp']:+6.1f}, {c['ci_hi_pp']:+6.1f}] {'EXCLUDES 0' if c['excludes_0'] else 'includes 0':<10}"
                         f"  discordant q {c['n_discordant_q']:>3} (GDC higher {c['q_gdc_higher']}, alt higher {c['q_alt_higher']})"
                         f"  p_sign {fmt_p(c['p_sign'])}  p_perm {fmt_p(c['p_perm'])} (Holm {fmt_p(c['p_perm_holm'])})"
                         f"  -> {c['verdict'].upper()}{' (survives Holm)' if c['resolved_after_holm'] else ''}"
                         f"  naive-cell [{c['naive_cell_ci_lo_pp']:+.1f}, {c['naive_cell_ci_hi_pp']:+.1f}]"
                         f"{' excl' if c['naive_excludes_0'] else ''}"
                         + (f"  by domain {c['discordant_by_stratum_gdc_higher/alt_higher']}" if name.startswith("pooled") else ""))
        L.append("")
    L.append("Overview: GDC minus alternative, pp, with verdict (R = bootstrap CI excludes 0 AND exact permutation p < 0.05; "
             "R* = also after Holm; b = bootstrap CI only; t = exact test only; . = unresolved)")
    L.append(f"  {'set':<30} {'metric':<12} " + " ".join(f"{a:>17}" for a in ALTS))
    mark = {"resolved": "R", "bootstrap-only": "b", "exact-test-only": "t", "unresolved": "."}
    for name in ORDER:
        for metric in dict.fromkeys(c["metric"] for c in comps if c["set"] == name):
            row = []
            for a in ALTS:
                c = next(c for c in comps if c["set"] == name and c["metric"] == metric and c["alt"] == a)
                v = mark[c["verdict"]] + ("*" if c["resolved_after_holm"] else "")
                row.append(f"{c['diff_pp']:+6.1f} {v:<2}".rjust(17))
            L.append(f"  {name:<30} {metric:<12} " + " ".join(row))
    L.append("")
    L.append("Seed agreement per system (share of questions whose seeds all give one outcome class / one identical "
             "scored result; pairwise = share of seed pairs agreeing on the class; temp0 cells = share of the set's cells "
             "emitted at temperature 0):")
    for a in agree:
        if a["cells"] == "all" and a["set"] in ("employee_in_contract", "spider_answer_strict",
                                                "decline_spider_oosr_pooled", "decline_employee_oor"):
            L.append(f"  {a['set']:<28} {a['system']:<12} {a['cells']:<12} q={a['n_questions']:<3} seeds={a['seeds_per_q']:<3} "
                     f"class-unanimous {a['class_unanimous_pct']:5.1f}%  result-identical {a['result_identical_pct']:5.1f}%  "
                     f"pairwise {a['pairwise_class_agree_pct']:5.1f}%  temp0 cells {a['temp0_cells_pct']:5.1f}%")
    s40 = [r for r in rates if r["set"] == S40ON]
    if s40:
        L.append("")
        L.append(f"== {S40ON}: GDC told it may decline (table-coverage instruction on), {s40[0]['n_questions']} "
                 f"questions, {s40[0]['n_cells']} cells; intervals on the resamples of {S40ON_BASE}; "
                 "rates only, no comparison")
        for r in s40:
            L.append(f"  {r['metric']}: GDC {r['rate_pct']:.1f}% [{r['ci_lo_pct']:.1f}, {r['ci_hi_pct']:.1f}] "
                     f"({r['k']}/{r['n_cells']}; {r['questions_with_any']} questions with any)")
    return "\n".join(L) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    G = run_builder()
    cells, spec = derive(G)
    vlog = verify(G, cells, spec)
    slog = verify_s40on(G, cells)
    for name, parts in POOLED.items():
        cells[name] = [c for part in parts for c in cells[part]]   # stratum = the cell's domain (employee/spider)
        keys = [(c["system"], c["dom"], c["qid"], c["seed"]) for c in cells[name]]
        if len(keys) != len(set(keys)):
            raise SystemExit(f"{name}: a cell appears in two pooled components")
    rates, comps, agree, pqrows = [], [], [], []
    for name in ORDER:
        kind = "decline" if name.startswith(("decline", "pooled_decline")) else "answer"
        r, c, pq = analyse(name, cells[name], kind)
        rates += r; comps += c; agree += seed_agreement(name, pq)
        if name in POOLED:
            continue
        for s in SYSTEMS:
            for (dom, qid), q in sorted(pq[s].items()):
                pqrows.append({"set": name, "system": s, "domain": dom, "qid": qid, "seeds": len(q["cls"]),
                               **{k: q["cls"].count(k) for k in CLASSES + ("failed",)},
                               "class_unanimous": len(set(q["cls"])) == 1, "result_identical": len(set(q["res"])) == 1,
                               "temp0_cells": sum(q["temp0"])})
    # GDC told it may decline: its cells in place of GDC's, under the base set's name (so the base
    # set's seed and resamples); GDC's rows only, renamed to the s40on set
    mix = [c for c in cells[S40ON_BASE] if c["system"] != GDC] + cells[S40ON]
    r, _, _ = analyse(S40ON_BASE, mix, "decline")
    rates += [dict(x, set=S40ON) for x in r if x["system"] == GDC]
    eng = json.loads((ROOT / "reports" / "paper_numbers.engine.json").read_text())
    info = {"engine_sha256": eng["engine_sha256"], "csv_sha256": eng["csv_sha256"],
            "batch2_present": sorted(p.name for p in (ROOT / "results").glob("results_temp0_batch2_*.jsonl")
                                     if not p.name.endswith("_alias.jsonl")),
            "n_boot": N_BOOT, "base_seed": BASE_SEED,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (OUT / "run_info.json").write_text(json.dumps(info, indent=1) + "\n")
    (OUT / "verification.txt").write_text(
        "Every outcome-class count and denominator below equals the builder's emitted row and "
        "reports/paper_numbers.csv (the script exits before this file otherwise).\n"
        + "\n".join(vlog) + "\n" + "\n".join(slog) + "\n")
    write_csv(OUT / "rates.csv", rates); write_csv(OUT / "comparisons.csv", comps)
    write_csv(OUT / "seed_agreement.csv", agree); write_csv(OUT / "per_question.csv", pqrows)
    text = summary(comps, rates, agree, info)
    (OUT / "results_summary.txt").write_text(text)
    print(f"verification: {len(vlog)} (set, system) groups equal the builder's rows and the CSV")
    print(f"outputs: {OUT.relative_to(ROOT)}/ ({len(comps)} comparisons over {len(ORDER)} sets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
