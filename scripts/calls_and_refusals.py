#!/usr/bin/env python
"""Mean LLM calls per query and refusal rates of the five designs on both models, on one
question set (the §5.3 calls-and-refusals table).

Designs: network (GDC), planner, react, code_agent, text_to_sql. Models:
  claude-sonnet-4-5               the recorded employee and Spider results the paper scores, read
                                  as scripts/build_paper_numbers.py reads them (PARITY_TEMP0=1, so
                                  with the temperature-0 overlays of scripts/parity_overlay.py);
  qwen3-coder-16k:30b-a3b-q4_K_M  the open-weights run of local_model/ (Ollama alias of
                                  qwen3-coder:30b-a3b-q4_K_M at num_ctx 16384),
                                  local_model/results/results_o2_<domain>.jsonl.

Questions. The tier of record is the gold record's (paper_inputs.retier); employee oor_03 has no
gold record since D11 and is in no set.
  in scope, 108: employee T1-T5 and house_vocab (81: the builder's Table2 employee
      in_contract_answer set) and the Spider answer tier, every tier but out_of_scope_register and
      out_of_contract_aggregate (27: the builder's Fig1/Fig2/Table3 spider_pooled
      in_contract_answer set). The beyond-vocabulary tier (out_of_contract_aggregate) is in
      neither set.
  out of scope, 54: employee out_of_range (9) and Spider out_of_scope_register (45), the decline
      tiers of the paper's out-of-scope table (the builder's Table5 rows).
Both models must cover these 162 questions for every design. If a model or design lacked some,
the script would use the questions every model and design covers and say so in its output.

Cells. claude-sonnet-4-5 has 3 seeds on employee and 5 on Spider: 378 in-scope and 252
out-of-scope cells per design. The open-weights run has one seed: 108 and 54. Every mean and
rate is over cells, not over per-question means, so under claude-sonnet-4-5 a Spider question
weighs 5/3 of an employee question.

Rows read, per design (claude-sonnet-4-5):
  employee: results/results_employee.jsonl, with the parity and batch-2 overlays
      (results_temp0_parity_employee.jsonl, results_temp0_batch2_employee.jsonl);
  Spider GDC: results/results_spider_<db>_postfix_s40off.jsonl, on both tiers (no overlay);
  Spider planner and the three baselines: results/results_spider_<db>.jsonl, with the parity and
      batch-2 overlays of that database.
The calls_source column of the CSV names every file the cells of a row came from, with counts.
Rows: claude-sonnet-4-5 first, then the open-weights model; within each, the paper's system order.

Mean LLM calls per query: the mean over the cells of the recorded llm_calls field of the row the
paper scores. GDC and the planner are scored on a replay of their recorded emission on this
repository's engine; their llm_calls is the usage of the recorded run that emitted it, which is
the row the replay reads, in the file calls_source names. min and max are written beside the
mean. Tokens are not computed.

Refusal rate, one definition per set, as the paper's tables count them (D8):
  in scope: refused_should_answer / in-scope cells, under the paper's scoring: the strict s48
      outcome with D8 applied (build_paper_numbers.d8_class): the builder's refused_should_answer
      rows in Table2 and Fig1/Fig2/Table3, the Refused column of the paper's tables. A refusal
      of an answerable question counts only when it is a decline, an explicit decline statement or
      a typed refusal. A failure (a GDC ParseError, an SQL error, a reply that neither parses nor
      declines, a run that completes with no result), a crash and a timeout are not refusals; they
      are counted in in_scope_failed_crash_timeout. Strict and tie-tolerant scoring give the same
      count, because the tie rule moves only answers.
  out of scope: declined / out-of-scope cells, declined = explicit + typed
      (build_paper_numbers.oos_class): the builder's declined rows in Table5, the declined column
      of the paper's out-of-scope table. Failures, crashes and timeouts are neither declines nor
      answers (out_of_scope_failed_crash_timeout).
The decline rule's hand readings (reports/decline_readings.json, keyed domain|system|qid|seed)
read claude-sonnet-4-5 cells. The open-weights cells, seed 0, share those keys but were not read,
so their D8 class comes from the rule alone (paper_inputs.rule_channel), as
local_model/analyse_o2.py classed them.

Checks; the script exits non-zero on any failure:
  * the 162 questions, per model and design, and one cell per question per seed;
  * claude-sonnet-4-5: per design, the in-scope refused_should_answer and failed counts and the
    out-of-scope declined and failed counts, with their denominators, equal the builder's rows in
    reports/paper_numbers.csv (Table2 employee in_contract_answer strict, Fig1/Fig2/Table3
    spider_pooled in_contract_answer strict, Table5 employee and spider_pooled);
  * open-weights: every cell's strict outcome, decline channel and llm_calls equal
    local_model/analysis/per_question.csv.

Offline: no API call. Reads files of this repository only; writes only --out.

    python scripts/calls_and_refusals.py [--out reports/calls_and_refusals.csv]
"""
from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import os
import sys
import types
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
if os.environ.get("PARTIAL_BATCH2"):
    raise SystemExit("PARTIAL_BATCH2 is set: the paper's numbers read whole batch-2 files only; unset it")
os.environ["PARITY_TEMP0"] = "1"                 # read the cells as the builder reads them (run_all.sh step 4)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evidence.eval_run.comparator import COMPARATOR_VERSION, score  # noqa: E402
from scripts import paper_inputs as pi                              # noqa: E402
from scripts.parity_overlay import PARITY                           # noqa: E402

assert PARITY and COMPARATOR_VERSION == "s48", (PARITY, COMPARATOR_VERSION)

OUT = ROOT / "reports" / "calls_and_refusals.csv"
LOCAL = ROOT / "local_model"
DOMAINS = ("employee",) + tuple(pi.SPIDER)
SYSTEMS = tuple(pi.SYSTEMS)
DESIGN = {"network": "GDC", "planner": "planner", "react": "ReAct", "code_agent": "code agent",
          "text_to_sql": "text-to-SQL"}
CLAUDE, OPEN = "claude-sonnet-4-5", "qwen3-coder-16k:30b-a3b-q4_K_M"
MODELS = (CLAUDE, OPEN)
EMP_IN = ("T1_single_op", "T2_two_ops", "T3_three_ops", "T4_business_rule_multi_op", "T5_high_complexity",
          "house_vocab")
OOSR, OOCA, OOR = "out_of_scope_register", "out_of_contract_aggregate", "out_of_range"
FIELDS = ["model", "design", "system", "questions", "questions_in_scope", "questions_out_of_scope", "cells",
          "mean_llm_calls_per_query", "min_llm_calls", "max_llm_calls", "mean_llm_calls_in_scope",
          "mean_llm_calls_out_of_scope", "in_scope_cells", "in_scope_refused", "in_scope_refused_pct",
          "in_scope_failed_crash_timeout", "out_of_scope_cells", "out_of_scope_declined",
          "out_of_scope_declined_pct", "out_of_scope_failed_crash_timeout", "calls_source"]


def group_of(dom: str, tier: str) -> str | None:
    """in / out / None (in neither set) for a question's tier of record."""
    if dom == "employee":
        return "in" if tier in EMP_IN else "out" if tier == OOR else None
    return "out" if tier == OOSR else None if tier == OOCA else "in"


def builder_functions(decline_channel) -> dict:
    """outcome, d8_class and oos_class, lifted from build_paper_numbers.py by ast (importing the
    builder runs its whole build), bound to the given decline-channel function."""
    src_p = ROOT / "scripts" / "build_paper_numbers.py"
    nodes = [n for n in ast.parse(src_p.read_text()).body
             if isinstance(n, ast.FunctionDef) and n.name in ("outcome", "d8_class", "oos_class")]
    if sorted(n.name for n in nodes) != ["d8_class", "oos_class", "outcome"]:
        raise SystemExit("build_paper_numbers.py: outcome/d8_class/oos_class not found")
    ns = {"score": score, "scored_input": pi.scored_input, "pi": types.SimpleNamespace(decline_channel=decline_channel)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(src_p), "exec"), ns)
    return ns


def classify(F: dict, dom: str, row: dict, grp: str) -> str:
    g = pi.GOLD[dom][row["qid"]]
    if grp == "in":
        return F["d8_class"](dom, row, F["outcome"](dom, row, g))
    return F["oos_class"](dom, row, g)


def claude_rows() -> list:
    """(domain, source file, row) for every claude-sonnet-4-5 cell of the two sets."""
    out = [("employee", r.get("overlay_source") or pi.EMPLOYEE_FILE, r) for r in pi.employee()
           if r["qid"] in pi.GOLD["employee"]]
    for d in pi.SPIDER:
        for s in SYSTEMS:
            fn = pi.spider_file(d, s)
            out += [(d, r.get("overlay_source") or fn, r) for r in pi.retier(pi.load(fn), pi.GOLD[d])
                    if r.get("system") == s and r["qid"] in pi.GOLD[d]]
    return out


def open_rows() -> list:
    out = []
    for d in DOMAINS:
        fn = f"local_model/results/results_o2_{d}.jsonl"
        rows = [json.loads(ln) for ln in (ROOT / fn).read_text().splitlines() if ln.strip()]
        out += [(d, fn, r) for r in pi.retier(rows, pi.GOLD[d]) if r["qid"] in pi.GOLD[d]]
    return out


def check_models(cells: dict) -> None:
    """The model each source file's provenance records is the model its row is labelled with."""
    for m, cs in cells.items():
        for fn in sorted({c["file"] for c in cs}):
            p = ROOT / ("" if "/" in fn else "results") / fn.replace(".jsonl", ".provenance.json")
            got = json.loads(p.read_text()).get("model")
            if got != m:
                raise SystemExit(f"{p.relative_to(ROOT)} records model {got!r}, not {m}")


def cells_of(model: str) -> list:
    """One dict per cell of the 162 questions: model, system, domain, qid, seed, group, class,
    llm_calls, source file."""
    F = builder_functions(pi.decline_channel if model == CLAUDE else pi.rule_channel)
    rows = claude_rows() if model == CLAUDE else open_rows()
    out = []
    for dom, fn, r in rows:
        grp = group_of(dom, pi.GOLD[dom][r["qid"]]["tier"])
        if grp is None or r.get("system") not in SYSTEMS:
            continue
        calls = r.get("llm_calls")
        if not isinstance(calls, int) or isinstance(calls, bool):
            raise SystemExit(f"{fn} {dom}/{r['system']}/{r['qid']}/{r.get('seed')}: llm_calls {calls!r} is not a count")
        out.append({"model": model, "system": r["system"], "domain": dom, "qid": r["qid"], "seed": r.get("seed"),
                    "group": grp, "cls": classify(F, dom, r, grp), "llm_calls": calls, "file": fn, "row": r})
    return out


def check_claude_against_builder(cells: list) -> None:
    """Every claude-sonnet-4-5 count against reports/paper_numbers.csv."""
    p = ROOT / "reports" / "paper_numbers.csv"
    idx = {(r["fig_table"], r["domain"], r["system"], r["tier_group"], r["scoring"], r["metric"]):
           (int(r["numerator"]), int(r["denominator"])) for r in csv.DictReader(open(p, newline="", encoding="utf-8"))}
    bad = []
    for s in SYSTEMS:
        for part, key, grp in (("employee", ("Table2", "employee", s, "in_contract_answer", "strict"), "in"),
                               ("spider", ("Fig1/Fig2/Table3", "spider_pooled", s, "in_contract_answer", "strict"), "in"),
                               ("employee", ("Table5", "employee", s, OOR, "strict"), "out"),
                               ("spider", ("Table5", "spider_pooled", s, OOSR, "strict"), "out")):
            cs = [c["cls"] for c in cells if c["system"] == s and c["group"] == grp
                  and (c["domain"] == "employee") == (part == "employee")]
            mine = ({"refused_should_answer": cs.count("refused_should_answer"), "failed": cs.count("failed")}
                    if grp == "in" else
                    {"declined": sum(v.startswith("declined_") for v in cs), "failed": cs.count("failed")})
            for metric, k in mine.items():
                want = idx.get(key + (metric,))
                if want != (k, len(cs)):
                    bad.append(f"{'|'.join(key)}|{metric}: builder {want}, here {(k, len(cs))}")
    if bad:
        raise SystemExit("claude-sonnet-4-5 counts differ from reports/paper_numbers.csv "
                         "(run ./run_all.sh step 4 first):\n  " + "\n  ".join(bad))


def check_open_against_analysis(cells: list) -> None:
    """Every open-weights cell against local_model/analysis/per_question.csv."""
    pq = {(r["domain"], r["system"], r["qid"]): r
          for r in csv.DictReader(open(LOCAL / "analysis" / "per_question.csv", newline="", encoding="utf-8"))}
    F = builder_functions(pi.rule_channel)
    bad = []
    for c in cells:
        x = pq.get((c["domain"], c["system"], c["qid"]))
        r, dom = c["row"], c["domain"]
        strict = F["outcome"](dom, r, pi.GOLD[dom][r["qid"]])
        ch = pi.rule_channel(dom, r)
        mine = (strict, ch[0] if ch else "", str(c["llm_calls"]), pi.GOLD[dom][r["qid"]]["tier"])
        theirs = (x["strict"], x["decline_channel"], x["llm_calls"], x["tier"]) if x else None
        if mine != theirs:
            bad.append(f"{dom}/{c['system']}/{c['qid']}: here {mine}, per_question.csv {theirs}")
    if bad:
        raise SystemExit(f"{len(bad)} open-weights cell(s) differ from local_model/analysis/per_question.csv:\n  "
                         + "\n  ".join(bad[:20]))


def pct(k: int, n: int) -> str:
    return f"{100 * k / n:.1f}" if n else ""


def mean2(xs: list) -> str:
    return f"{sum(xs) / len(xs):.2f}" if xs else ""


def build() -> tuple[list, list]:
    """-> (CSV rows, notes)."""
    notes = []
    want = {(d, q): group_of(d, g["tier"]) for d in DOMAINS for q, g in pi.GOLD[d].items()}
    want = {k: v for k, v in want.items() if v}
    n_in, n_out = sum(v == "in" for v in want.values()), sum(v == "out" for v in want.values())
    if (n_in, n_out) != (108, 54):
        notes.append(f"the gold defines {n_in} in-scope and {n_out} out-of-scope questions, not 108 and 54")
    cells = {m: cells_of(m) for m in MODELS}
    check_models(cells)
    check_claude_against_builder(cells[CLAUDE])
    check_open_against_analysis(cells[OPEN])
    # coverage: the same questions, per model and design; one cell per question and seed
    have = {}
    for m in MODELS:
        for s in SYSTEMS:
            cs = [c for c in cells[m] if c["system"] == s]
            dup = [k for k, n in Counter((c["domain"], c["qid"], c["seed"]) for c in cs).items() if n > 1]
            if dup:
                raise SystemExit(f"{m} {s}: {len(dup)} duplicated cells, e.g. {dup[:3]}")
            have[(m, s)] = {(c["domain"], c["qid"]) for c in cs}
            seeds = Counter(frozenset(c["seed"] for c in cs if (c["domain"], c["qid"]) == k) for k in have[(m, s)])
            if m == OPEN and set(seeds) != {frozenset({0})}:
                raise SystemExit(f"{m} {s}: seeds {dict(seeds)}, expected seed 0 only")
    common = set(want).intersection(*have.values())
    for (m, s), qs in sorted(have.items()):
        if qs != set(want):
            notes.append(f"{m} {s}: {len(set(want) - qs)} question(s) missing, {len(qs - set(want))} extra; "
                         f"the table uses the {len(common)} questions every model and design covers")
    rows = []
    for m in MODELS:
        for s in SYSTEMS:
            cs = [c for c in cells[m] if c["system"] == s and (c["domain"], c["qid"]) in common]
            ci = [c for c in cs if c["group"] == "in"]
            co = [c for c in cs if c["group"] == "out"]
            calls = [c["llm_calls"] for c in cs]
            src = Counter(c["file"] for c in cs)
            rows.append({
                "model": m, "design": DESIGN[s], "system": s,
                "questions": len({(c["domain"], c["qid"]) for c in cs}),
                "questions_in_scope": len({(c["domain"], c["qid"]) for c in ci}),
                "questions_out_of_scope": len({(c["domain"], c["qid"]) for c in co}),
                "cells": len(cs), "mean_llm_calls_per_query": mean2(calls), "min_llm_calls": min(calls),
                "max_llm_calls": max(calls), "mean_llm_calls_in_scope": mean2([c["llm_calls"] for c in ci]),
                "mean_llm_calls_out_of_scope": mean2([c["llm_calls"] for c in co]),
                "in_scope_cells": len(ci),
                "in_scope_refused": sum(c["cls"] == "refused_should_answer" for c in ci),
                "in_scope_refused_pct": pct(sum(c["cls"] == "refused_should_answer" for c in ci), len(ci)),
                "in_scope_failed_crash_timeout": sum(c["cls"] in ("failed", "crash", "timeout") for c in ci),
                "out_of_scope_cells": len(co),
                "out_of_scope_declined": sum(c["cls"].startswith("declined_") for c in co),
                "out_of_scope_declined_pct": pct(sum(c["cls"].startswith("declined_") for c in co), len(co)),
                "out_of_scope_failed_crash_timeout": sum(c["cls"] in ("failed", "crash", "timeout") for c in co),
                "calls_source": "; ".join(f"{fn} {n}" for fn, n in sorted(src.items()))})
    return rows, notes


def markdown(rows: list) -> str:
    L = ["| design | model | questions (cells) | LLM calls per query | in-scope refused % | out-of-scope declined % |",
         "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['design']} | {r['model']} | {r['questions']} ({r['cells']}) | {r['mean_llm_calls_per_query']} | "
                 f"{r['in_scope_refused_pct']} ({r['in_scope_refused']}/{r['in_scope_cells']}) | "
                 f"{r['out_of_scope_declined_pct']} ({r['out_of_scope_declined']}/{r['out_of_scope_cells']}) |")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    rows, notes = build()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(buf.getvalue(), encoding="utf-8")
    print(f"Calls and refusals, five designs x two models ({rows[0]['questions']} questions: "
          f"{rows[0]['questions_in_scope']} in scope, {rows[0]['questions_out_of_scope']} out of scope; "
          f"means and rates over cells; refusals under D8). Checked against reports/paper_numbers.csv "
          f"and local_model/analysis/per_question.csv.")
    for n in notes:
        print("NOTE:", n)
    print()
    print(markdown(rows))
    print(f"\nwrote {a.out.relative_to(ROOT) if a.out.is_relative_to(ROOT) else a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
