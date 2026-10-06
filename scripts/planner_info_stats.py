#!/usr/bin/env python
"""Statistics of the post hoc equal-information run (review/planner_info_prereg.md): the planner
under its reported prompt with the shared briefing replaced by GDC's domain information (system
planner_gdcinfo, results/results_temp0_planner_gdcinfo.jsonl, run by scripts/run_planner_info.py).

If the results file is absent the script prints one line, exits 0 and writes nothing, so a
checkout without the run regenerates exactly what it did before. A file that is present must be
the whole run: its provenance records temperature 0, the model of record, max_tokens 1500, every
pinned informed prompt and `complete`, and its rows are exactly the 398 planned cells, each a
completed cell; anything else stops the script (exit 1).

Cells. Every planner_gdcinfo cell is scored as every planner cell is: its recorded reply is
replayed through the shipped executor (planner_upfront.execute_reply) on the fixed engine, and the
result is scored strict s48 and tie-tolerant s48t.
  employee   the builder's own functions on the row read as a planner row (scored_input, outcome,
             score_tt_syn), as question_clustered_stats scores employee_in_contract
  Spider     the same (scored_input, score, score_tt), as spider_answer_strict / _tolerant
  held-out   execute_reply on the held-out register, then heldout/analyse_heldout.py's score and
             score_tt; the replay must equal the cell as run
GDC's and the reported planner's cells are those the paper reports: employee and Spider from
question_clustered_stats' employee_in_contract[_tolerant] and spider_answer_strict/_tolerant sets
(the builder in process, checked against reports/paper_numbers.csv), held-out from
heldout/analyse_heldout.load_cells (as run, A.10), checked against heldout/results/analysis/rates.csv.

Sets (stratified by database; question_clustered_stats.analyse(), GDC minus planner_gdcinfo):
  planner_info_primary_tolerant   employee + held-out, s48t: the pre-registered primary set
  planner_info_primary_strict     the same, s48
  planner_info_spider_tolerant    Spider's answer tier, s48t (GDC's development set)
  planner_info_spider_strict      the same, s48
Each gives accuracy and silent_wrong: paired difference, 95% percentile interval from a
question-level cluster bootstrap (10,000 resamples, seeded by the set's name), discordant
questions, exact sign test and exact sign-flip permutation p. One comparison per set and metric,
so p_perm_holm equals p_perm.

Pre-registered rule, on planner_info_primary_tolerant accuracy: OUTCOME A no resolved difference;
B GDC resolved as more accurate; C the informed planner resolved as more accurate. Resolved = the
interval excludes 0 and the exact p < 0.05 (verdict "resolved"). The outcome under s48 is given
beside it.

Outputs, in reports/stats/planner_info/ (new files only):
  rates.csv           accuracy and silent_wrong per set and system (GDC, planner_gdcinfo), with intervals
  per_database.csv    per database and pooled: accuracy and silent_wrong of planner_gdcinfo, the
                      reported planner and GDC, strict and tolerant
  per_question.csv    every question where planner_gdcinfo and GDC differ: classes per seed, both rules,
                      with the reported planner's classes beside them
  headline.csv        the numbers a write-up takes, one row per key
  summary.txt         all of the above in words, with the pre-registered outcome
and rows appended to reports/stats/comparisons.csv under the four set names above (any earlier
planner_info_* rows are replaced; every other byte of the file is kept).

    python scripts/planner_info_stats.py        # run_all.sh step 6, after alias_control_stats.py
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

RESULTS = ROOT / "results" / "results_temp0_planner_gdcinfo.jsonl"
OUT = ROOT / "reports" / "stats" / "planner_info"
COMPARISONS = ROOT / "reports" / "stats" / "comparisons.csv"
SET_PREFIX = "planner_info_"
NEW = "planner_gdcinfo"
GDC = "network"
PLANNER = "planner"
SETS = {("primary", "tolerant"): SET_PREFIX + "primary_tolerant",
        ("primary", "strict"): SET_PREFIX + "primary_strict",
        ("spider", "tolerant"): SET_PREFIX + "spider_tolerant",
        ("spider", "strict"): SET_PREFIX + "spider_strict"}
BASE_SET = {("employee", "strict"): "employee_in_contract", ("employee", "tolerant"): "employee_in_contract_tolerant",
            ("spider", "strict"): "spider_answer_strict", ("spider", "tolerant"): "spider_answer_tolerant"}
DATABASES = ("employee", "employee_hire_evaluation", "train_station", "wta_1", "world_1", "bike_1")
HELDOUT = ("employee_hire_evaluation", "train_station")
SPIDER = ("wta_1", "world_1", "bike_1")
SCORINGS = ("tolerant", "strict")


@contextlib.contextmanager
def _pair(Q, first: str, second: str):
    """question_clustered_stats.analyse() over two systems: `first` in GDC's place, `second` as
    the only alternative (Holm over one comparison is the comparison's own p)."""
    saved = Q.GDC, Q.ALTS, Q.SYSTEMS
    Q.GDC, Q.ALTS, Q.SYSTEMS = first, (second,), (first, second)
    try:
        yield
    finally:
        Q.GDC, Q.ALTS, Q.SYSTEMS = saved


def check_input(path: Path) -> tuple[list[dict], dict]:
    """The rows of a complete run and its provenance, or SystemExit naming what is wrong."""
    from scripts.parity_overlay import incomplete_row
    from scripts.run_planner_info import MAX_TOKENS, MODEL, PROMPTS
    prov_p = path.with_name(path.name.replace(".jsonl", ".provenance.json"))
    if not prov_p.exists():
        raise SystemExit(f"{path.name} has no {prov_p.name}")
    prov = json.loads(prov_p.read_text())
    want = {"system": NEW, "temperature": 0, "model": MODEL, "max_tokens": MAX_TOKENS, "complete": True}
    bad = {k: prov.get(k) for k, v in want.items() if prov.get(k) != v}
    for d, (_, inf) in PROMPTS.items():
        got = ((prov.get("domains") or {}).get(d) or {}).get("informed_prompt_sha256")
        if got != inf:
            bad[f"domains.{d}.informed_prompt_sha256"] = got
    if bad:
        raise SystemExit(f"{prov_p.name} does not record the complete run: {bad}")
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if {r.get("system") for r in rows} != {NEW}:
        raise SystemExit(f"{path.name}: systems {sorted({r.get('system') for r in rows})}, expected [{NEW}]")
    held = sorted((NEW, r["domain"], r["qid"], int(r["seed"])) for r in rows)
    plan = sorted(tuple(k) for k in prov.get("plan_keys") or [])
    if held != plan or len(set(held)) != len(held):
        raise SystemExit(f"{path.name}: {len(set(held) & set(plan))} of {len(plan)} planned cells "
                         f"({len(set(held) - set(plan))} unplanned); the statistics need the whole run")
    inc = sorted((r["qid"], r["seed"], why) for r in rows if (why := incomplete_row(r)))
    if inc:
        raise SystemExit(f"{path.name}: rows that are not completed cells: {inc[:3]}")
    return rows, prov


def builder_cells():
    """question_clustered_stats' verified cells (the builder run in process, as step 6 runs it)."""
    import scripts.question_clustered_stats as Q
    G = Q.run_builder()
    cells, spec = Q.derive(G)
    Q.verify(G, cells, spec)
    return G, cells


def heldout_cells() -> list[dict]:
    """GDC's and the planner's held-out answer-tier cells as the paper reports them (as run), checked
    against heldout/results/analysis/rates.csv."""
    sys.path.insert(0, str(ROOT / "heldout"))
    import analyse_heldout as AH
    cells = [c for c in AH.load_cells(ROOT / "heldout" / "results")
             if c["tier"] == "answer" and c["system"] in (GDC, PLANNER)]
    with open(ROOT / "heldout" / "results" / "analysis" / "rates.csv", newline="", encoding="utf-8") as fh:
        rep = {(r["set"], r["system"], r["metric"]): (int(r["k"]), int(r["n_cells"])) for r in csv.DictReader(fh)}
    for sc in SCORINGS:
        for s in (GDC, PLANNER):
            cs = [c for c in cells if c["system"] == s]
            for metric, cls in (("accuracy", "correct"), ("silent_error", "silent_wrong")):
                got = (sum(c[sc] == cls for c in cs), len(cs))
                want = rep.get((f"heldout_answer_{sc}", s, metric))
                if got != want:
                    raise SystemExit(f"held-out {s} {sc} {metric}: {got} != heldout/results/analysis/rates.csv {want}")
    return cells


def score_new(G, rows: list[dict]) -> list[dict]:
    """Each planner_gdcinfo cell replayed and scored as a planner cell of its database."""
    from evidence.eval_run.adapters.planner_upfront import execute_reply
    from evidence.eval_run.comparator import score
    from evidence.eval_run.types import empty_run_result
    from scripts.question_clustered_stats import canon
    import scripts.planner_info_prompt as PIP
    sys.path.insert(0, str(ROOT / "heldout"))
    import analyse_heldout as AH
    import pandas as pd
    pi = G["pi"]
    regs, golds = {}, {}
    for d in HELDOUT:
        p = PIP.paths(ROOT, d)
        regs[d] = pd.read_pickle(p["register"])
        golds[d] = json.loads(p["gold"].read_text())
    out = []
    for r in rows:
        d, q = r["domain"], r["qid"]
        rr = dict(r, system=PLANNER)                         # the planner's replay path
        art = r.get("artifact") if isinstance(r.get("artifact"), dict) else {}
        if d == "employee":
            g = pi.GOLD["employee"][q]
            res, st = G["scored_input"]("employee", rr)
            strict = G["outcome"]("employee", rr, g)
            tol = G["score_tt_syn"]("employee", res, g, q, status=st)[0]
            asrun = None
        elif d in SPIDER:
            g = G["GOLD_SPIDER"][d][q]
            res, st = G["scored_input"](d, rr)
            strict = G["score"](res, g, status=st)[0]
            tol = G["score_tt"](res, g, q, status=st)[0]
            asrun = None
        else:
            g = golds[d][q]
            text = art.get("raw_text")
            if text is None:
                res, st = r.get("raw_result"), r.get("status", "completed")
            else:
                rec = execute_reply(text, regs[d], empty_run_result())
                res, st = rec["result"], rec["status"]
            strict = score(res, g, status=st)[0]
            tol = AH.score_tt(res, g, q, st)
            asrun = (canon(res) == canon(r.get("raw_result")) and st == r.get("status"))
        out.append({"system": NEW, "dom": d, "qid": q, "seed": int(r["seed"]), "tier": g["tier"],
                    "strict": strict, "tolerant": tol, "res": f"{st}|{canon(res)}", "status": st,
                    "error": r.get("error") or "", "recorded": r.get("outcome_class"), "asrun_equal": asrun})
    return out


def qcell(system: str, dom: str, qid: str, seed: int, cls: str, res: str) -> dict:
    return {"system": system, "dom": dom, "qid": qid, "seed": int(seed), "cls": cls, "res": res,
            "temp0": True, "stratum": dom, "src": ""}


def assemble(cells: dict, ho: list[dict], new: list[dict]) -> dict:
    """{(group, scoring): {system: [cell, ...]}} for GDC, the reported planner and planner_gdcinfo."""
    from scripts.question_clustered_stats import canon
    out = defaultdict(lambda: defaultdict(list))
    for sc in SCORINGS:
        for c in cells[BASE_SET[("employee", sc)]]:
            if c["system"] in (GDC, PLANNER):
                out[("primary", sc)][c["system"]].append(dict(c, stratum="employee"))
        for c in ho:
            out[("primary", sc)][c["system"]].append(qcell(c["system"], c["dom"], c["qid"], c["seed"], c[sc], ""))
        for c in cells[BASE_SET[("spider", sc)]]:
            if c["system"] in (GDC, PLANNER):
                out[("spider", sc)][c["system"]].append(dict(c, stratum=c["dom"]))
        for c in new:
            grp = "spider" if c["dom"] in SPIDER else "primary"
            out[(grp, sc)][NEW].append(qcell(NEW, c["dom"], c["qid"], c["seed"], c[sc], c["res"]))
    for key, by in out.items():                              # one cell per (system, database, question, seed)
        ks = {s: sorted((c["dom"], c["qid"], c["seed"]) for c in cs) for s, cs in by.items()}
        if not (ks[GDC] == ks[PLANNER] == ks[NEW]) or len(set(ks[GDC])) != len(ks[GDC]):
            raise SystemExit(f"{key}: the cells of GDC, the planner and {NEW} differ in keys")
    return out


def compute(G, cells: dict, rows: list[dict], out: Path, comparisons: Path, echo=print) -> dict:
    import scripts.question_clustered_stats as Q
    ho = heldout_cells()
    new = score_new(G, rows)
    asrun_bad = [c for c in new if c["asrun_equal"] is False]
    groups = assemble(cells, ho, new)
    comps, rates = [], []
    for (grp, sc), name in SETS.items():
        cs = groups[(grp, sc)][GDC] + groups[(grp, sc)][NEW]
        with _pair(Q, GDC, NEW):
            r, cc, _ = Q.analyse(name, cs, "answer")
        comps += cc
        rates += r
    by = {(c["set"], c["metric"]): c for c in comps}

    def outcome_of(c) -> str:
        if c["verdict"] != "resolved":
            return "A"
        return "B" if c["diff_pp"] > 0 else "C"
    outcome = {sc: outcome_of(by[(SETS[("primary", sc)], "accuracy")]) for sc in SCORINGS}

    # per database (and pooled), three systems, both rules
    import evidence.eval_run.harness as H
    import scripts.planner_info_prompt as PIP
    corpus = {d: H.load_corpus(PIP.paths(ROOT, d)["corpus"]) for d in DATABASES}
    perdb = []
    for sc in SCORINGS:
        for scope in DATABASES + ("primary_pooled", "spider_pooled"):
            for s in (NEW, PLANNER, GDC):
                if scope == "primary_pooled":
                    cs = groups[("primary", sc)][s]
                elif scope == "spider_pooled":
                    cs = groups[("spider", sc)][s]
                else:
                    cs = [c for g in ("primary", "spider") for c in groups[(g, sc)][s] if c["dom"] == scope]
                n = len(cs)
                k_ok = sum(c["cls"] == "correct" for c in cs)
                k_sw = sum(c["cls"] == "silent_wrong" for c in cs)
                perdb.append({"scoring": sc, "scope": scope, "system": s, "n_cells": n,
                              "n_questions": len({(c["dom"], c["qid"]) for c in cs}),
                              "correct": k_ok, "accuracy_pct": f"{100 * k_ok / n:.4f}",
                              "silent_wrong": k_sw, "silent_wrong_pct": f"{100 * k_sw / n:.4f}",
                              "other": n - k_ok - k_sw})
    # per question: every question where planner_gdcinfo and GDC differ under either rule
    cls = defaultdict(dict)
    for (grp, sc), by_s in groups.items():
        for s, cs in by_s.items():
            for c in cs:
                cls[(grp, c["dom"], c["qid"])].setdefault((s, sc), {})[c["seed"]] = c["cls"]
    newinfo = {(c["dom"], c["qid"], c["seed"]): c for c in new}
    perq = []
    for (grp, d, q), v in sorted(cls.items()):
        differ = any(v[(GDC, sc)] != v[(NEW, sc)] for sc in SCORINGS)
        if not differ:
            continue
        seeds = sorted(v[(GDC, "tolerant")])
        row = {"set": grp, "database": d, "qid": q, "tier": newinfo[(d, q, seeds[0])]["tier"],
               "question": corpus[d][q]["question"], "seeds": " ".join(map(str, seeds))}
        for s, label in ((GDC, "gdc"), (NEW, "informed"), (PLANNER, "reported_planner")):
            for sc in SCORINGS:
                row[f"{label}_{sc}"] = " ".join(v[(s, sc)][x] for x in seeds)
        for sc in SCORINGS:
            row[f"correct_gdc_minus_informed_{sc}"] = (sum(v[(GDC, sc)][x] == "correct" for x in seeds)
                                                       - sum(v[(NEW, sc)][x] == "correct" for x in seeds))
        row["informed_errors"] = " | ".join(sorted({newinfo[(d, q, x)]["error"] for x in seeds} - {""}))
        perq.append(row)

    # headline
    head = []

    def put(key, value, num="", den="", lo="", hi="", p="", disc="", note=""):
        head.append({"key": key, "value": value, "numerator": num, "denominator": den, "ci_lo": lo, "ci_hi": hi,
                     "p_perm": p, "n_discordant_q": disc, "note": note})
    rate = {(x["set"], x["metric"], x["system"]): x for x in rates}
    for (grp, sc), name in SETS.items():
        for metric in ("accuracy", "silent_wrong"):
            for s in (GDC, NEW):
                x = rate[(name, metric, s)]
                put(f"{grp}_{metric}_pct_{s}_{sc}", x["rate_pct"], x["k"], x["n_cells"], x["ci_lo_pct"], x["ci_hi_pct"],
                    note=f"{x['n_questions']} questions, {sc}")
            c = by[(name, metric)]
            put(f"{grp}_diff_{metric}_gdc_minus_{NEW}_{sc}", c["diff_pp"], "", c["n_questions"], c["ci_lo_pp"],
                c["ci_hi_pp"], c["p_perm"], c["n_discordant_q"],
                f"pp; {c['verdict']}; GDC higher on {c['q_gdc_higher']} questions, lower on {c['q_alt_higher']}")
    for sc in SCORINGS:
        put(f"outcome_{sc}", outcome[sc], note="pre-registered on primary accuracy: A no resolved difference, "
            "B GDC resolved as more accurate, C the informed planner resolved as more accurate"
            + ("" if sc == "tolerant" else "; s48, beside the decision"))
    put("heldout_replay_differs_from_run", len(asrun_bad), len(asrun_bad), sum(c["asrun_equal"] is not None for c in new),
        note="held-out planner_gdcinfo cells whose replay differs from the cell as run")
    put("questions_differing", len(perq), len(perq), len(cls), note="questions where planner_gdcinfo and GDC differ "
        "under either rule")

    # write
    out.mkdir(parents=True, exist_ok=True)
    Q.write_csv(out / "rates.csv", rates)
    Q.write_csv(out / "per_database.csv", perdb)
    _write_rows(out / "per_question.csv", perq, list(perq[0]) if perq else ["set", "database", "qid"])
    Q.write_csv(out / "headline.csv", head)
    append_comparisons(comparisons, comps)
    text = summary(comps, rates, perdb, perq, outcome, asrun_bad, Q.fmt_p)
    (out / "summary.txt").write_text(text)
    echo(f"planner info: OUTCOME {outcome['tolerant']} (s48t; s48 {outcome['strict']}); {len(perq)} questions differ; "
         f"{len(comps)} comparisons appended to {comparisons.name}; outputs "
         f"{out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}/")
    return {"outcome": outcome, "comparisons": comps, "rates": rates, "per_database": perdb, "per_question": perq,
            "headline": head, "asrun_bad": asrun_bad}


def _write_rows(path: Path, rows: list[dict], fields: list[str]):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def append_comparisons(path: Path, comps: list[dict]):
    """Keep every line of comparisons.csv byte for byte except earlier planner_info_* rows, and
    append these comparisons in question_clustered_stats.write_csv's format."""
    raw = path.read_bytes().decode("utf-8")
    lines = raw.splitlines(keepends=True)
    header = next(csv.reader([lines[0]]))
    if list(comps[0]) != header:
        raise SystemExit(f"comparison fields {list(comps[0])} != {path.name} header")
    kept = [lines[0]] + [l for l in lines[1:] if not l.startswith(SET_PREFIX)]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header)
    for r in comps:
        w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()})
    path.write_bytes(("".join(kept) + buf.getvalue()).encode("utf-8"))


def summary(comps, rates, perdb, perq, outcome, asrun_bad, fmt_p) -> str:
    L = ["Equal-information run (review/planner_info_prereg.md): the planner under its reported prompt with the",
         "shared briefing replaced by GDC's domain information (planner_gdcinfo), replayed on the fixed engine.", "",
         f"Pre-registered outcome (primary accuracy, employee + held-out, s48t): OUTCOME {outcome['tolerant']}; "
         f"under s48: OUTCOME {outcome['strict']}.", ""]
    for c in comps:
        L.append(f"{c['set']:<32} {c['metric']:<12} GDC {c['gdc_pct']:5.1f} - informed planner {c['alt_pct']:5.1f} = "
                 f"{c['diff_pp']:+6.1f} [{c['ci_lo_pp']:+6.1f}, {c['ci_hi_pp']:+6.1f}]  {c['n_questions']} q, discordant "
                 f"{c['n_discordant_q']} ({c['q_gdc_higher']} GDC higher, {c['q_alt_higher']} lower)  p_perm "
                 f"{fmt_p(c['p_perm'])}  {c['verdict']}")
    L.append("")
    for x in rates:
        L.append(f"{x['set']:<32} {x['metric']:<12} {x['system']:<16} {x['rate_pct']:5.1f}% "
                 f"[{x['ci_lo_pct']:.1f}, {x['ci_hi_pct']:.1f}] ({x['k']}/{x['n_cells']})")
    L += ["", "Per database (accuracy / silent-error, cells): informed planner | reported planner | GDC"]
    for sc in SCORINGS:
        L.append(f"  {sc}:")
        rows = defaultdict(dict)
        for r in perdb:
            if r["scoring"] == sc:
                rows[r["scope"]][r["system"]] = r
        for scope, by in rows.items():
            L.append(f"    {scope:<26} " + " | ".join(
                f"{by[s]['correct']}/{by[s]['n_cells']} {float(by[s]['accuracy_pct']):5.1f}% / "
                f"{by[s]['silent_wrong']} {float(by[s]['silent_wrong_pct']):4.1f}%" for s in (NEW, PLANNER, GDC)))
    L += ["", f"Questions where the informed planner and GDC differ ({len(perq)}); classes per seed, s48t then s48:"]
    for r in perq:
        L.append(f"  {r['database']}/{r['qid']} ({r['tier']}): GDC [{r['gdc_tolerant']}] [{r['gdc_strict']}]; informed "
                 f"[{r['informed_tolerant']}] [{r['informed_strict']}]; reported planner [{r['reported_planner_tolerant']}]"
                 + (f"; informed errors: {r['informed_errors']}" if r["informed_errors"] else ""))
        L.append(f"      {r['question']}")
    L += ["", f"Held-out replay of the new cells differs from the cell as run: {len(asrun_bad)}"]
    for c in asrun_bad:
        L.append(f"  {c['dom']}/{c['qid']} s{c['seed']}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default=str(RESULTS))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--comparisons", default=str(COMPARISONS))
    a = ap.parse_args(argv)
    path = Path(a.results)
    if not path.exists():
        print(f"planner info: {path.name} not present, nothing written")
        return 0
    os.environ["PARITY_TEMP0"] = "1"
    rows, _ = check_input(path)
    G, cells = builder_cells()
    compute(G, cells, rows, Path(a.out), Path(a.comparisons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
