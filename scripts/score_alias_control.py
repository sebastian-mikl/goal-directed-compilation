#!/usr/bin/env python
"""Score the alias-control cells (system network_noalias) exactly as the reported GDC employee
cells are scored, and check the scorer on the reported cells themselves.

Scoring, as scripts/build_paper_numbers.py scores GDC on the employee domain (Decisions 2 and 7,
D8, D9): each cell's recorded Goal + OrderSpec is REPLAYED through the current engine
(paper_inputs._network_replay -> replay_goals.run_deterministic, the post-emission half of
NetworkAdapter.run); a cell with no Goal (an emission-time decline or ParseError) keeps its
recorded status and result. The replayed result is scored
  strict    comparator s48 (evidence/eval_run/comparator.score)
  tolerant  s48t (scripts/score_tie_tolerant.score_tt_syn("employee", ...), D9)
and D8 splits a `correct` / `refused_should_answer` refusal whose decline channel is `failed`
(a reply that was not valid JSON, a run that completed with no result) into `failed`.
The channel is paper_inputs.decline_channel for the reported cells. For network_noalias cells it
is paper_inputs.rule_channel on the row read as a GDC row (the rule branches on the system id);
no hand reading (reports/decline_readings.json) exists for any GDC cell, and none is applied to a
new emission.

    python scripts/score_alias_control.py --control   # the 243 reported s31 GDC cells: must give the
                                                      # reported classes (paper_numbers.csv Table1/
                                                      # Table2, reports/stats/per_question.csv)
    python scripts/score_alias_control.py             # results/results_temp0_employee_gdc_noalias.jsonl

Writes <out>/cells_<label>.jsonl (one scored cell per line: the fields question_clustered_stats
uses, plus the Goal's status filter) and <out>/summary_<label>.txt. Offline; no API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve()
DEFAULT_ROOT = HERE.parents[1]          # the repository this script sits in (scripts/..)
DOM = "employee"
SYSTEM_ID = "network_noalias"
NEW_FILE = "results_temp0_employee_gdc_noalias.jsonl"
CLASSES = ("correct", "silent_wrong", "refused_should_answer", "failed", "crash", "timeout")
METRIC = {"correct": "accuracy"}                      # paper_numbers.csv metric name per class
SEEDS = (0, 1, 2)


def resolve_root(arg):
    if arg:
        return Path(arg).resolve()
    if HERE.parent.name == "scripts" and (HERE.parents[1] / "gdc" / "contract.py").exists():
        return HERE.parents[1]
    return DEFAULT_ROOT


def load_modules(root: Path):
    os.environ["PARITY_TEMP0"] = "1"              # the builder's reading of results_employee.jsonl
    sys.path.insert(0, str(root))
    from scripts import paper_inputs as pi
    from evidence.eval_run.comparator import score, COMPARATOR_VERSION
    from scripts.score_tie_tolerant import score_tt_syn, SCORER_VERSION
    from scripts.question_clustered_stats import canon
    assert COMPARATOR_VERSION == "s48" and SCORER_VERSION == "s48t", (COMPARATOR_VERSION, SCORER_VERSION)
    return pi, score, score_tt_syn, canon


def status_filter(row: dict):
    """The Goal's conjuncts on `status`, as emitted: [[op, val], ...], or None with no Goal."""
    goal = (row.get("artifact") or {}).get("goal") if isinstance(row.get("artifact"), dict) else None
    if not goal:
        return None
    return [[op, val] for col, op, val in goal["where"] if col == "status"]


def score_cell(mods, row: dict, gold_rec: dict, *, reported: bool) -> dict:
    pi, score, score_tt_syn, canon = mods
    res, status, err = pi._network_replay(DOM, row)
    strict = score(res, gold_rec, status=status)[0]
    tol = score_tt_syn(DOM, res, gold_rec, row["qid"], status=status)[0]
    as_gdc = dict(row, system="network")
    if reported:
        ch = pi.decline_channel(DOM, as_gdc)
    else:
        if pi.reading_key(DOM, as_gdc) in pi.DECLINE_READINGS:
            raise SystemExit(f"a hand reading exists for {pi.reading_key(DOM, as_gdc)}; it reads another emission")
        ch = pi.rule_channel(DOM, as_gdc)

    def d8(v):
        return "failed" if v in ("correct", "refused_should_answer") and ch is not None and ch[0] == "failed" else v
    art = row.get("artifact") if isinstance(row.get("artifact"), dict) else {}
    return {"system": row["system"], "dom": DOM, "qid": row["qid"], "seed": int(row["seed"]),
            "tier": gold_rec["tier"], "strict": d8(strict), "tolerant": d8(tol),
            "strict_raw": strict, "channel": ch[0] if ch else "", "channel_detail": ch[1] if ch else "",
            "replay_status": status, "replay_error": err, "recorded_outcome_class": row.get("outcome_class"),
            "status_filter": status_filter(row),
            "goal_sha12": hashlib.sha256(json.dumps(art.get("goal"), sort_keys=True).encode()).hexdigest()[:12]
            if art.get("goal") else "",
            "res": f"{status}|{canon(res)}", "temp0": True, "stratum": DOM,
            "src": row.get("overlay_source") or ""}


def reported_rows(pi) -> list[dict]:
    rows = [r for r in pi.employee() if r["system"] == "network" and r["tier"] != "out_of_range"]
    if any(r.get("overlay_source") for r in rows):
        raise SystemExit("an overlay replaced a reported GDC employee cell")
    return pi.scored(rows, pi.GOLD[DOM])


def new_rows(root: Path, path: Path | None) -> list[dict]:
    p = path or root / "results" / NEW_FILE
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    if {r.get("system") for r in rows} != {SYSTEM_ID}:
        raise SystemExit(f"{p.name}: systems {sorted({r.get('system') for r in rows})}, expected [{SYSTEM_ID}]")
    keys = [(r["qid"], int(r["seed"])) for r in rows]
    if len(keys) != len(set(keys)):
        raise SystemExit(f"{p.name}: duplicate (qid, seed) cells")
    return rows


def check_against_reported(root: Path, cells: list[dict]) -> list[str]:
    """The control: per tier and in_contract_answer, both scorings, every class, equal to
    reports/paper_numbers.csv; per question, equal to reports/stats/per_question.csv."""
    import csv
    bad, log = [], []
    with open(root / "reports" / "paper_numbers.csv", newline="", encoding="utf-8") as fh:
        csvrows = {(r["fig_table"], r["tier_group"], r["scoring"], r["metric"]): (int(r["numerator"]), int(r["denominator"]))
                   for r in csv.DictReader(fh) if r["domain"] == DOM and r["system"] == "network"
                   and r["fig_table"] in ("Table1", "Table2")}
    groups = defaultdict(list)
    for c in cells:
        groups[("Table1", c["tier"])].append(c)
        groups[("Table2", "in_contract_answer")].append(c)
    for (ft, tg), cs in sorted(groups.items()):
        for scoring in ("strict", "tolerant"):
            cnt = Counter(c[scoring] for c in cs)
            for cls in CLASSES:
                key = (ft, tg, scoring, METRIC.get(cls, cls))
                want = csvrows.get(key)
                got = (cnt.get(cls, 0), len(cs))
                if want != got:
                    bad.append(f"{key}: scorer {got} != paper_numbers.csv {want}")
            log.append(f"  {ft:<6} {tg:<28} {scoring:<8} n={len(cs):<3} " +
                       " ".join(f"{k}={v}" for k, v in sorted(cnt.items())) + "  = paper_numbers.csv")
    with open(root / "reports" / "stats" / "per_question.csv", newline="", encoding="utf-8") as fh:
        pq = {(r["set"], r["qid"]): r for r in csv.DictReader(fh) if r["system"] == "network"}
    byq = defaultdict(list)
    for c in cells:
        byq[c["qid"]].append(c)
    nq = 0
    for qid, cs in sorted(byq.items()):
        for set_, scoring in (("employee_in_contract", "strict"), ("employee_in_contract_tolerant", "tolerant")):
            r = pq.get((set_, qid))
            if r is None:
                bad.append(f"per_question.csv has no {set_}/network/{qid}")
                continue
            cnt = Counter(c[scoring] for c in cs)
            for cls in ("correct", "silent_wrong", "refused_should_answer", "crash", "timeout", "failed"):
                if int(r[cls]) != cnt.get(cls, 0):
                    bad.append(f"{set_}/{qid}/{cls}: scorer {cnt.get(cls, 0)} != per_question.csv {r[cls]}")
            if r["result_identical"] != str(len({c['res'] for c in cs}) == 1):
                bad.append(f"{set_}/{qid}: result_identical differs")
            nq += 1
    log.append(f"  per_question.csv: {nq} (set, question) rows compared, both scorings, every class and "
               f"result_identical")
    return bad, log


def summarise(cells: list[dict], label: str) -> str:
    L = [f"{label}: {len(cells)} cells, {len({c['qid'] for c in cells})} questions"]
    for scoring in ("strict", "tolerant"):
        L.append(f"  {scoring}: " + " ".join(f"{k}={v}" for k, v in sorted(Counter(c[scoring] for c in cells).items())))
        for t in sorted({c["tier"] for c in cells}):
            cs = [c for c in cells if c["tier"] == t]
            L.append(f"    {t:<28} n={len(cs):<3} " + " ".join(f"{k}={v}" for k, v in sorted(Counter(c[scoring] for c in cs).items())))
    flips = [c for c in cells if c["recorded_outcome_class"] != c["strict_raw"]]
    L.append(f"  replay strict != outcome_class recorded at emission time: {len(flips)} "
             + ", ".join(f"{c['qid']}/s{c['seed']} {c['recorded_outcome_class']}->{c['strict_raw']}" for c in flips[:12]))
    act = sorted({c["qid"] for c in cells if c["tier"] == "T4_business_rule_multi_op"})
    L.append("  T4 status filters per question (seed: filter -> strict):")
    for q in act:
        for c in sorted((c for c in cells if c["qid"] == q), key=lambda c: c["seed"]):
            L.append(f"    {q} s{c['seed']}: {json.dumps(c['status_filter'])} -> {c['strict']}")
    return "\n".join(L) + "\n"


ACTIVE_QIDS = ("t4_01", "t4_02", "t4_05", "t4_07", "t4_08", "t4_10", "t4_12", "t4_14")   # the alias arm's eight
REGRESSION_LIMIT = 6        # PREREG_DRAFT.md: more than 6 of the 219 other cells differing = control not clean


def preregistered(cells: list[dict], reported: list[dict], corpus: dict) -> str:
    """PREREG_DRAFT.md's primary measure and regression check, strict (primary) and tolerant."""
    import re
    active = sorted(q for q, rec in corpus.items() if re.search(r"activ", rec["question"], re.I)
                    and q in {c["qid"] for c in reported})
    if tuple(active) != ACTIVE_QIDS:
        raise SystemExit(f"in-scope questions worded with 'activ': {active}, expected {ACTIVE_QIDS}")
    rep = {(c["qid"], c["seed"]): c for c in reported}
    L = ["pre-registered measures (PREREG_DRAFT.md):"]
    for scoring in ("strict", "tolerant"):
        failed = []
        L.append(f"  [{scoring}] primary: the eight 'active' T4 questions (FAILED = silent_wrong on >= 2 of 3 seeds)")
        for q in ACTIVE_QIDS:
            cs = sorted((c for c in cells if c["qid"] == q), key=lambda c: c["seed"])
            sw = sum(c[scoring] == "silent_wrong" for c in cs)
            if sw >= 2:
                failed.append(q)
            L.append(f"    {q}: " + "  ".join(f"s{c['seed']} {json.dumps(c['status_filter'])} {c[scoring]}"
                                           f" (with alias: {rep[(q, c['seed'])][scoring]})" for c in cs)
                     + f"  -> {'FAILED' if sw >= 2 else 'not failed'} ({sw}/{len(cs)} silent)")
        n = len(failed)
        outcome = "A" if n >= 6 else "B" if n <= 2 else "C"
        L.append(f"    FAILED {n} of 8 {failed} -> OUTCOME {outcome}")
        other = [c for c in cells if c["qid"] not in ACTIVE_QIDS]
        moved = [c for c in other if c[scoring] != rep[(c["qid"], c["seed"])][scoring]]
        trans = Counter(f"{rep[(c['qid'], c['seed'])][scoring]}->{c[scoring]}" for c in moved)
        goal_moved = sum(c["goal_sha12"] != rep[(c["qid"], c["seed"])]["goal_sha12"] for c in other)
        L.append(f"    regression check, other {len({c['qid'] for c in other})} questions ({len(other)} cells): "
                 f"{len(moved)} cells change class {dict(trans)}; {goal_moved} cells emit another Goal -> "
                 + ("CLEAN" if len(moved) <= REGRESSION_LIMIT else f"NOT CLEAN (> {REGRESSION_LIMIT}): stop and report"))
        for c in moved:
            L.append(f"      {c['qid']} s{c['seed']}: {rep[(c['qid'], c['seed'])][scoring]} -> {c[scoring]}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--control", action="store_true", help="score the reported s31 GDC employee cells")
    ap.add_argument("--results", default=None, help=f"network_noalias results file (default results/{NEW_FILE})")
    ap.add_argument("--root", default=None)
    ap.add_argument("--out", default=None, help="output directory (default <root>/reports/alias_control)")
    a = ap.parse_args(argv)
    root = resolve_root(a.root)
    mods = load_modules(root)
    pi = mods[0]
    gold = pi.GOLD[DOM]
    out = Path(a.out) if a.out else root / "reports" / "alias_control"
    out.mkdir(parents=True, exist_ok=True)
    if a.control:
        rows, label = reported_rows(pi), "reported_s31"
    else:
        rows, label = new_rows(root, Path(a.results) if a.results else None), SYSTEM_ID
        rows = [r for r in rows if r["qid"] in gold and gold[r["qid"]]["tier"] != "out_of_range"]
    cells = [score_cell(mods, r, gold[r["qid"]], reported=a.control) for r in rows]
    cells.sort(key=lambda c: (c["qid"], c["seed"]))
    want = {(q, s) for q, g in gold.items() if g["tier"] != "out_of_range" for s in SEEDS}
    have = {(c["qid"], c["seed"]) for c in cells}
    text = summarise(cells, label)
    if have != want:
        text += f"  NOTE: {len(want - have)} planned cells missing, {len(have - want)} unplanned\n"
    rc = 0
    if not a.control:
        if have != want:
            text += "  (pre-registered measures need all 243 cells; not computed)\n"
        else:
            corpus = json.loads((root / "evaluation/domains/employee/eval_corpus_employee.json").read_text())
            rep = [score_cell(mods, r, gold[r["qid"]], reported=True) for r in reported_rows(pi)]
            text += preregistered(cells, rep, {q["id"]: q for q in corpus["queries"]})
    if a.control:
        bad, log = check_against_reported(root, cells)
        text += "control against the reported classes:\n" + "\n".join(log) + "\n"
        text += ("CONTROL PASSED: every reported class reproduced\n" if not bad and have == want
                 else "CONTROL FAILED:\n  " + "\n  ".join(bad) + "\n")
        rc = 0 if not bad and have == want else 1
    with open(out / f"cells_{label}.jsonl", "w", encoding="utf-8") as fh:
        for c in cells:
            fh.write(json.dumps(c, default=str) + "\n")
    (out / f"summary_{label}.txt").write_text(text)
    print(text, end="")
    print(f"wrote {out / f'cells_{label}.jsonl'} and summary_{label}.txt")
    return rc


if __name__ == "__main__":
    sys.exit(main())
