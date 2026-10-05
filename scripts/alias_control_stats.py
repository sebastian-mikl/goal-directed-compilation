#!/usr/bin/env python
"""Statistics of the post hoc alias control (review/alias_control_prereg.md): GDC on the 81
employee in-scope questions under its recorded s31 prompt with only the "active employees" alias
removed (system network_noalias, results/results_temp0_employee_gdc_noalias.jsonl, run by
scripts/run_alias_control.py).

If the results file is absent the script prints one line, exits 0 and writes nothing, so a
checkout without the run regenerates exactly what it did before. A file that is present must
be the whole run: its provenance records the control prompt, temperature 0, the model of record
and `complete`, and its rows are exactly the 243 planned (qid, seed) cells, each a completed
cell; anything else stops the script (exit 1).

Cells. The network_noalias cells are scored as every GDC cell is: replayed through the current
engine, strict s48 and tie-tolerant s48t (scripts/score_alias_control.py, which reproduces the
reported GDC employee cells' classes exactly). GDC with the alias and the four alternatives are
the cells of question_clustered_stats.py's employee_in_contract sets, taken from the builder in
process and checked against reports/paper_numbers.csv (question_clustered_stats.verify). The
reported GDC cells are also replayed through the scorer and must agree with them cell by cell.

Outputs, in reports/stats/alias_control/ (new files only):
  active_questions.csv  (a) the eight T4 questions worded with "active": per seed, the status set
                        of the Goal's filter and the class, with and without the alias; FAILED
                        (silent_wrong on at least 2 of 3 seeds), strict and tolerant
  regression.csv        (d) every cell of the other 73 questions whose class or emitted Goal /
                        OrderSpec differs from the reported run
  rates.csv             GDC without the alias: silent-error and accuracy over the 81 questions,
                        strict and tolerant, with the question-clustered interval
  headline.csv          (e) the numbers the paper takes, one row per key
  summary.txt           all of the above in words, with the pre-registered outcome
and rows appended to reports/stats/comparisons.csv under new set names only (any earlier
alias_control_* rows are replaced; every other byte of the file is kept):
  alias_control_employee_in_contract                (c) GDC without the alias minus each
      alternative as run, strict, silent_wrong and accuracy, Holm over the four alternatives; the
      alternatives' cells and the resamples (seed 20261001 + crc32) are employee_in_contract's
  alias_control_noalias_minus_gdc_in_contract       (b) GDC without the alias minus GDC with it
  alias_control_noalias_minus_gdc_in_contract_tolerant  (alt = network, the reported GDC cells),
  alias_control_noalias_minus_gdc_active8           over the 81 questions (on the resamples of
  alias_control_noalias_minus_gdc_active8_tolerant  employee_in_contract[_tolerant]) and over the
      eight (resamples seeded by the set's own name). One comparison per set and metric, so
      p_perm_holm equals p_perm. In these sets gdc_pct is GDC without the alias.
Every comparison is question_clustered_stats.analyse(): paired difference, 95% percentile
interval from a question-level cluster bootstrap (10,000 resamples), discordant questions,
exact sign test and exact sign-flip permutation p.

Pre-registered rule (review/alias_control_prereg.md): a question FAILS if GDC is silent_wrong
on at least 2 of its 3 seeds; OUTCOME A at least 6 of 8 FAILED, B at most 2, C 3 to 5. The
paired comparison with the alias-on cells is resolved only if at least 6 of the eight questions
are discordant, all one way. Regression check: if more than 6 of the 219 cells of the other 73
questions change class (strict), the control is not clean: the script prints a STOP line, writes
every output and exits 0.

    python scripts/alias_control_stats.py        # run_all.sh step 6, after question_clustered_stats.py
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

RESULTS = ROOT / "results" / "results_temp0_employee_gdc_noalias.jsonl"
OUT = ROOT / "reports" / "stats" / "alias_control"
COMPARISONS = ROOT / "reports" / "stats" / "comparisons.csv"
SET_PREFIX = "alias_control_"
SET_C = SET_PREFIX + "employee_in_contract"
SET_B = {("in_contract", "strict"): SET_PREFIX + "noalias_minus_gdc_in_contract",
         ("in_contract", "tolerant"): SET_PREFIX + "noalias_minus_gdc_in_contract_tolerant",
         ("active8", "strict"): SET_PREFIX + "noalias_minus_gdc_active8",
         ("active8", "tolerant"): SET_PREFIX + "noalias_minus_gdc_active8_tolerant"}
BASE_SET = {"strict": "employee_in_contract", "tolerant": "employee_in_contract_tolerant"}
NOALIAS = "network_noalias"
SEEDS = (0, 1, 2)
PREREG_DISCORDANT = 6         # the paired comparison over the eight resolves only at >= 6 discordant, all one way


def status_text(sf) -> str:
    """The Goal's filter on status, as emitted: `in Active|On Leave|Sabbatical`; `none` when the
    Goal has no status conjunct; `no Goal` for an emission-time refusal."""
    if sf is None:
        return "no Goal"
    if not sf:
        return "none"
    return " AND ".join(f"{op} {'|'.join(map(str, v)) if isinstance(v, (list, tuple)) else v}" for op, v in sf)


def emission_key(row: dict) -> tuple:
    """(Goal fingerprint, canonical OrderSpec) of a row, or ('no Goal', error label)."""
    from scripts.replay_goals import rebuild_goal
    art = row.get("artifact") if isinstance(row.get("artifact"), dict) else {}
    if not art.get("goal"):
        return ("no Goal", str(row.get("error") or "").split(":")[0])
    return (rebuild_goal(art["goal"]).fingerprint, json.dumps(art.get("order") or {}, sort_keys=True))


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


def check_input(path: Path, gold: dict) -> list[dict]:
    """The rows of a complete run, or SystemExit naming what is wrong."""
    from scripts.parity_overlay import incomplete_row
    from scripts.run_alias_control import CONTROL_SHA256, MODEL
    from scripts.score_alias_control import new_rows
    prov_p = path.with_name(path.name.replace(".jsonl", ".provenance.json"))
    if not prov_p.exists():
        raise SystemExit(f"{path.name} has no {prov_p.name}")
    prov = json.loads(prov_p.read_text())
    want = {"system": NOALIAS, "temperature": 0, "model": MODEL, "complete": True}
    bad = {k: prov.get(k) for k, v in want.items() if prov.get(k) != v}
    if (prov.get("prompt_sha256") or {}).get("sent_control") != CONTROL_SHA256:
        bad["prompt_sha256.sent_control"] = (prov.get("prompt_sha256") or {}).get("sent_control")
    if bad:
        raise SystemExit(f"{prov_p.name} does not record the complete control run: {bad}")
    rows = new_rows(ROOT, path)
    planned = {(q, s) for q, g in gold.items() if g["tier"] != "out_of_range" for s in SEEDS}
    held = {(r["qid"], int(r["seed"])) for r in rows}
    if held != planned or sorted(map(tuple, prov.get("plan_keys") or [])) != sorted((NOALIAS, q, s) for q, s in planned):
        raise SystemExit(f"{path.name}: {len(held & planned)} of {len(planned)} planned cells "
                         f"({len(held - planned)} unplanned); the statistics need the whole run")
    inc = sorted((r["qid"], r["seed"], why) for r in rows if (why := incomplete_row(r)))
    if inc:
        raise SystemExit(f"{path.name}: rows that are not completed cells: {inc[:3]}")
    return rows


def builder_cells():
    """question_clustered_stats' verified cells (the builder run in process, as step 6 runs it)."""
    import scripts.question_clustered_stats as Q
    G = Q.run_builder()
    cells, spec = Q.derive(G)
    Q.verify(G, cells, spec)
    return G, cells


def compute(G, cells: dict, rows: list[dict], out: Path, comparisons: Path, echo=print) -> dict:
    import scripts.question_clustered_stats as Q
    import scripts.score_alias_control as S
    mods = S.load_modules(ROOT)
    pi = mods[0]
    gold = pi.GOLD["employee"]
    corpus = {q["id"]: q for q in json.loads((ROOT / "evaluation/domains/employee/eval_corpus_employee.json")
                                            .read_text())["queries"]}
    new = {(c["qid"], c["seed"]): c for c in (S.score_cell(mods, r, gold[r["qid"]], reported=False) for r in rows)}
    rep_rows = S.reported_rows(pi)
    rep = {(c["qid"], c["seed"]): c for c in (S.score_cell(mods, r, gold[r["qid"]], reported=True) for r in rep_rows)}
    new_em = {(r["qid"], int(r["seed"])): emission_key(r) for r in rows}
    rep_em = {(r["qid"], int(r["seed"])): emission_key(r) for r in rep_rows}
    base = {sc: [c for c in cells[BASE_SET[sc]]] for sc in BASE_SET}
    gdc = {sc: {(c["qid"], c["seed"]): c for c in base[sc] if c["system"] == Q.GDC} for sc in BASE_SET}
    if set(new) != set(rep) or set(rep) != set(gdc["strict"]):
        raise SystemExit("the control cells, the scorer's reported cells and the builder's GDC cells differ in keys")
    for k, c in gdc["strict"].items():                      # the scorer reproduces the builder, cell by cell
        if (c["cls"], gdc["tolerant"][k]["cls"]) != (rep[k]["strict_raw"], rep[k]["tolerant"]):
            raise SystemExit(f"{k}: the scorer's reported class differs from the builder's")
    active = list(S.ACTIVE_QIDS)
    if sorted(q for q in {k[0] for k in new} if "activ" in corpus[q]["question"].lower()) != sorted(active):
        raise SystemExit("the in-scope questions worded with 'activ' are not the pre-registered eight")

    def qcell(c: dict, cls: str, system: str) -> dict:
        return {"system": system, "dom": "employee", "qid": c["qid"], "seed": c["seed"], "cls": cls,
                "res": c["res"], "temp0": True, "stratum": "employee", "src": ""}

    noalias = {"strict": [qcell(c, c["strict_raw"], Q.GDC) for c in new.values()],
               "tolerant": [qcell(c, c["tolerant"], Q.GDC) for c in new.values()]}
    comps, rates = [], []
    # (c) GDC without the alias minus each alternative as run, strict, on employee_in_contract's resamples
    mix = [c for c in base["strict"] if c["system"] != Q.GDC] + noalias["strict"]
    r, cc, _ = Q.analyse(BASE_SET["strict"], mix, "answer")
    comps += [dict(x, set=SET_C) for x in cc]
    rates += [dict(x, set=SET_C, system=NOALIAS) for x in r if x["system"] == Q.GDC]
    # (b) GDC without the alias minus GDC with it, paired, over the 81 and over the eight
    for (scope, sc), name in SET_B.items():
        a = [dict(c, system=NOALIAS) for c in noalias[sc]]
        b = list(gdc[sc].values())
        if scope == "active8":
            a = [c for c in a if c["qid"] in active]
            b = [c for c in b if c["qid"] in active]
        with _pair(Q, NOALIAS, Q.GDC):
            r, cc, _ = Q.analyse(BASE_SET[sc] if scope == "in_contract" else name, a + b, "answer")
        comps += [dict(x, set=name) for x in cc]
        if scope == "in_contract" and sc == "tolerant":
            rates += [dict(x, set=SET_PREFIX + "employee_in_contract_tolerant") for x in r if x["system"] == NOALIAS]

    # (a) the eight questions
    act_rows, failed = [], {"strict": [], "tolerant": []}
    for q in active:
        row = {"qid": q, "question": corpus[q]["question"]}
        for s in SEEDS:
            row[f"s{s}_status_with_alias"] = status_text(rep[(q, s)]["status_filter"])
            row[f"s{s}_class_with_alias"] = rep[(q, s)]["strict"]
            row[f"s{s}_status_noalias"] = status_text(new[(q, s)]["status_filter"])
            row[f"s{s}_class_noalias"] = new[(q, s)]["strict"]
        for sc in ("strict", "tolerant"):
            n_sw = sum(new[(q, s)][sc] == "silent_wrong" for s in SEEDS)
            row[f"silent_noalias_{sc}"] = n_sw
            row[f"silent_with_alias_{sc}"] = sum(rep[(q, s)][sc] == "silent_wrong" for s in SEEDS)
            row[f"failed_{sc}"] = n_sw >= 2
            if n_sw >= 2:
                failed[sc].append(q)
        row["tolerant_classes_noalias"] = " ".join(new[(q, s)]["tolerant"] for s in SEEDS)
        act_rows.append(row)
    outcome = {sc: ("A" if len(f) >= 6 else "B" if len(f) <= 2 else "C") for sc, f in failed.items()}

    # (d) the regression check on the other 73 questions
    other = sorted(k for k in new if k[0] not in active)
    reg_rows = []
    moved = {"strict": [], "tolerant": []}
    goal_moved = [k for k in other if new_em[k] != rep_em[k]]
    for k in other:
        for sc in moved:
            if new[k][sc] != rep[k][sc]:
                moved[sc].append(k)
        if k in goal_moved or new[k]["strict"] != rep[k]["strict"] or new[k]["tolerant"] != rep[k]["tolerant"]:
            reg_rows.append({"qid": k[0], "seed": k[1], "tier": new[k]["tier"],
                             "strict_with_alias": rep[k]["strict"], "strict_noalias": new[k]["strict"],
                             "tolerant_with_alias": rep[k]["tolerant"], "tolerant_noalias": new[k]["tolerant"],
                             "class_changed_strict": new[k]["strict"] != rep[k]["strict"],
                             "emission_changed": k in goal_moved,
                             "status_with_alias": status_text(rep[k]["status_filter"]),
                             "status_noalias": status_text(new[k]["status_filter"]),
                             "replay_error_noalias": new[k]["replay_error"] or ""})
    clean = len(moved["strict"]) <= S.REGRESSION_LIMIT

    # (e) headline
    by = {(c["set"], c["metric"], c["alt"]): c for c in comps}
    rate = {(x["set"], x["metric"]): x for x in rates}
    head = []

    def put(key, value, num="", den="", lo="", hi="", p="", ph="", disc="", note=""):
        head.append({"key": key, "value": value, "numerator": num, "denominator": den, "ci_lo": lo, "ci_hi": hi,
                     "p_perm": p, "p_perm_holm": ph, "n_discordant_q": disc, "note": note})
    for sc, st in (("strict", SET_C), ("tolerant", SET_PREFIX + "employee_in_contract_tolerant")):
        for metric in ("silent_wrong", "accuracy"):
            x = rate[(st, metric)]
            put(f"noalias_in_scope_{metric}_pct_{sc}", x["rate_pct"], x["k"], x["n_cells"], x["ci_lo_pct"],
                x["ci_hi_pct"], note=f"81 questions, {sc}; interval on {BASE_SET[sc]}'s resamples")
    for sc in ("strict", "tolerant"):
        k = sum(new[(q, s)][sc] == "silent_wrong" for q in active for s in SEEDS)
        put(f"noalias_active24_silent_count_{sc}", k, k, 3 * len(active), note="the eight 'active' questions x 3 seeds")
        put(f"noalias_active8_failed_{sc}", len(failed[sc]), len(failed[sc]), len(active),
            note="FAILED: " + (" ".join(failed[sc]) or "none"))
        put(f"outcome_{sc}", outcome[sc], note="pre-registered: A >= 6 of 8 FAILED, B <= 2, C 3 to 5")
    for metric in ("silent_wrong", "accuracy"):
        for alt in Q.ALTS:
            c = by[(SET_C, metric, alt)]
            put(f"diff_{metric}_noalias_minus_{alt}_strict", c["diff_pp"], "", c["n_questions"], c["ci_lo_pp"],
                c["ci_hi_pp"], c["p_perm"], c["p_perm_holm"], c["n_discordant_q"],
                f"pp; {c['verdict']}; Holm over the four alternatives")
    for (scope, sc), name in SET_B.items():
        for metric in ("silent_wrong", "accuracy"):
            c = by[(name, metric, Q.GDC)]
            resolved = (scope == "active8" and c["n_discordant_q"] >= PREREG_DISCORDANT
                        and min(c["q_gdc_higher"], c["q_alt_higher"]) == 0)
            put(f"diff_{metric}_noalias_minus_gdc_{scope}_{sc}", c["diff_pp"], "", c["n_questions"], c["ci_lo_pp"],
                c["ci_hi_pp"], c["p_perm"], c["p_perm_holm"], c["n_discordant_q"],
                f"pp; noalias higher on {c['q_gdc_higher']} questions, lower on {c['q_alt_higher']}"
                + (f"; pre-registered rule (>= {PREREG_DISCORDANT} discordant, all one way): "
                   f"{'resolved' if resolved else 'not resolved'}" if scope == "active8" else ""))
    put("regression_cells_changed_strict", len(moved["strict"]), len(moved["strict"]), len(other),
        note=f"other {len({k[0] for k in other})} questions; limit {S.REGRESSION_LIMIT}")
    put("regression_cells_changed_tolerant", len(moved["tolerant"]), len(moved["tolerant"]), len(other))
    put("regression_cells_emission_changed", len(goal_moved), len(goal_moved), len(other),
        note="Goal fingerprint or OrderSpec differs from the reported emission")
    put("regression_clean", int(clean), note="1 = at most 6 of 219 cells change class (strict)")

    # write
    out.mkdir(parents=True, exist_ok=True)
    Q.write_csv(out / "active_questions.csv", act_rows)
    _write_rows(out / "regression.csv", reg_rows, ["qid", "seed", "tier", "strict_with_alias", "strict_noalias",
                "tolerant_with_alias", "tolerant_noalias", "class_changed_strict", "emission_changed",
                "status_with_alias", "status_noalias", "replay_error_noalias"])
    Q.write_csv(out / "rates.csv", rates)
    Q.write_csv(out / "headline.csv", head)
    append_comparisons(comparisons, comps)
    stop = None if clean else (f"STOP: the alias control is NOT CLEAN: {len(moved['strict'])} of {len(other)} cells "
                               f"of the other {len({k[0] for k in other})} questions change class (limit "
                               f"{S.REGRESSION_LIMIT}); review/alias_control_prereg.md: the analysis stops and is reported")
    text = summary(act_rows, failed, outcome, moved, goal_moved, other, comps, rates, stop)
    (out / "summary.txt").write_text(text)
    if stop:
        echo("=" * 100 + "\n" + stop + "\n" + "=" * 100)
    echo(f"alias control: OUTCOME {outcome['strict']} (FAILED {len(failed['strict'])} of 8, strict); regression "
         f"check {len(moved['strict'])} of {len(other)} cells ({'clean' if clean else 'NOT CLEAN'}); "
         f"{len(comps)} comparisons appended to {comparisons.name}; outputs {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}/")
    return {"outcome": outcome, "failed": failed, "moved": moved, "goal_moved": goal_moved, "clean": clean,
            "comparisons": comps, "headline": head}


def _write_rows(path: Path, rows: list[dict], fields: list[str]):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def append_comparisons(path: Path, comps: list[dict]):
    """Keep every line of comparisons.csv byte for byte except earlier alias_control_* rows, and
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


def summary(act_rows, failed, outcome, moved, goal_moved, other, comps, rates, stop) -> str:
    L = ["Alias control (review/alias_control_prereg.md): GDC on the employee in-scope questions, recorded s31",
         "prompt minus the 'active employees' alias (network_noalias), replayed on the current engine.", ""]
    if stop:
        L += [stop, ""]
    L.append("Primary: the eight T4 questions worded with 'active' (FAILED = silent_wrong on >= 2 of 3 seeds)")
    for r in act_rows:
        L.append(f"  {r['qid']}  " + "  ".join(f"s{s}: {r[f's{s}_status_noalias']} -> {r[f's{s}_class_noalias']}"
                                             f" (with alias {r[f's{s}_class_with_alias']})" for s in SEEDS)
                 + f"  {'FAILED' if r['failed_strict'] else 'not failed'}")
    for sc in ("strict", "tolerant"):
        L.append(f"  {sc}: FAILED {len(failed[sc])} of 8 ({' '.join(failed[sc]) or 'none'}) -> OUTCOME {outcome[sc]}")
    L.append("")
    for x in rates:
        L.append(f"GDC without the alias, {x['set']}, {x['metric']}: {x['rate_pct']:.1f}% "
                 f"[{x['ci_lo_pct']:.1f}, {x['ci_hi_pct']:.1f}] ({x['k']}/{x['n_cells']})")
    L.append("")
    for c in comps:
        L.append(f"{c['set']:<52} {c['metric']:<12} vs {c['alt']:<11} {c['gdc_pct']:5.1f} - {c['alt_pct']:5.1f} = "
                 f"{c['diff_pp']:+6.1f} [{c['ci_lo_pp']:+6.1f}, {c['ci_hi_pp']:+6.1f}]  discordant q "
                 f"{c['n_discordant_q']} ({c['q_gdc_higher']} up, {c['q_alt_higher']} down)  p_perm "
                 f"{Q_fmt(c['p_perm'])} (Holm {Q_fmt(c['p_perm_holm'])})  {c['verdict']}")
    L.append("")
    L.append(f"Regression check, other {len({k[0] for k in other})} questions ({len(other)} cells): "
             f"{len(moved['strict'])} change class strict, {len(moved['tolerant'])} tolerant; "
             f"{len(goal_moved)} emit another Goal or OrderSpec")
    for k in moved["strict"]:
        L.append(f"  {k[0]} s{k[1]}")
    return "\n".join(L) + "\n"


def Q_fmt(p):
    from scripts.question_clustered_stats import fmt_p
    return fmt_p(p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default=str(RESULTS))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--comparisons", default=str(COMPARISONS))
    a = ap.parse_args(argv)
    path = Path(a.results)
    if not path.exists():
        print(f"alias control: {path.name} not present, nothing written")
        return 0
    os.environ["PARITY_TEMP0"] = "1"
    G, cells = builder_cells()
    rows = check_input(path, G["pi"].GOLD["employee"])
    compute(G, cells, rows, Path(a.out), Path(a.comparisons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
