#!/usr/bin/env python
"""RELEASE NOTE. This is the development tree's scoring script with its absolute path
defaults replaced by placeholders (DEV, D_TREE, D_CWD below are directories of the development
workspace, and --claude-csv, --tree, --run, --stats take the real locations) and with the author's
first name replaced by "the author" in the text it writes. It scores the cells in results/ with the
development tree's own functions (a scoring tree: the development tree at 5bf429e plus the decisions
patch), so it cannot run in this repository; the outputs it wrote are in analysis/. Two changes
since the scoring run. The rates and comparisons now keep their unrounded values and only the CSVs
round them (to two decimals, as before), so every figure that summary.txt and O2_RESULTS.md print
is rounded once; the scoring run printed the two-decimal values to one decimal, so that 7/13 =
53.846% printed as 53.9. And `--from-shipped`, which runs in this repository, rewrites the
statistics and status-count lines of analysis/summary.txt from the shipped analysis/per_question.csv
and results/ with the same functions, after checking that the rates and comparisons it recomputes
give the shipped rates.csv and comparisons.csv byte for byte (`--check` compares and writes
nothing). Nothing else differs.

O2 analysis: the local open-weights replication (Qwen3-Coder 30B, 4-bit, through Ollama),
scored exactly as the paper scores the Claude cells.

Offline and read-only on every input. No API call, no network (a socket guard refuses any
connection), no .env (a stub `dotenv` module is installed before any tree import, because
gdc/config.py calls load_dotenv() at import).

Inputs (all read-only)
  --tree   a scoring tree: git archive 5bf429e + decisions_2026-10-01/pipeline/patches/
           all_decisions_vs_5bf429e.patch (D5, D6, D7, D8, D11). Its own functions score:
           paper_inputs.GOLD / retier / scored / scored_input (GDC and planner REPLAYED on the
           tree's engine, the other three on their recorded raw_result) / decline_channel (D8),
           comparator.score (s48), score_tie_tolerant.score_tt (s48t, reports/tie_alternatives.json),
           and build_paper_numbers.outcome / oos_class, lifted from the builder's source by ast
           (the builder itself runs its whole build at import, so it is not imported).
  --run    the O2 run directory (results/results_o2_<domain>.jsonl, calls_*, cell_context_*,
           sessions.jsonl, run_full.log, full_scope.json, tree/ = the run's own code).
  --stats  question_clustered_stats.py: boot_index, sign_test, permutation_p, holm, cluster_var,
           N_BOOT and BASE_SEED are imported from it, unchanged.
  --claude-csv  the Claude-side numbers (default: work_2026-10-02/m2/paper_numbers.final.csv; if
           absent, decisions_2026-10-01/pipeline/paper_numbers.decisions.csv, whose baselines are
           pre-batch-2).
  --ollama-logs  ~/.ollama/logs, read for the disclosure evidence; the lines used are saved to
           <out>/evidence/ so a later run without those logs (they rotate) reuses them.

Rules
  * Tier of record = the tree's gold record (paper_inputs.retier). D11: employee oor_03 has no
    gold record, so its five cells are listed and not scored. D7: wta_26 is out_of_contract_aggregate
    with an answer gold. D8: the decline tiers split a refusal into declined (explicit or typed)
    and failed, by paper_inputs.decline_channel, as Table 5 does.
  * Engine check: parity_overlay.engine_hashes of the scoring tree and of the run tree. If they
    agree, the recorded GDC/planner results are scored and a sample is replayed; if they differ,
    every GDC and planner cell is replayed (as the paper does) and the recorded result is compared
    with the replay cell by cell.
  * Statistics as in question_clustered_stats.py and heldout/analyse_heldout.py: per set, one
    question-cluster bootstrap index (10,000 resamples, seed BASE_SEED + crc32(set name),
    stratified by database), percentile 95% intervals; GDC minus each alternative with an exact
    sign test, an exact sign-flip permutation test and Holm over the four alternatives;
    "resolved" = interval excludes 0 and permutation p < 0.05. One seed, so a cell is a question.

    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python analyse_o2.py
Outputs (<out>): rates.csv, comparisons.csv, per_question.csv, mismatches.csv, status_counts.csv,
claude_side.csv, summary.txt, O2_RESULTS.md, evidence/ollama_evidence.json, evidence/ollama_log_excerpts.txt
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True          # no __pycache__ under any read-only directory

import argparse                          # noqa: E402
import ast                               # noqa: E402
import csv                               # noqa: E402
import datetime as dt                    # noqa: E402
import hashlib                           # noqa: E402
import importlib.util                    # noqa: E402
import json                              # noqa: E402
import math                              # noqa: E402
import os                                # noqa: E402
import re                                # noqa: E402
import socket                            # noqa: E402
import types                             # noqa: E402
import zlib                              # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from pathlib import Path                 # noqa: E402

DEV = Path("dev_tree")                                       # placeholder: the development tree
AUDIT = DEV / "docs" / "audit" / "phase2_2026-10-01"
HERE = Path(__file__).resolve().parent
D_TREE = Path("scoring_tree")                                # placeholder: 5bf429e + the decisions patch
D_RUN = AUDIT / "o2_localmodel" / "run"
D_STATS = HERE.parent / "scripts" / "question_clustered_stats.py"   # this repository's copy
D_CLAUDE = AUDIT / "work_2026-10-02" / "m2" / "paper_numbers.final.csv"
D_CLAUDE_FALLBACK = AUDIT / "decisions_2026-10-01" / "pipeline" / "paper_numbers.decisions.csv"
D_CWD = Path("scoring_cwd")                                 # placeholder: an empty working directory

DOMAINS = ("employee", "wta_1", "world_1", "bike_1")
SPIDER = ("wta_1", "world_1", "bike_1")
GDC = "network"
ALTS = ("planner", "react", "code_agent", "text_to_sql")
SYSTEMS = (GDC,) + ALTS
EMP_IC = ("T1_single_op", "T2_two_ops", "T3_three_ops", "T4_business_rule_multi_op",
          "T5_high_complexity", "house_vocab")
OOSR, OOCA, OOR = "out_of_scope_register", "out_of_contract_aggregate", "out_of_range"
SPIDER_BOUNDARY = {OOSR, OOCA}
# the earlier, unverified chat figures this analysis is asked to check
CHAT = {"pooled_silent_pct": {"network": 14.2, "planner": 20.5, "text_to_sql": 29.0,
                              "code_agent": 40.3, "react": 46.0},
        "gdc_checks": (42, 47), "gdc_in_vocab_acc_pct": 56.5, "gdc_in_vocab_refused_pct": 38.9,
        "react_timeout_pct": 17.6}


# ── guards ────────────────────────────────────────────────────────────────────
BLOCKED: list = []


def install_guards() -> None:
    for k in ("PARITY_TEMP0", "PARTIAL_BATCH2"):
        if os.environ.get(k):
            raise SystemExit(f"{k} is set: this analysis reads no temperature-0 overlay; unset it")
    if "ANTHROPIC_API_KEY" in os.environ:
        raise SystemExit("ANTHROPIC_API_KEY is set: this analysis needs no key; unset it")
    stub = types.ModuleType("dotenv")

    def _blocked(*a, **k):
        BLOCKED.append("load_dotenv")
        return False
    stub.load_dotenv = _blocked
    stub.find_dotenv = lambda *a, **k: ""
    stub.dotenv_values = lambda *a, **k: {}
    sys.modules["dotenv"] = stub

    def _no_net(*a, **k):
        raise RuntimeError("analyse_o2: network access refused (the analysis is offline)")
    socket.socket.connect = _no_net
    socket.socket.connect_ex = _no_net
    socket.create_connection = _no_net
    socket.getaddrinfo = _no_net


def sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def utc(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def log_epoch(text: str) -> float | None:
    """Epoch of an Ollama log line's `time=...+01:00` stamp."""
    m = re.search(r"time=(\d{4}-\d\d-\d\dT[\d:.]+[+-]\d\d:\d\d)", text)
    return dt.datetime.fromisoformat(m.group(1)).timestamp() if m else None


def hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def jl(p: Path) -> list:
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]


# ── tree imports ──────────────────────────────────────────────────────────────
class Tree:
    def __init__(self, tree: Path):
        sys.path.insert(0, str(tree))
        from scripts import paper_inputs as pi                          # noqa: E402
        from scripts import parity_overlay as po                        # noqa: E402
        from scripts.score_tie_tolerant import SCORER_VERSION, score_tt  # noqa: E402
        from scripts.replay_goals import logger_view                    # noqa: E402
        from evidence.eval_run.comparator import COMPARATOR_VERSION, score  # noqa: E402
        from evidence.eval_run.types import REFUSAL                     # noqa: E402
        assert Path(pi.__file__).resolve().is_relative_to(tree.resolve()), pi.__file__
        assert po.PARITY is False and po.PARTIAL_BATCH2 is False
        assert COMPARATOR_VERSION == "s48" and SCORER_VERSION == "s48t", (COMPARATOR_VERSION, SCORER_VERSION)
        self.root, self.pi, self.po, self.score, self.score_tt = tree, pi, po, score, score_tt
        self.logger_view, self.REFUSAL = logger_view, REFUSAL
        self.comparator, self.scorer = COMPARATOR_VERSION, SCORER_VERSION
        self.ties = json.loads((tree / "reports" / "tie_alternatives.json").read_text())
        self.ties_syn = json.loads((tree / "reports" / "tie_alternatives_synthetic.json").read_text())
        # build_paper_numbers.outcome / oos_class, lifted from the builder's own source
        src_p = tree / "scripts" / "build_paper_numbers.py"
        mod = ast.parse(src_p.read_text())
        nodes = [n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name in ("outcome", "oos_class")]
        if sorted(n.name for n in nodes) != ["oos_class", "outcome"]:
            raise SystemExit("build_paper_numbers.py: outcome/oos_class not found")
        ns = {"score": score, "scored_input": pi.scored_input, "pi": pi}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(src_p), "exec"), ns)
        self.outcome, self.oos_class = ns["outcome"], ns["oos_class"]
        self.builder_lines = {n.name: (n.lineno, n.end_lineno) for n in nodes}


def load_stats(path: Path):
    spec = importlib.util.spec_from_file_location("question_clustered_stats", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.N_BOOT == 10_000, m.N_BOOT
    return m


# ── 1. scoring ────────────────────────────────────────────────────────────────
def load_o2(run: Path) -> list:
    scope = json.loads((run / "full_scope.json").read_text())
    rows = []
    for dom in DOMAINS:
        for r in jl(run / "results" / f"results_o2_{dom}.jsonl"):
            r["_dom"] = dom
            rows.append(r)
    planned = {(d, s, q, 0) for d in DOMAINS for s in SYSTEMS for q in scope[d]}
    seen = Counter((r["_dom"], r["system"], r["qid"], int(r["seed"])) for r in rows)
    missing, extra = planned - set(seen), set(seen) - planned
    dup = [k for k, n in seen.items() if n > 1]
    if missing or extra or dup:
        raise SystemExit(f"INCOMPLETE OR INCONSISTENT RUN: {len(missing)} missing, {len(extra)} unplanned, "
                         f"{len(dup)} duplicated cells")
    return rows


def canon(T: Tree, x) -> str:
    return json.dumps(T.logger_view(x), sort_keys=True, default=str)


def set_group(dom: str, tier: str) -> str:
    if dom == "employee":
        return "in_contract" if tier in EMP_IC else ("out_of_range" if tier == OOR else "other:" + tier)
    return "answer" if tier not in SPIDER_BOUNDARY else tier


def score_cells(T: Tree, rows: list, replay_all: bool, sample_every: int = 7) -> list:
    pi = T.pi
    cells = []
    for i, r in enumerate(rows):
        dom, s, qid = r["_dom"], r["system"], r["qid"]
        a = r.get("artifact") if isinstance(r.get("artifact"), dict) else {}
        c = {"domain": dom, "qid": qid, "system": s, "seed": int(r["seed"]), "raw_tier": r.get("tier"),
             "status_recorded": r.get("status"), "verdict_runtime": r.get("comparator_verdict"),
             "outcome_class_runtime": r.get("outcome_class"), "error": str(r.get("error") or "")[:160],
             "llm_calls": r.get("llm_calls"), "wall_time_s": r.get("wall_time_s"),
             "has_goal": bool(s == GDC and a.get("goal"))}
        g = pi.GOLD[dom].get(qid)
        if g is None:
            c.update(scored=False, tier="", group="dropped",
                     drop_reason="no gold record in the scoring tree (D11: employee oor_03 dropped)")
            cells.append(c)
            continue
        assert pi.scored([r], pi.GOLD[dom]) == [r]
        tier = pi.retier([r], pi.GOLD[dom])[0]["tier"]
        assert tier == g["tier"]
        replayed = s in pi.REPLAYED and (replay_all or i % sample_every == 0)
        if s in pi.REPLAYED and replayed:
            res, status = pi.scored_input(dom, r)
        else:
            res, status = r.get("raw_result"), r.get("status", "completed")
        strict = T.score(res, g, status=status)[0]
        tol = T.score_tt(res, g, qid, status=status)[0] if dom in SPIDER else strict
        if replayed or s not in pi.REPLAYED:
            assert T.outcome(dom, r, g) == strict      # the builder's own outcome() agrees
        c.update(scored=True, tier=tier, group=set_group(dom, tier), expected=g["expected_behavior"],
                 status_scored=status, strict=strict, tolerant=tol, scored_from=(
                     "replay (tree engine)" if replayed else "recorded raw_result"))
        if s in pi.REPLAYED:
            rec_strict = T.score(r.get("raw_result"), g, status=r.get("status", "completed"))[0]
            if replayed:
                c["replay_equal_recorded"] = (status == r.get("status") and canon(T, res) == canon(T, r.get("raw_result")))
            c["strict_on_recorded"] = rec_strict
        ch = pi.decline_channel(dom, r)
        c["decline_channel"], c["decline_detail"] = (ch if ch else ("", ""))
        if g["expected_behavior"] == "refuse":
            c["d8_class"] = T.oos_class(dom, r, g)
        else:
            c["d8_class"] = ""
        cells.append(c)
    return cells


def explain_mismatch(T: Tree, run_tree_gold: dict, c: dict) -> str:
    """Why a strict re-score differs from the run-time verdict."""
    dom, qid = c["domain"], c["qid"]
    old = run_tree_gold[dom].get(qid)
    new = T.pi.GOLD[dom].get(qid)
    if old is not None and new is not None and json.dumps(old, sort_keys=True) != json.dumps(new, sort_keys=True):
        diff = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
        tag = " (D7)" if (dom, qid) == ("wta_1", "wta_26") else ""
        return f"gold record changed between the run tree and the scoring tree{tag}: {', '.join(diff)}"
    if c["system"] in T.pi.REPLAYED and c.get("replay_equal_recorded") is False:
        return "replay result differs from the recorded result"
    return "unexplained (same gold, same scored input)"


# ── 2. statistics ─────────────────────────────────────────────────────────────
ANSWER_METRICS = [("accuracy", lambda v: v == "correct"), ("silent_error", lambda v: v == "silent_wrong"),
                  ("refused_should_answer", lambda v: v == "refused_should_answer"),
                  ("timeout", lambda v: v == "timeout"), ("crash", lambda v: v == "crash")]
DECLINE_METRICS = [("answer_anyway", lambda v: v == "silent_wrong"),
                   ("declined", lambda v: v.startswith("declined_")),
                   ("failed", lambda v: v == "failed"), ("timeout", lambda v: v == "timeout"),
                   ("crash", lambda v: v == "crash"),
                   ("declined_explicit", lambda v: v == "declined_explicit"),
                   ("declined_typed", lambda v: v == "declined_typed"),
                   ("refused_any", lambda v: v.startswith("declined_") or v == "failed")]
COMPARE = {"answer": ("silent_error", "accuracy", "refused_should_answer"), "decline": ("answer_anyway", "declined")}


def build_sets(cells: list) -> list:
    """(name, rule, kind, cells, primary?) in report order."""
    sc = [c for c in cells if c["scored"]]
    emp_ic = [c for c in sc if c["domain"] == "employee" and c["group"] == "in_contract"]
    emp_oor = [c for c in sc if c["domain"] == "employee" and c["group"] == "out_of_range"]
    sp_ans = [c for c in sc if c["domain"] in SPIDER and c["group"] == "answer"]
    sp_ooca = [c for c in sc if c["domain"] in SPIDER and c["group"] == OOCA]
    sp_oosr = [c for c in sc if c["domain"] in SPIDER and c["group"] == OOSR]
    sets = [("o2_employee_in_contract", "strict", "answer", emp_ic, True),
            ("o2_employee_out_of_range", "d8_class", "decline", emp_oor, True),
            ("o2_spider_answer_tolerant", "tolerant", "answer", sp_ans, True),
            ("o2_spider_answer_strict", "strict", "answer", sp_ans, True),
            ("o2_spider_out_of_contract_aggregate", "strict", "answer", sp_ooca, True),
            ("o2_spider_out_of_scope_register", "d8_class", "decline", sp_oosr, True),
            ("o2_pooled_within_vocab_tolerant", "tolerant", "answer", emp_ic + sp_ans, True),
            ("o2_pooled_within_vocab_strict", "strict", "answer", emp_ic + sp_ans, True),
            ("o2_pooled_in_scope_strict", "strict", "answer", emp_ic + sp_ans + sp_ooca, False)]
    for d in SPIDER:
        sets.append((f"o2_spider_answer_tolerant_{d}", "tolerant", "answer", [c for c in sp_ans if c["domain"] == d], False))
        sets.append((f"o2_spider_out_of_scope_register_{d}", "d8_class", "decline",
                     [c for c in sp_oosr if c["domain"] == d], False))
    return sets


def analyse(Q, name: str, rule: str, kind: str, cs: list, compare: bool):
    np = Q.np
    pq = defaultdict(lambda: defaultdict(list))
    for c in cs:
        pq[c["system"]][(c["domain"], c["qid"])].append(c[rule])
    keys = sorted(pq[GDC])
    for s in SYSTEMS:
        if sorted(pq[s]) != keys:
            raise SystemExit(f"{name}: question sets differ between {GDC} and {s}")
    m = np.array([len(pq[GDC][k]) for k in keys])
    strata = np.array([k[0] for k in keys])                      # stratified by database
    rng = np.random.default_rng(Q.BASE_SEED + zlib.crc32(name.encode()))
    idx = Q.boot_index(strata, rng)
    M, L = float(m.sum()), math.lcm(*[int(x) for x in set(m)])
    Mb = m[idx].sum(1)
    metrics = ANSWER_METRICS if kind == "answer" else DECLINE_METRICS
    rates, comps = [], []
    for metric, pred in metrics:
        K = {s: np.array([sum(1 for v in pq[s][k] if pred(v)) for k in keys]) for s in SYSTEMS}
        for s in SYSTEMS:
            k = K[s]
            p = k.sum() / M
            bt = k[idx].sum(1) / Mb
            v = Q.cluster_var(k - p * m, strata, M)
            rates.append({"set": name, "rule": rule.replace("d8_class", "strict+D8"), "metric": metric, "system": s,
                          "k": int(k.sum()), "n_cells": int(M), "n_questions": len(keys),
                          "rate_pct": 100 * p, "ci_lo_pct": 100 * np.percentile(bt, 2.5),
                          "ci_hi_pct": 100 * np.percentile(bt, 97.5),
                          "questions_with_any": int((k > 0).sum()),
                          "n_eff": round(p * (1 - p) / v, 1) if v > 0 else ""})
        if not compare or metric not in COMPARE[kind]:
            continue
        ps, block = [], []
        for a in ALTS:
            dk = K[GDC] - K[a]
            bt = dk[idx].sum(1) / Mb
            lo, hi = np.percentile(bt, 2.5), np.percentile(bt, 97.5)
            drate = dk / m
            npos, nneg, psign = Q.sign_test(drate)
            pperm = Q.permutation_p((dk * (L // m)).astype(np.int64))
            by_h = " ".join(f"{h}:{int((drate[strata == h] > 0).sum())}/{int((drate[strata == h] < 0).sum())}"
                            for h in sorted(set(strata)))
            block.append({"set": name, "rule": rule.replace("d8_class", "strict+D8"), "metric": metric, "alt": a,
                          "gdc_pct": 100 * K[GDC].sum() / M, "alt_pct": 100 * K[a].sum() / M,
                          "diff_pp": 100 * dk.sum() / M, "ci_lo_pp": 100 * lo,
                          "ci_hi_pp": 100 * hi, "excludes_0": bool(lo > 1e-12 or hi < -1e-12),
                          "n_questions": len(keys), "n_discordant_q": npos + nneg, "q_gdc_higher": npos,
                          "q_alt_higher": nneg, "p_sign": psign, "p_perm": pperm,
                          "discordant_by_database_gdc_higher/alt_higher": by_h})
            ps.append(pperm)
        for c, ph in zip(block, Q.holm(ps)):
            c["p_perm_holm"] = ph
            c["verdict"] = ("resolved" if c["excludes_0"] and c["p_perm"] < 0.05 else
                            "bootstrap-only" if c["excludes_0"] else
                            "exact-test-only" if c["p_perm"] < 0.05 else "unresolved")
            c["resolved_after_holm"] = bool(c["excludes_0"] and ph < 0.05)
        comps += block
    return rates, comps


# ── 3. context loss, sessions, disclosure facts from the run directory ───────
def run_facts(run: Path, rows: list) -> dict:
    R = run / "results"
    F: dict = {}
    # cell-level context flags
    cc = {}
    for dom in DOMAINS:
        for i, x in enumerate(jl(R / f"cell_context_{dom}.jsonl"), 1):
            cc[(dom, x["system"], x["qid"])] = dict(x, _line=i, _file=f"cell_context_{dom}.jsonl")
    have = {(r["_dom"], r["system"], r["qid"]) for r in rows}
    F["cc_missing"] = sorted(have - set(cc))
    F["cc_flagged"] = sorted(k for k, x in cc.items() if x["context_lost"])
    F["cc_unaligned"] = sorted(k for k, x in cc.items() if not x["aligned"])
    F["cc_events"] = sorted(k for k, x in cc.items() if x["loss_events"])
    F["cc_n"] = len(cc)
    F["cc_n_ctx"] = sorted({n for x in cc.values() for n in x["n_ctx"]})
    F["cc_temps"] = sorted({t for x in cc.values() for t in x["temps"]})
    F["cc_max_prompt"] = {s: max(x["max_prompt_tokens"] for k, x in cc.items() if k[1] == s) for s in SYSTEMS}
    F["cc_max_prompt_plus_out"] = {s: max(x["max_prompt_plus_out"] for k, x in cc.items() if k[1] == s) for s in SYSTEMS}
    F["cc_by_domain"] = {d: {"flagged": sum(1 for k in F["cc_flagged"] if k[0] == d),
                             "cells": sum(1 for k in cc if k[0] == d)} for d in DOMAINS}
    F["cc_records"] = cc
    # calls
    calls = []
    for dom in DOMAINS:
        for i, x in enumerate(jl(R / f"calls_{dom}.jsonl"), 1):
            x["_line"], x["_file"] = i, f"calls_{dom}.jsonl"
            calls.append(x)
    F["calls"] = calls
    by_cell = defaultdict(list)
    for x in calls:
        by_cell[(x["domain"], x["system"], x["qid"])].append(x)
    F["calls_by_cell"] = by_cell
    F["cells_without_calls"] = sorted(k for k in have if not by_cell.get(k))
    llm_calls = {(r["_dom"], r["system"], r["qid"]): r.get("llm_calls") or 0 for r in rows}
    # sessions
    sess = jl(R / "sessions.jsonl")
    starts = [s for s in sess if s["event"] == "start"]
    ends = {s["session"]: s for s in sess if s["event"] == "end"}
    F["sessions"] = []
    for i, s in enumerate(starts):
        mine = [x for x in calls if x["session"] == s["session"]]
        e = ends.get(s["session"])
        F["sessions"].append({"session": s["session"], "domain": s["domain"], "start": s["epoch"],
                              "end": e["epoch"] if e else None, "summary": e.get("summary") if e else None,
                              "first_call": min((x["t_start"] for x in mine), default=None),
                              "last_call": max((x["t_end"] for x in mine), default=None),
                              "n_calls": len(mine), "line": sess.index(s) + 1,
                              "server_log_offset": s["server_log_offset"],
                              # the post-hoc pass (full.py _sessions) ends a session's range at its end
                              # record, else at the next session's start offset
                              "server_log_offset_end": (e["server_log_offset"] if e else
                                                        (starts[i + 1]["server_log_offset"] if i + 1 < len(starts) else None))})
    # recording session of each cell = the session of its last call; abandoned episodes
    rec_sess, abandoned = {}, []
    for k, xs in by_cell.items():
        last = max(xs, key=lambda x: x["t_end"])["session"]
        rec_sess[k] = last
        other = [x for x in xs if x["session"] != last]
        if other:
            abandoned.append((k, other[0]["session"], len(other), utc(max(x["t_end"] for x in other))))
    F["rec_session"], F["abandoned"] = rec_sess, sorted(abandoned)
    # each recorded cell's llm_calls equals its calls in the session that recorded it
    F["calls_count_mismatch"] = sorted(
        (k, llm_calls[k], sum(1 for x in by_cell.get(k, []) if x["session"] == rec_sess.get(k)))
        for k in llm_calls if llm_calls[k] != sum(1 for x in by_cell.get(k, []) if x["session"] == rec_sess.get(k)))
    # run_full.log landmarks
    log = (R / "run_full.log").read_text().splitlines()
    F["log_n_lines"] = len(log)
    F["log_starts"] = [(i + 1, ln) for i, ln in enumerate(log) if ln.startswith("O2 full run start")]
    F["log_preflight"] = [(i + 1, ln) for i, ln in enumerate(log) if ln.startswith("[preflight] ok")]
    F["log_domain_headers"] = [(i + 1, ln) for i, ln in enumerate(log) if ln.startswith("=== ")]
    F["log_context_loss"] = [(i + 1, ln) for i, ln in enumerate(log) if "[CONTEXT LOSS]" in ln]
    F["log_complete"] = [(i + 1, ln) for i, ln in enumerate(log) if ln.startswith(("COMPLETE", "INCOMPLETE"))]
    F["log_cell"] = {}
    for i, ln in enumerate(log):
        m = re.match(r"^\[(\w+)\s+(\w+) s0\] (\w+)", ln)
        if m:
            F["log_cell"].setdefault((m.group(1), m.group(2)), []).append(i + 1)
    F["log_cache_warnings"] = sum(1 for ln in log if ln.startswith("[cache WARNING]"))
    F["log_git_fatal"] = [(i + 1) for i, ln in enumerate(log) if ln.startswith("fatal: not a git repository")]
    # run-time configuration from the run's own files
    o2c = (run / "o2common.py").read_text().splitlines()
    F["bounds_line"] = next((i + 1, ln.strip()) for i, ln in enumerate(o2c) if ln.startswith("BOUNDS ="))
    sh = (run / "run_full.sh").read_text().splitlines()
    F["version_lines"] = [(i + 1, ln.strip()) for i, ln in enumerate(sh) if "0.35.0" in ln or "0.33.3" in ln]
    F["version_check"] = (next(i + 1 for i, ln in enumerate(sh) if ln.startswith("v=$(curl")),
                          next(i + 1 for i, ln in enumerate(sh) if ln.strip() == "esac"))
    F["driver_mtimes"] = {n: (run / n).stat().st_mtime for n in ("full.py", "o2common.py", "localguard.py",
                                                                 "run_full.sh", "local_model.patch")}
    F["prov_git_commit"] = {d: json.loads((R / f"results_o2_{d}.provenance.json").read_text()).get("git_commit")
                            for d in DOMAINS}
    F["modelfile"] = (run / "Modelfile.16k").read_text().strip().replace("\n", "; ")
    prov = {d: json.loads((R / f"results_o2_{d}.provenance.json").read_text()) for d in DOMAINS}
    F["prov"] = {d: {"temperature": p.get("temperature"), "bounds": p.get("bounds"), "model": p.get("model"),
                     "network_prompt_version": p.get("network_prompt_version"),
                     "comparator_version": p.get("comparator_version"), "contract_version": p.get("contract_version"),
                     "o2_blocks": [{"session": b["session"], "ollama_version": b["ollama"]["ollama_version"],
                                    "digest": b["ollama"]["digest"], "num_ctx": b["ollama"]["num_ctx"],
                                    "weights": b["ollama"]["weights_blob_sha256"],
                                    "base_weights": b["ollama"]["base_weights_blob_sha256"],
                                    "runner_ctx": (b["ollama"].get("loaded_runner") or {}).get("context_length"),
                                    "after_version": (b.get("ollama_after") or {}).get("ollama_version"),
                                    "after_digest": (b.get("ollama_after") or {}).get("digest"),
                                    "after_runner_ctx": ((b.get("ollama_after") or {}).get("loaded_runner") or {}).get("context_length"),
                                    "host": b.get("host"), "guard_after": b.get("guard_after"),
                                    "patch": b["local_model_patch_sha256"], "driver": b["driver_sha256"]}
                                   for b in p.get("o2_local_model", [])]} for d, p in prov.items()}
    # the code-agent exec watchdog in the run tree
    wd = {}
    for rel in ("evidence/eval_run/tools_code.py", "evidence/eval_run/adapters/code_agent.py"):
        lines = (run / "tree" / rel).read_text().splitlines()
        wd[rel] = [i + 1 for i, ln in enumerate(lines) if "CodeTimeout" in ln or "timeout_s" in ln]
    F["watchdog_lines"] = {k: (min(v), max(v)) for k, v in wd.items() if v}
    # the run location recorded in provenance, and whether a second copy of the results matches this one
    pe = json.loads((R / "results_o2_employee.provenance.json").read_text())
    F["run_location"] = str(Path(pe.get("corpus_path", "")).parent.parent)
    other = Path(F["run_location"]) / "results"
    F["run_location_results_identical"] = (
        None if not other.exists() or other.resolve() == R.resolve() else
        all((other / f"results_o2_{d}.jsonl").exists() and sha256(other / f"results_o2_{d}.jsonl") == sha256(R / f"results_o2_{d}.jsonl")
            for d in DOMAINS))
    # the watchdog cell's loop follows Manager ID upwards from each VP: do those chains cycle?
    import pandas as pd
    reg = pd.read_pickle(run / "tree" / "probe_domain" / "employees.pkl")
    mgr = dict(zip(reg["Employee ID"], reg["Manager ID"]))
    vps = reg.loc[reg["Role"] == "VP", "Employee ID"].tolist()
    cyc = 0
    for v in vps:
        seen, cur = set(), v
        while isinstance(cur, str) and cur in mgr:
            if cur in seen:
                cyc += 1
                break
            seen.add(cur)
            cur = mgr[cur]
    F["vp_chain_cycles"] = (cyc, len(vps))
    return F


# ── 4. Ollama log evidence (saved, so a later run without the logs reuses it) ─
def ollama_evidence(logs: Path, ev_dir: Path, t105: dict, sessions: list | None = None) -> dict:
    """The log lines behind the disclosure facts. Read from ~/.ollama/logs when they are there,
    else from the saved evidence/ollama_evidence.json."""
    saved = ev_dir / "ollama_evidence.json"
    E: dict = {}

    def grab(fname, pattern, key, first=True, flags=0):
        p = logs / fname
        if not p.exists():
            return
        hits = []
        with open(p, errors="replace") as fh:
            for i, ln in enumerate(fh, 1):
                if re.search(pattern, ln, flags):
                    hits.append({"file": str(p), "line": i, "text": ln.rstrip("\n")[:1600]})
                    if first:
                        break
        if hits:
            E[key] = hits
    if logs.exists():
        grab("app-2.log", r'msg="starting Ollama".*version=0\.33\.3', "app2_start_0333")
        grab("app-2.log", r'performing update at startup', "app2_update")
        grab("app-2.log", r'New update available.*v0\.35\.0', "app2_update_url")
        grab("app-1.log", r'msg="starting Ollama".*version=0\.35\.0', "app1_start_0350")
        grab("app-1.log", r'received SIGINT or SIGTERM', "app1_sigterm")
        grab("app.log", r'msg="starting Ollama".*version=0\.35\.0', "app_restart_0350")
        grab("server-3.log", r'Listening on .*version 0\.33\.3', "s3_listen_0333")
        grab("server-3.log", r'starting llama-server.*sha256-1194192cf2a1.* -c 16384', "s3_runner_16k_blob")
        grab("server-1.log", r'msg="server config".*OLLAMA_KEEP_ALIVE:5m0s', "s1_config_keepalive_5m", first=False)
        grab("server-1.log", r'Listening on .*version 0\.35\.0', "s1_listen_0350", first=False)
        grab("server-1.log", r'starting llama-server.*sha256-1194192cf2a1.* -c 16384', "s1_runner_loads", first=False)
        grab("server-1.log", r'msg="system memory"', "s1_system_memory", first=False)
        grab("server.log", r'msg="server config".*OLLAMA_KEEP_ALIVE:2562047h', "s0_config_keepalive_inf")
        grab("server.log", r'Listening on .*version 0\.35\.0', "s0_listen_0350")
        grab("server.log", r'starting llama-server.*sha256-1194192cf2a1.* -c 16384', "s0_runner_load")
        # the t1_05 request: the first /v1/messages task of the new server log
        p = logs / "server.log"
        if p.exists():
            want = t105.get("prompt_tokens")
            lines = []
            with open(p, errors="replace") as fh:
                for i, ln in enumerate(fh, 1):
                    lines.append((i, ln.rstrip("\n")))
                    if i > 5000:
                        break
            task = None
            for i, ln in lines:
                m = re.search(r"\| task (\d+) \| new prompt, n_ctx_slot = (\d+), n_keep = \d+, task\.n_tokens = (\d+)", ln)
                if m and int(m.group(3)) == want and task is None:
                    task = m.group(1)
                    E["t105_new_prompt"] = [{"file": str(p), "line": i, "text": ln[:400]}]
                    continue
                if task is not None:
                    if re.search(rf"\| task {task} \| prompt eval time", ln) or re.search(rf"\| task {task} \|\s+eval time", ln):
                        E.setdefault("t105_timing", []).append({"file": str(p), "line": i, "text": ln[:400]})
                    if re.search(rf"\| task {task} \| stop processing", ln):
                        E["t105_release"] = [{"file": str(p), "line": i, "text": ln[:400]}]
                    if "truncating input prompt" in ln or "context shift" in ln:
                        E.setdefault("t105_loss_lines", []).append({"file": str(p), "line": i, "text": ln[:400]})
                    if 'POST     "/v1/messages"' in ln:
                        E["t105_gin"] = [{"file": str(p), "line": i, "text": ln[:400]}]
                        break
        # loss markers anywhere in the two logs that cover the reported run
        for fname in ("server-1.log", "server.log"):
            p = logs / fname
            if p.exists():
                txt = p.read_text(errors="replace")
                E[f"count_truncating_{fname}"] = txt.count("truncating input prompt")
                E[f"count_context_shift_{fname}"] = txt.count("context shift")
        # the post-hoc whole-session alignment of full.py context_loss(), redone: server tasks in each
        # session's [start, end) log range against the session's calls
        rot = log_epoch(E["app_restart_0350"][0]["text"]) if E.get("app_restart_0350") else None
        if rot and sessions:
            post = []
            for s in sessions:
                fname = "server-1.log" if s["start"] < rot else "server.log"
                a_, b_ = s["server_log_offset"], s["server_log_offset_end"]
                p = logs / fname
                if not p.exists():
                    continue
                if b_ is not None and b_ < a_:
                    n = None
                else:
                    with open(p, "rb") as fh:
                        fh.seek(a_)
                        seg = (fh.read() if b_ is None else fh.read(b_ - a_)).decode(errors="replace")
                    n = len(re.findall(r"new prompt, n_ctx_slot = \d+, n_keep = \d+, task\.n_tokens = \d+", seg))
                post.append({"session": s["session"], "file": fname, "range": [a_, b_], "server_tasks": n,
                             "calls": s["n_calls"]})
            E["posthoc_alignment"] = post
    if E.get("t105_new_prompt"):
        ev_dir.mkdir(parents=True, exist_ok=True)
        saved.write_text(json.dumps(E, indent=1) + "\n")
        with open(ev_dir / "ollama_log_excerpts.txt", "w") as fh:
            fh.write("Ollama log lines behind the O2 disclosure facts (read-only copies; file:line: text).\n"
                     "Times in these logs are BST (UTC+1).\n\n")
            for k, hits in E.items():
                if isinstance(hits, list) and all(isinstance(h, dict) and "line" in h for h in hits):
                    for h in hits:
                        fh.write(f"[{k}] {h['file']}:{h['line']}: {h['text']}\n")
                else:
                    fh.write(f"[{k}] {json.dumps(hits)}\n")
        E["_source"] = f"live logs in {logs} (copied to {saved})"
    elif saved.exists():
        E = json.loads(saved.read_text())
        E["_source"] = f"saved evidence {saved} (the live logs no longer hold the lines)"
    else:
        E["_source"] = "none: neither the live logs nor a saved copy hold the lines"
    return E


# ── 5. Claude side ────────────────────────────────────────────────────────────
def claude_side(path: Path) -> list:
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    idx = {(r["fig_table"], r["domain"], r["system"], r["tier_group"], r["scoring"], r["metric"]): r for r in rows}
    out = []

    def get(ft, dom, s, tg, sc, metric):
        r = idx.get((ft, dom, s, tg, sc, metric))
        if r is None:
            return None
        return int(r["numerator"]), int(r["denominator"]), "|".join((ft, dom, s, tg, sc, metric))

    def put(set_name, metric, s, parts):
        parts = [p for p in parts if p is not None]
        if not parts:
            return
        k, n = sum(p[0] for p in parts), sum(p[1] for p in parts)
        out.append({"set": set_name, "metric": metric, "system": s, "numerator": k, "denominator": n,
                    "pct": round(100 * k / n, 1) if n else "", "source_rows": " + ".join(p[2] for p in parts)})
    am = (("accuracy", "accuracy"), ("silent_error", "silent_wrong"), ("refused_should_answer", "refused_should_answer"),
          ("timeout", "timeout"))
    dm = (("answer_anyway", "silent_wrong"), ("declined", "declined"), ("failed", "failed"), ("timeout", "timeout"))
    for s in SYSTEMS:
        for m, cm in am:
            put("employee_in_contract", m, s, [get("Table2", "employee", s, "in_contract_answer", "strict", cm)])
        for m, cm in dm:
            put("employee_out_of_range", m, s, [get("Table5", "employee", s, OOR, "strict", cm)])
        for sc in ("strict", "tolerant"):
            for m, cm in am:
                put(f"spider_answer_{sc}", m, s, [get("Fig1/Fig2/Table3", "spider_pooled", s, "in_contract_answer", sc, cm)])
                put(f"pooled_within_vocab_{sc}", m, s,
                    [get("Table2", "employee", s, "in_contract_answer", "strict", cm),
                     get("Fig1/Fig2/Table3", "spider_pooled", s, "in_contract_answer", sc, cm)])
        for m, cm in dm:
            put("spider_out_of_scope_register", m, s, [get("Table5", "spider_pooled", s, OOSR, "strict", cm)])
    for m, cm in (("accuracy", "correct"), ("silent_error", "silent_wrong"), ("refused_should_answer", "refused_should_answer")):
        put("spider_out_of_contract_aggregate", m, GDC,
            [get("Fig3/Fig4", d, GDC, OOCA, "strict", cm) for d in SPIDER])
    return out


# ── output helpers ────────────────────────────────────────────────────────────
# Decimals each CSV column is rounded to. The rows keep the unrounded values, so the printed
# figures (summary.txt, O2_RESULTS.md) are rounded once, from them, and the CSVs round once, here.
CSV_DP = {"rate_pct": 2, "ci_lo_pct": 2, "ci_hi_pct": 2, "gdc_pct": 2, "alt_pct": 2, "diff_pp": 2,
          "ci_lo_pp": 2, "ci_hi_pp": 2, "wall_time_s_sum": 1}
RATES_FIELDS = ["set", "rule", "metric", "system", "k", "n_cells", "n_questions", "rate_pct", "ci_lo_pct", "ci_hi_pct",
                "questions_with_any", "n_eff"]


def _csv_value(k, v):
    if isinstance(v, float) and k in CSV_DP:
        v = round(v, CSV_DP[k])
    return f"{v:.6g}" if isinstance(v, float) else v


def write_csv(path: Path, rows: list, fields: list | None = None) -> None:
    if not rows:
        path.write_text("")
        return
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _csv_value(k, v) for k, v in r.items()})


def pct(k, n, dp=1):
    return f"{100 * k / n:.{dp}f}%" if n else "-"


def status_rows(rows: list) -> list:
    """Status counts and bound hits (run-time statuses, all 880 cells), per system and domain."""
    st_rows = []
    for s in SYSTEMS:
        for dom in DOMAINS + ("all",):
            rs = [r for r in rows if r["system"] == s and (dom == "all" or r["_dom"] == dom)]
            st = Counter(r["status"] for r in rs)
            err = [str(r.get("error") or "") for r in rs]
            st_rows.append({"system": s, "domain": dom, "cells": len(rs), "ok_completed": st.get("completed", 0),
                            "refused": st.get("refused", 0), "timeout": st.get("timeout", 0),
                            "crashed": st.get("crashed", 0),
                            "react_max_iters_15_hit": sum(e.startswith("max_iters 15 hit") for e in err),
                            "code_agent_max_retries_12_hit": sum(e.startswith("max_retries 12 hit") for e in err),
                            "code_agent_exec_watchdog_60s": sum("agent code did not return within" in e for e in err),
                            "wall_time_s_sum": sum(r["wall_time_s"] for r in rs),
                            "llm_calls_sum": sum(r.get("llm_calls") or 0 for r in rs)})
    return st_rows


def stat_lines(rates: list, comps: list) -> list:
    """summary.txt: the rate lines, the comparison lines and the resolved comparisons."""
    L = []
    for x in rates:
        if x["metric"] in ("declined_explicit", "declined_typed", "crash") and x["k"] == 0:
            continue
        L.append(f"  {x['set']:<45} {x['metric']:<22} {x['system']:<12} {x['rate_pct']:6.1f}%  "
                 f"[{x['ci_lo_pct']:5.1f}, {x['ci_hi_pct']:5.1f}]  {x['k']}/{x['n_cells']} cells, "
                 f"{x['n_questions']} questions")
    L.append("")
    for x in comps:
        L.append(f"  {x['set']:<45} {x['metric']:<22} GDC-{x['alt']:<12} {x['diff_pp']:+6.1f} pp "
                 f"[{x['ci_lo_pp']:+6.1f}, {x['ci_hi_pp']:+6.1f}]  discordant {x['n_discordant_q']} "
                 f"({x['q_gdc_higher']}/{x['q_alt_higher']})  p_perm {x['p_perm']:.3g} (Holm {x['p_perm_holm']:.3g})"
                 f"  {x['verdict']}{' (survives Holm)' if x['resolved_after_holm'] else ''}")
    L.append("")
    L.append("Resolved comparisons (bootstrap CI excludes 0 and exact permutation p < 0.05):")
    res_c = [x for x in comps if x["verdict"] == "resolved"]
    for x in res_c:
        L.append(f"  {x['set']} {x['metric']} GDC-{x['alt']}: {x['diff_pp']:+.1f} pp [{x['ci_lo_pp']:+.1f}, "
                 f"{x['ci_hi_pp']:+.1f}], p_perm {x['p_perm']:.3g}, Holm {x['p_perm_holm']:.3g}"
                 f"{' (survives Holm)' if x['resolved_after_holm'] else ' (does not survive Holm)'}")
    if not res_c:
        L.append("  (none)")
    return L


STATUS_HEAD = "Status counts, run-time (all 880 cells; ok = completed):"


def status_lines(st_rows: list) -> list:
    """summary.txt: the status-count lines, one per system."""
    L = [STATUS_HEAD]
    for x in st_rows:
        if x["domain"] == "all":
            L.append(f"  {x['system']:<12} ok {x['ok_completed']:>3}  refused {x['refused']:>3}  timeout {x['timeout']:>3}  "
                     f"crashed {x['crashed']:>2}  of {x['cells']}; react 15-iteration bound {x['react_max_iters_15_hit']}, "
                     f"code-agent 12-retry bound {x['code_agent_max_retries_12_hit']}, exec watchdog {x['code_agent_exec_watchdog_60s']}; "
                     f"wall {x['wall_time_s_sum'] / 3600:.2f} h")
    return L


def from_shipped(out: Path, stats: Path, check: bool) -> int:
    """Rewrite the statistics lines and the status-count lines of <out>/summary.txt from the shipped
    files, with the functions the scoring run used: build_sets and analyse on the cells of
    <out>/per_question.csv (domain, qid, system, group, strict, tolerant, d8_class; a cell is scored
    unless its tier is "(dropped)"), status_rows on results/, then stat_lines and status_lines. Refuses
    unless the recomputed rates and comparisons write the shipped rates.csv and comparisons.csv byte
    for byte. Every other line of summary.txt is kept as it is. With check, writes nothing and returns
    1 if summary.txt would change."""
    import tempfile
    import numpy as np                            # noqa: E402
    Q = load_stats(stats)
    Q.np = np
    pq = list(csv.DictReader(open(out / "per_question.csv", newline="", encoding="utf-8")))
    cells = [{"domain": r["domain"], "qid": r["qid"], "system": r["system"], "scored": r["tier"] != "(dropped)",
              "group": r["group"], "strict": r["strict"], "tolerant": r["tolerant"], "d8_class": r["d8_class"]}
             for r in pq]
    rates, comps = [], []
    for name, rule, kind, cs, primary in build_sets(cells):
        r, cp = analyse(Q, name, rule, kind, cs, compare=primary)
        rates += r
        comps += cp
    with tempfile.TemporaryDirectory() as td:
        write_csv(Path(td) / "rates.csv", rates, RATES_FIELDS)
        write_csv(Path(td) / "comparisons.csv", comps)
        for f in ("rates.csv", "comparisons.csv"):
            if (Path(td) / f).read_bytes() != (out / f).read_bytes():
                raise SystemExit(f"--from-shipped: the recomputed {f} differs from {out / f}; summary.txt not touched")
    st_rows = status_rows(load_o2(HERE))
    old = (out / "summary.txt").read_text(encoding="utf-8").split("\n")
    i0 = old.index("") + 1                                     # the blank line after the four header lines
    i1 = next(i for i, ln in enumerate(old) if ln.startswith("Re-score against the run-time verdict")) - 1
    j0 = old.index(STATUS_HEAD)
    j1 = j0 + len(status_lines(st_rows))
    if old[i1] != "" or old[j1] != "":
        raise SystemExit("--from-shipped: summary.txt does not have the layout analyse_o2.py writes")
    new = old[:i0] + stat_lines(rates, comps) + old[i1:j0] + status_lines(st_rows) + old[j1:]
    changed = [(n + 1, a, b) for n, (a, b) in enumerate(zip(old, new)) if a != b]
    if len(new) != len(old):
        raise SystemExit(f"--from-shipped: line count would change ({len(old)} -> {len(new)})")
    for n, a, b in changed:
        print(f"summary.txt line {n}:\n  - {a.strip()}\n  + {b.strip()}")
    print(f"{len(changed)} line(s) of summary.txt {'differ' if check else 'rewritten'}; rates.csv and "
          f"comparisons.csv recomputed byte for byte")
    if check:
        return 1 if changed else 0
    (out / "summary.txt").write_text("\n".join(new), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", type=Path, default=D_TREE)
    ap.add_argument("--run", type=Path, default=D_RUN)
    ap.add_argument("--stats", type=Path, default=D_STATS)
    ap.add_argument("--out", type=Path, default=None, help="output directory (default: this directory; with --from-shipped, analysis/)")
    ap.add_argument("--claude-csv", type=Path, default=None)
    ap.add_argument("--ollama-logs", type=Path, default=Path.home() / ".ollama" / "logs")
    ap.add_argument("--cwd", type=Path, default=D_CWD, help="scratch working directory for the tree imports")
    ap.add_argument("--from-shipped", action="store_true",
                    help="rewrite the statistics and status-count lines of <out>/summary.txt from the shipped files")
    ap.add_argument("--check", action="store_true", help="with --from-shipped: compare only, exit 1 on a difference")
    a = ap.parse_args(argv)
    if a.from_shipped:
        return from_shipped(a.out or HERE / "analysis", a.stats, a.check)
    install_guards()
    a.cwd.mkdir(parents=True, exist_ok=True)
    os.chdir(a.cwd)                              # any relative-path write by a tree import lands here
    T = Tree(a.tree.resolve())
    Q = load_stats(a.stats)
    import numpy as np                            # noqa: E402
    Q.np = np
    out = a.out or HERE
    out.mkdir(parents=True, exist_ok=True)

    # engine check
    eh_tree, eh_run = T.po.engine_hashes(a.tree), T.po.engine_hashes(a.run / "tree")
    eng_diff = sorted(f for f in set(eh_tree) | set(eh_run) if eh_tree.get(f) != eh_run.get(f))
    replay_all = bool(eng_diff)
    rows = load_o2(a.run)
    # the run's own checksum file, written when the durable copy was made
    sums_p = a.run.parent / "results_sha256.txt"
    sums = {}
    if sums_p.exists():
        for ln in sums_p.read_text().splitlines():
            if ln.strip():
                h, n = ln.split(None, 1)
                sums[n.strip()] = (h, sha256(a.run / "results" / n.strip()) == h)
    cells = score_cells(T, rows, replay_all)
    run_tree_gold = {d: json.loads((a.run / "tree" / "evidence" / "gold" /
                                    ("gold_employee.json" if d == "employee" else f"gold_spider_{d}.json")).read_text())
                     for d in DOMAINS}

    # re-score vs run-time verdict
    mismatches = []
    for c in cells:
        if not c["scored"]:
            mismatches.append({**{k: c[k] for k in ("domain", "qid", "system", "raw_tier")}, "tier_of_record": "",
                               "verdict_runtime": c["verdict_runtime"], "strict_rescore": "(not scored)",
                               "tolerant_rescore": "", "cause": c["drop_reason"]})
            continue
        if c["strict"] != c["verdict_runtime"]:
            mismatches.append({**{k: c[k] for k in ("domain", "qid", "system", "raw_tier")}, "tier_of_record": c["tier"],
                               "verdict_runtime": c["verdict_runtime"], "strict_rescore": c["strict"],
                               "tolerant_rescore": c["tolerant"], "cause": explain_mismatch(T, run_tree_gold, c)})
    n_verdict_mismatch = sum(1 for m in mismatches if m["strict_rescore"] != "(not scored)")
    tie_moves = [c for c in cells if c["scored"] and c["tolerant"] != c["strict"]]
    retiered = sorted({(c["domain"], c["qid"], c["raw_tier"], c["tier"]) for c in cells
                       if c["scored"] and c["raw_tier"] != c["tier"]})
    rep = [c for c in cells if c["scored"] and c["system"] in T.pi.REPLAYED]
    rep_done = [c for c in rep if "replay_equal_recorded" in c]
    rep_neq = [c for c in rep_done if not c["replay_equal_recorded"]]
    rep_flip = [c for c in rep if c["strict"] != c["strict_on_recorded"]]

    # statistics
    sets = build_sets(cells)
    rates, comps = [], []
    for name, rule, kind, cs, primary in sets:
        r, cp = analyse(Q, name, rule, kind, cs, compare=primary)
        rates += r
        comps += cp
    rate_of = {(x["set"], x["metric"], x["system"]): x for x in rates}

    # per-question rows (one seed: one row per cell); heldout's columns first
    pq = []
    for c in cells:
        v = c.get("tolerant") or ""
        pq.append({"domain": c["domain"], "qid": c["qid"], "tier": c.get("tier") or "(dropped)", "system": c["system"],
                   **{k: int(v == k) for k in ("correct", "silent_wrong", "refused_should_answer", "crash", "timeout")},
                   "raw_tier": c["raw_tier"], "group": c["group"], "strict": c.get("strict", ""),
                   "tolerant": c.get("tolerant", ""), "d8_class": c.get("d8_class", ""),
                   "decline_channel": c.get("decline_channel", ""), "decline_detail": c.get("decline_detail", ""),
                   "verdict_runtime": c["verdict_runtime"], "status_recorded": c["status_recorded"],
                   "status_scored": c.get("status_scored", ""), "scored_from": c.get("scored_from", ""),
                   "replay_equal_recorded": c.get("replay_equal_recorded", ""),
                   "gdc_goal_emitted": c["has_goal"] if c["system"] == GDC else "",
                   "llm_calls": c["llm_calls"], "wall_time_s": c["wall_time_s"], "error": c["error"]})

    st_rows = status_rows(rows)
    watchdog_cells = [r for r in rows if "agent code did not return within" in str(r.get("error") or "")]

    F = run_facts(a.run, rows)
    calls = F["calls"]
    # t1_05 (text_to_sql, employee): the flagged cell
    k105 = ("employee", "text_to_sql", "t1_05")
    c105 = F["cc_records"].get(k105, {})
    calls105 = F["calls_by_cell"].get(k105, [])
    t105 = {"prompt_tokens": sum((x.get("in") or 0) + (x.get("cache_read") or 0) for x in calls105[:1]),
            "out": calls105[0].get("out") if calls105 else None}
    E = ollama_evidence(a.ollama_logs, out / "evidence", t105, F["sessions"])
    sessions = F["sessions"]
    s_by = {s["session"]: s for s in sessions}

    # wall time per system, sessions, slow phase after the watchdog
    wall = {s: sum(r["wall_time_s"] for r in rows if r["system"] == s) for s in SYSTEMS}
    callsec = {s: sum(x["latency_s"] for x in calls if x["system"] == s) for s in SYSTEMS}
    wd_end = None
    if watchdog_cells:
        w = watchdog_cells[0]
        xs = F["calls_by_cell"].get((w["_dom"], w["system"], w["qid"]), [])
        wd_end = max(x["t_end"] for x in xs) + 60.0 if xs else None
        wd_session = max(xs, key=lambda x: x["t_end"])["session"] if xs else None
    slow = []
    if wd_end:
        for r in rows:
            k = (r["_dom"], r["system"], r["qid"])
            xs = F["calls_by_cell"].get(k, [])
            if xs and F["rec_session"].get(k) == wd_session and min(x["t_start"] for x in xs) > wd_end - 60:
                slow.append((k, r["wall_time_s"]))
    run_start = sessions[0]["start"]
    run_end = max(s["end"] or s["last_call"] or 0 for s in sessions)
    # text-to-SQL employee wall times: the slow session against the next one
    tts_wall = defaultdict(list)
    for r in rows:
        if r["_dom"] == "employee" and r["system"] == "text_to_sql":
            tts_wall[F["rec_session"].get(("employee", "text_to_sql", r["qid"]))].append(r["wall_time_s"])
    # model loads in the slow session (Ollama keep-alive 5 min expired between calls)
    s_slow = s_by.get(wd_session) if wd_end else None
    loads_slow = [h for h in E.get("s1_runner_loads", []) if s_slow and log_epoch(h["text"])
                  and s_slow["start"] <= log_epoch(h["text"]) <= (s_slow["last_call"] or 0)]
    mem_slow = [h for h in E.get("s1_system_memory", []) if s_slow and log_epoch(h["text"])
                and s_slow["start"] <= log_epoch(h["text"]) <= (s_slow["last_call"] or 0)]
    # inter-call gaps and client-side call latency, per session (and before/after the watchdog)
    import statistics as _st
    gapstat = []
    for s in sessions:
        cs_ = sorted((x for x in calls if x["session"] == s["session"]), key=lambda x: x["t_start"])
        phases = ([("before the watchdog", lambda x: x["t_start"] < wd_end - 60),
                   ("after the watchdog", lambda x: x["t_start"] >= wd_end - 60)]
                  if wd_end and s["session"] == wd_session else [("whole session", lambda x: True)])
        for label, sel in phases:
            sub = [x for x in cs_ if sel(x)]
            g = [b["t_start"] - a["t_end"] for a, b in zip(sub, sub[1:])]
            if g:
                gapstat.append({"session": s["session"], "domain": s["domain"], "phase": label, "calls": len(sub),
                                "median_gap_s": round(_st.median(g), 2), "max_gap_s": round(max(g), 1),
                                "median_latency_s": round(_st.median(x["latency_s"] for x in sub), 2)})
    wall_slow = Counter()
    for (k, w) in slow:
        wall_slow[k[1]] += w
    same_label_gold_changed = [c for c in cells if c["scored"] and (c["domain"], c["qid"]) == ("wta_1", "wta_26")
                               and c["strict"] == c["verdict_runtime"]]
    d8_detail = Counter((c["domain"] if c["domain"] == "employee" else "spider", c["system"], c["d8_class"],
                         c["decline_detail"]) for c in cells if c["scored"] and c.get("d8_class", "").startswith("declined_"))

    # the earlier chat figures, reproduced and checked
    chat = {}
    allc = [c for c in cells]
    for s in SYSTEMS:
        cs = [c for c in allc if c["system"] == s]
        chat[s] = {"runtime_silent_all": sum(c["verdict_runtime"] == "silent_wrong" for c in cs), "n_all": len(cs),
                   "runtime_timeout_all": sum(c["verdict_runtime"] == "timeout" for c in cs)}
    fiv = [c for c in cells if c["scored"] and ((c["domain"] == "employee" and c["group"] == "in_contract")
                                                 or (c["domain"] in SPIDER and c["group"] == "answer"))]
    g_fiv = [c for c in fiv if c["system"] == GDC]
    g_ref = [c for c in g_fiv if c["strict"] == "refused_should_answer"]
    g_ch = Counter((c["decline_channel"], c["decline_detail"]) for c in g_ref)
    g_goal_ref = [c for c in g_ref if c["has_goal"]]
    g_nogoal_ref = [c for c in g_ref if not c["has_goal"]]
    g_sil = [c for c in g_fiv if c["strict"] == "silent_wrong"]
    g_sil_tol = [c for c in g_fiv if c["tolerant"] == "silent_wrong"]
    g_goal = [c for c in g_fiv if c["has_goal"]]
    g_goal_ok = [c for c in g_goal if c["strict"] == "correct"]
    ch_nogoal = Counter((c["decline_channel"], c["decline_detail"]) for c in g_nogoal_ref)
    ch_goal = Counter(c["decline_detail"] for c in g_goal_ref)
    parse_msgs = Counter(re.sub(r"^ParseError:\s*", "", c["error"])[:70] for c in g_nogoal_ref)
    g_all_parse = Counter((c["domain"], c["group"]) for c in cells if c["system"] == GDC and c["scored"]
                          and not c["has_goal"] and c["error"].startswith("ParseError"))

    # D7 sensitivity: wta_26 scored against the run tree's (pre-D7) gold
    d7 = {}
    old26 = run_tree_gold["wta_1"].get("wta_26")
    for r in rows:
        if r["_dom"] == "wta_1" and r["qid"] == "wta_26":
            res, status = T.pi.scored_input("wta_1", r)
            d7[r["system"]] = {"pre_D7": T.score(res, old26, status=status)[0],
                               "D7": next(c["strict"] for c in cells if c["domain"] == "wta_1" and c["qid"] == "wta_26"
                                          and c["system"] == r["system"]),
                               "d8_pre_D7": None}
            if T.score(res, old26, status=status)[0] == "correct":
                ch = T.pi.decline_channel("wta_1", r)
                d7[r["system"]]["d8_pre_D7"] = "failed" if ch and ch[0] == "failed" else f"declined_{ch[0]}" if ch else None
    # D6 disclosure: employee limit ties
    ties_emp = {q: t for q, t in T.ties_syn["ties"].get("employee", {}).items() if q in T.pi.GOLD["employee"]}
    d6 = {}
    for s in SYSTEMS:
        tot = Counter()
        for q, t in sorted(ties_emp.items()):
            for r in rows:
                if r["_dom"] == "employee" and r["system"] == s and r["qid"] == q:
                    res, status = T.pi.scored_input("employee", r)
                    g = T.pi.GOLD["employee"][q]
                    if T.score(res, g, status=status)[0] == "correct":
                        tot["correct"] += 1
                    elif any(T.score(res, {**g, "value": v}, status=status)[0] == "correct" for v in t["alt_values"]):
                        tot["tie_alternative"] += 1
                    tot["n"] += 1
        d6[s] = dict(tot)

    # Claude side
    ccsv = a.claude_csv or (D_CLAUDE if D_CLAUDE.exists() else D_CLAUDE_FALLBACK)
    cside = claude_side(ccsv)
    c_of = {(x["set"], x["metric"], x["system"]): x for x in cside}
    claude_label = ("batch 2 included, m2 final build" if ccsv.name == "paper_numbers.final.csv"
                    else "pre-batch-2 baselines (decisions build)")

    # ── write CSVs ──
    write_csv(out / "rates.csv", rates, RATES_FIELDS)
    write_csv(out / "comparisons.csv", comps)
    write_csv(out / "per_question.csv", pq)
    write_csv(out / "mismatches.csv", mismatches)
    write_csv(out / "status_counts.csv", st_rows)
    write_csv(out / "claude_side.csv", cside)

    # ── summary.txt (heldout layout) ──
    n_scored = sum(c["scored"] for c in cells)
    L = [f"O2 analysis (comparator {T.comparator}, tie-tolerant {T.scorer}; ties {sorted(T.ties)}); "
         f"{len(cells)} cells, {n_scored} scored ({len(cells) - n_scored} dropped by D11: employee oor_03); "
         f"{n_verdict_mismatch} scored cell(s) whose strict re-score differs from the run-time verdict.",
         f"Model qwen3-coder-16k:30b-a3b-q4_K_M (Ollama alias of qwen3-coder:30b-a3b-q4_K_M, num_ctx 16384), "
         f"temperature 0, seed 0, S40-off, GDC prompt s48. Scoring tree {a.tree} (engine "
         f"{T.po.engine_sha(eh_tree)[:12]}); run tree engine {T.po.engine_sha(eh_run)[:12]}.",
         f"Engine hashes {'DIFFER in ' + ', '.join(eng_diff) + ': every GDC and planner cell replayed on the scoring tree' if eng_diff else 'agree: recorded GDC/planner results scored, a sample replayed'}; "
         f"replayed {len(rep_done)} of {len(rep)} scored GDC/planner cells, {len(rep_done) - len(rep_neq)} equal to the "
         f"recorded result and status, {len(rep_neq)} different; strict verdict on the replay differs from the "
         f"strict verdict on the recorded result in {len(rep_flip)}.",
         "Intervals: question-cluster bootstrap, 10,000 resamples, stratified by database, percentile 95%; one seed, so "
         "a cell is a question. D8 classes on the decline tiers (declined = explicit + typed; failed separate).", ""]
    L += stat_lines(rates, comps)
    L.append("")
    L.append("Re-score against the run-time verdict (run-time: s48 on the run tree's gold, raw tiers):")
    for m in mismatches:
        L.append(f"  {m['domain']:<8} {m['qid']:<9} {m['system']:<12} {m['raw_tier']} -> {m['tier_of_record'] or '-'}: "
                 f"run-time {m['verdict_runtime']} -> re-score {m['strict_rescore']}  [{m['cause']}]")
    L.append(f"  tier of record differs from the run-time tier: {retiered}")
    L.append(f"  tolerant differs from strict: {[(c['domain'], c['qid'], c['system'], c['strict'], c['tolerant']) for c in tie_moves]}")
    if rep_neq:
        L.append("  replay != recorded: " + "; ".join(f"{c['domain']}/{c['qid']}/{c['system']}" for c in rep_neq))
    L.append("")
    L += status_lines(st_rows)
    L.append("")
    L.append(f"Context-loss flags: {len(F['cc_flagged'])} of {F['cc_n']} cells flagged {F['cc_flagged']}; with a loss event "
             f"{F['cc_events']}; unaligned {F['cc_unaligned']}; n_ctx seen {F['cc_n_ctx']}; temps {F['cc_temps']}; "
             f"max prompt+reply by system {F['cc_max_prompt_plus_out']}.")
    L.append(f"D6 employee limit ties {sorted(ties_emp)}: " + ", ".join(
        f"{s} correct {d6[s].get('correct', 0)} tie_alternative {d6[s].get('tie_alternative', 0)} of {d6[s].get('n', 0)}"
        for s in SYSTEMS))
    L.append(f"D7 sensitivity (wta_26 under the run tree's pre-D7 gold vs D7): {d7}")
    L.append(f"Sessions: " + "; ".join(f"{s['session']} {s['domain']} {utc(s['start'])} -> "
                                        f"{utc(s['end']) if s['end'] else 'no end record, last call ' + utc(s['last_call'])}"
                                        for s in sessions))
    L.append(f"Episodes cut by a stopped session and re-run: {F['abandoned']}; recorded cells whose llm_calls differ "
             f"from their calls in the recording session: {F['calls_count_mismatch']}; cells with no call record: "
             f"{F['cells_without_calls']}")
    L.append(f"Exec watchdog: {[(r['_dom'], r['qid'], r['error']) for r in watchdog_cells]}; fired ~"
             f"{utc(wd_end) if wd_end else '-'}; slow-session cells after it: {len(slow)}, wall "
             f"{sum(w for _, w in slow):.0f} s ({dict(wall_slow)}); gaps/latency: {gapstat}")
    mem_free = [m.group(1) for h in mem_slow for m in [re.search(r'free="([^"]+)"', h["text"])] if m]
    L.append(f"Model reloads in the slow session: {[(Path(h['file']).name, h['line']) for h in loads_slow]}; "
             f"system memory free then: {mem_free}")
    L.append(f"Wall time per system (s): {({s: round(v, 1) for s, v in wall.items()})}; total wall clock "
             f"{utc(run_start)} -> {utc(run_end)} = {hms(run_end - run_start)}")
    L.append(f"Claude side: {ccsv} ({claude_label}), sha256 {sha256(ccsv)[:16]}")
    L.append(f"Ollama evidence: {E.get('_source')}")
    L.append(f"dotenv calls blocked: {len(BLOCKED)}")
    (out / "summary.txt").write_text("\n".join(L) + "\n")

    # ── O2_RESULTS.md ──
    md = render_md(locals())
    (out / "O2_RESULTS.md").write_text(md)
    print("\n".join(L[:6]))
    print(f"wrote {out}/rates.csv comparisons.csv per_question.csv mismatches.csv status_counts.csv claude_side.csv "
          f"summary.txt O2_RESULTS.md")
    return 0


# ── O2_RESULTS.md ─────────────────────────────────────────────────────────────
def render_md(V: dict) -> str:
    T, F, E, rates, comps = V["T"], V["F"], V["E"], V["rates"], V["comps"]
    rate_of, c_of = V["rate_of"], V["c_of"]
    rows, cells, sessions = V["rows"], V["cells"], V["sessions"]
    names = {"network": "GDC", "planner": "planner", "react": "ReAct", "code_agent": "code agent",
             "text_to_sql": "text-to-SQL"}

    def cell(set_name, metric, s):
        x = rate_of.get((set_name, metric, s))
        if not x:
            return "-"
        return f"{x['rate_pct']:.1f} [{x['ci_lo_pct']:.1f}, {x['ci_hi_pct']:.1f}] ({x['k']}/{x['n_cells']})"

    def ccell(set_name, metric, s):
        x = c_of.get((set_name, metric, s))
        return f"{x['pct']:.1f} ({x['numerator']}/{x['denominator']})" if x else "-"

    def table(title, o2set, cset, metrics, note=""):
        out = [f"**{title}**", "", "| system | " + " | ".join(f"O2 {m}" for m, _ in metrics) + " | "
               + " | ".join(f"Claude {m}" for m, _ in metrics) + " |",
               "|---|" + "---|" * (2 * len(metrics))]
        for s in SYSTEMS:
            out.append(f"| {names[s]} | " + " | ".join(cell(o2set, m, s) for m, _ in metrics) + " | "
                       + " | ".join(ccell(cset, cm, s) if cset else "-" for _, cm in metrics) + " |")
        if note:
            out += ["", note]
        return "\n".join(out) + "\n"

    s_by = V["s_by"]

    def ev_line(x):
        return f"`{Path(x['file']).name}:{x['line']}`"

    def ev(key, i=0):
        h = E.get(key)
        if not h or not isinstance(h, list):
            return f"(no line found for {key})"
        return ev_line(h[i])

    am = [("accuracy", "accuracy"), ("silent_error", "silent_error"), ("refused_should_answer", "refused_should_answer")]
    dm = [("answer_anyway", "answer_anyway"), ("declined", "declined"), ("failed", "failed")]
    L = ["# O2: the released code on an open-weights model", "",
         f"Generated by `analyse_o2.py` on {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
         "Every O2 number below comes from `rates.csv`, `comparisons.csv`, `per_question.csv`, `status_counts.csv` "
         "or `summary.txt` in this folder; every Claude number from `claude_side.csv`, which names its CSV rows. "
         "Nothing here has gone into a paper file.", ""]
    # headline
    hl = [("o2_pooled_within_vocab_strict", "pooled_within_vocab_strict", "accuracy", "in scope, within the vocabulary (f, strict): accuracy"),
          ("o2_pooled_within_vocab_strict", "pooled_within_vocab_strict", "silent_error", "in scope, within the vocabulary (f, strict): silent error"),
          ("o2_pooled_within_vocab_strict", "pooled_within_vocab_strict", "refused_should_answer", "in scope, within the vocabulary (f, strict): refused"),
          ("o2_spider_out_of_contract_aggregate", "spider_out_of_contract_aggregate", "silent_error", "Spider beyond the vocabulary (d): silent error"),
          ("o2_spider_out_of_scope_register", "spider_out_of_scope_register", "answer_anyway", "Spider out_of_scope_register (e): answer anyway"),
          ("o2_spider_out_of_scope_register", "spider_out_of_scope_register", "declined", "Spider out_of_scope_register (e): declined (D8)"),
          ("o2_employee_out_of_range", "employee_out_of_range", "answer_anyway", "employee out_of_range (b): answer anyway"),
          ("o2_employee_out_of_range", "employee_out_of_range", "declined", "employee out_of_range (b): declined (D8)")]

    def mark(set_name, metric, s):
        x = next((c for c in comps if c["set"] == set_name and c["metric"] == metric and c["alt"] == s), None)
        if not x:
            return ""
        return " R*" if x["resolved_after_holm"] else (" R" if x["verdict"] == "resolved" else "")
    L += ["## Headline", "",
          "O2 rate % [95% CI] (k/n) per system; R = GDC minus that system resolved (CI excludes 0 and exact "
          "permutation p < 0.05), R* = also after Holm. Claude rate % (k/n) underneath, from the CSV named below.", "",
          "| set and metric | " + " | ".join(names[s] for s in SYSTEMS) + " |", "|---|" + "---|" * len(SYSTEMS)]
    for o2s, cs_, m, label in hl:
        L.append(f"| {label} | " + " | ".join(cell(o2s, m, s) + (mark(o2s, m, s) if s != GDC else "") for s in SYSTEMS) + " |")
        L.append("| Claude | " + " | ".join(ccell(cs_, m, s) for s in SYSTEMS) + " |")
    L += ["", "## Scope and scoring", "",
         "- Model: `qwen3-coder:30b-a3b-q4_K_M` through the Ollama alias `qwen3-coder-16k:30b-a3b-q4_K_M` "
         f"(Modelfile: `{F['modelfile']}`), temperature 0, seed 0, all five systems, the shipped prompts of the current "
         "code (GDC prompt s48, not the s45 prompt behind the paper's Spider GDC numbers), S40-off (no refuse or decline "
         "toggle), bounds react 15 iterations, code agent 12 retries, 60 s per-query timeout.",
         "- Cells: employee 91 questions, wta_1 30, world_1 30, bike_1 25; 880 cells, all present once. D11 drops "
         f"employee oor_03 (5 cells), so {sum(c['scored'] for c in cells)} cells are scored.",
         f"- Scoring tree `{V['a'].tree}`: `git archive 5bf429e` plus `all_decisions_vs_5bf429e.patch` "
         "(sha256 b6f2a156c670...). Comparator s48 strict, s48t tie-tolerant on Spider with `reports/tie_alternatives.json` "
         f"({', '.join(sorted(T.ties))}); tier of record from the gold (`paper_inputs.retier`); D7 (wta_26 moves to "
         "out_of_contract_aggregate with an answer gold), D8 (declined = explicit or typed; failed separate; "
         "`paper_inputs.decline_channel` and the builder's own `oos_class`), D11 applied.",
         f"- Engine: `parity_overlay.engine_hashes` of the scoring tree ({T.po.engine_sha(V['eh_tree'])[:12]}) and of the "
         f"run tree ({T.po.engine_sha(V['eh_run'])[:12]}) differ in {', '.join('`' + f + '`' for f in V['eng_diff']) or 'nothing'} "
         "(the code-agent exec watchdog of `local_model.patch`). Neither file is on the GDC or planner path, but the rule is "
         f"to replay all when the hashes differ: all {len(V['rep_done'])} scored GDC and planner cells were replayed on the "
         f"scoring tree; {len(V['rep_done']) - len(V['rep_neq'])} reproduce the recorded result and status exactly, "
         f"{len(V['rep_neq'])} do not, and the strict verdict on the replay differs from the verdict on the recorded "
         f"result in {len(V['rep_flip'])}.",
         f"- Re-score against the run-time verdict: {V['n_verdict_mismatch']} scored cells differ; every one is listed in "
         "`mismatches.csv` and in the table below. The five oor_03 cells are listed there as not scored.",
         "- Intervals: question-cluster bootstrap, 10,000 resamples (`question_clustered_stats.boot_index`), stratified by "
         "database, percentile 95%, seed 20261001 + crc32(set). Paired GDC-minus-alternative differences: exact sign-flip "
         "permutation test and Holm over the four alternatives (`permutation_p`, `holm` from question_clustered_stats.py). "
         "Resolved = interval excludes 0 and permutation p < 0.05. One seed, so a cell is a question.", ""]
    # mismatches
    L += ["| domain | qid | system | tier (run, record) | run-time | re-score (strict) | cause |", "|---|---|---|---|---|---|---|"]
    for m in V["mismatches"]:
        L.append(f"| {m['domain']} | {m['qid']} | {m['system']} | {m['raw_tier']}, {m['tier_of_record'] or '-'} | "
                 f"{m['verdict_runtime']} | {m['strict_rescore']} | {m['cause']} |")
    L += ["", "Same verdict label, different gold (D7, not a mismatch): "
          + (", ".join(f"wta_26 {c['system']} {c['strict']}" for c in V["same_label_gold_changed"]) or "none")
          + "; at run time a silent_wrong there meant an answer to a decline question, now it means a wrong answer to "
          "an answerable one."]
    L += ["", f"Tier of record differs from the run-time tier (no verdict effect unless listed above): "
          + ", ".join(f"{d}/{q} {a} -> {b}" for d, q, a, b in V["retiered"]) + ".",
          f"Tie-tolerant differs from strict in {len(V['tie_moves'])} cell(s): "
          + (", ".join(f"{c['domain']}/{c['qid']} {c['system']} ({c['strict']} -> {c['tolerant']})" for c in V["tie_moves"]) or "none")
          + ".", ""]
    # tables
    L += ["## Results, O2 beside Claude", "",
          f"O2 cells: rate % [95% CI] (k/n), one seed. Claude cells: rate % (k/n) from `{V['ccsv'].name}` "
          f"({V['claude_label']}); Claude has 3 seeds per employee question and 5 per Spider question, so its n counts "
          "cells, and the Claude GDC Spider rows are the s45 Phase R run (S40-off). The paper's CSV holds no interval for the "
          "Claude sets; question_clustered_stats.py gives them.", ""]
    L.append(table("(a) Employee in-contract (T1-T5, house_vocab; 81 questions)", "o2_employee_in_contract",
                   "employee_in_contract", am))
    L.append(table("(b) Employee out_of_range (9 questions after D11)", "o2_employee_out_of_range",
                   "employee_out_of_range", dm,
                   "answer_anyway = silent_wrong on a decline tier; declined = explicit or typed (D8); failed = a refusal "
                   "that is neither (an SQL error, a GDC ParseError, an unparseable plan with no decline text, an empty result)."))
    L.append(table("(c1) Spider answer tier, tie-tolerant s48t (27 questions)", "o2_spider_answer_tolerant",
                   "spider_answer_tolerant", am))
    L.append(table("(c2) Spider answer tier, strict s48 (27 questions)", "o2_spider_answer_strict",
                   "spider_answer_strict", am))
    L.append(table("(d) Spider out_of_contract_aggregate, beyond the vocabulary (13 questions after D7; strict = tolerant)",
                   "o2_spider_out_of_contract_aggregate", "spider_out_of_contract_aggregate", am,
                   "All five systems ran this tier in O2. The Claude runs hold it for GDC only (Fig3/Fig4 rows summed over the "
                   "three databases; 65 cells); the baselines have no rows for it in the CSV."))
    L.append(table("(e) Spider out_of_scope_register (45 questions after D7)", "o2_spider_out_of_scope_register",
                   "spider_out_of_scope_register", dm))
    L.append(table("(f1) Pooled in-scope within the vocabulary, tolerant (employee in-contract + Spider answer; 108 questions)",
                   "o2_pooled_within_vocab_tolerant", "pooled_within_vocab_tolerant", am,
                   "Claude (f) is derived: the employee Table2 row plus the Spider Fig1/Fig2/Table3 row (numerators and "
                   "denominators summed; `claude_side.csv` names both rows). It weights cells, not questions."))
    L.append(table("(f2) Pooled in-scope within the vocabulary, strict", "o2_pooled_within_vocab_strict",
                   "pooled_within_vocab_strict", am))
    # status counts
    L += ["**(g) Run-time status counts, all 880 cells (ok = completed), and bound hits**", "",
          "| system | ok | refused | timeout | crashed | react 15-iteration bound | code-agent 12-retry bound | exec watchdog (60 s) | wall time (h) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for x in V["st_rows"]:
        if x["domain"] == "all":
            L.append(f"| {names[x['system']]} | {x['ok_completed']} | {x['refused']} | {x['timeout']} | {x['crashed']} | "
                     f"{x['react_max_iters_15_hit']} | {x['code_agent_max_retries_12_hit']} | {x['code_agent_exec_watchdog_60s']} | "
                     f"{x['wall_time_s_sum'] / 3600:.2f} |")
    L += ["", "Per-domain counts are in `status_counts.csv`. A react `timeout` is the 15-iteration bound; react has no "
          "wall-clock bound. A code-agent `timeout` is the 12-retry bound or the exec watchdog.", ""]
    # comparisons
    L += ["## Paired comparisons (GDC minus alternative)", "",
          "| set | metric | vs | diff pp [95% CI] | discordant (GDC higher/alt higher) | p_perm | Holm | verdict |",
          "|---|---|---|---|---|---|---|---|"]
    for x in comps:
        L.append(f"| {x['set']} | {x['metric']} | {x['alt']} | {x['diff_pp']:+.1f} [{x['ci_lo_pp']:+.1f}, {x['ci_hi_pp']:+.1f}] | "
                 f"{x['n_discordant_q']} ({x['q_gdc_higher']}/{x['q_alt_higher']}) | {x['p_perm']:.3g} | {x['p_perm_holm']:.3g} | "
                 f"{x['verdict']}{' (survives Holm)' if x['resolved_after_holm'] else ''} |")
    res_c = [x for x in comps if x["verdict"] == "resolved"]
    L += ["", "Resolved: " + ("; ".join(
        f"{x['set']} {x['metric']} vs {x['alt']} {x['diff_pp']:+.1f} pp [{x['ci_lo_pp']:+.1f}, {x['ci_hi_pp']:+.1f}], "
        f"p_perm {x['p_perm']:.3g}, Holm {x['p_perm_holm']:.3g}{'' if x['resolved_after_holm'] else ' (not after Holm)'}"
        for x in res_c) or "none") + ".", ""]
    # GDC refusals on the in-vocabulary answerable set
    L += ["## GDC's refusals on answerable questions within the vocabulary (set f, strict)", "",
          f"GDC answered {sum(1 for c in V['g_fiv'] if c['strict'] == 'correct')} of {len(V['g_fiv'])} correctly, "
          f"refused {len(V['g_ref'])} and gave {len(V['g_sil'])} silent errors (strict; {len(V['g_sil_tol'])} tolerant). "
          f"Of the {len(V['g_ref'])} refusals, {len(V['g_nogoal_ref'])} had no Goal at all (D8 channel/detail: "
          + ", ".join(f"{a}/{b} {n}" for (a, b), n in sorted(V["ch_nogoal"].items()))
          + "; ParseError messages: " + ", ".join(f"'{m}' {n}" for m, n in V["parse_msgs"].most_common())
          + f") and {len(V['g_goal_ref'])} were emitted Goals the engine refused ("
          + ", ".join(f"{b} {n}" for b, n in sorted(V["ch_goal"].items())) + "). "
          f"GDC emitted a Goal on {len(V['g_goal'])} of the {len(V['g_fiv'])} questions; {len(V['g_goal_ok'])} of those were "
          f"correct, {len(V['g_goal_ref'])} were refused by a check and "
          f"{sum(1 for c in V['g_sil'] if c['has_goal'])} reached the user as silent errors. GDC ParseErrors on every "
          "scored tier: " + ", ".join(f"{d} {g} {n}" for (d, g), n in sorted(V["g_all_parse"].items())) + ".", ""]
    # context loss
    k105, c105, calls105, t105 = V["k105"], V["c105"], V["calls105"], V["t105"]
    x105 = calls105[0] if calls105 else {}
    L += ["## Context loss", "",
          f"`cell_context_<domain>.jsonl` holds one record per completed cell ({F['cc_n']} records, none missing: "
          f"{not F['cc_missing']}). {len(F['cc_flagged'])} cell is flagged: "
          + ", ".join(f"{s} {d} {q}" for d, s, q in F["cc_flagged"]) + f". Cells with a loss event (dropped messages, input "
          f"truncation, context shift): {len(F['cc_events'])}. Unaligned cells (server-log tasks != calls): "
          + ", ".join(f"{s} {d} {q}" for d, s, q in F["cc_unaligned"]) + f". Every aligned call ran at n_ctx "
          f"{F['cc_n_ctx']} and temperature {F['cc_temps']}. Largest prompt plus reply per system: "
          + ", ".join(f"{names[s]} {v}" for s, v in F["cc_max_prompt_plus_out"].items()) + " tokens (of 16384).", "",
          f"**text_to_sql / employee / t1_05: the flag is spurious.**", "",
          f"1. The flag cites no loss event. `{c105.get('_file')}:{c105.get('_line')}` records calls 1, server_tasks "
          f"{c105.get('server_tasks')}, aligned {c105.get('aligned')}, loss_events {c105.get('loss_events')}, n_ctx "
          f"{c105.get('n_ctx')}, max_prompt_tokens {c105.get('max_prompt_tokens')}. `run_full.log:"
          f"{F['log_context_loss'][0][0] if F['log_context_loss'] else '?'}` prints the same. It is flagged only by the rule "
          "\"unaligned = cannot be checked: flagged, not assumed clean\" (`o2common.cell_context_record`): the server-log "
          "segment read for the cell held no task at all.",
          f"2. The prompt was far inside the context. `{x105.get('_file')}:{x105.get('_line')}`: input {x105.get('in')} uncached + "
          f"{x105.get('cache_read')} cache-read = {t105['prompt_tokens']} tokens, output {x105.get('out')}, one message, "
          f"stop {x105.get('stop')}, sent {utc(x105['t_start']) if x105 else '?'}, answered {utc(x105['t_end']) if x105 else '?'}; "
          "num_ctx is 16384.",
          f"3. Ollama restarted during the cell, so the segment was empty. The Ollama app received SIGTERM at 13:08:19 BST "
          f"(12:08:19Z, {ev('app1_sigterm')}) and started again at 13:08:31 BST ({ev('app_restart_0350')}); its server "
          f"started at 13:08:33 BST ({ev('s0_listen_0350')}) in a fresh `server.log`, the old one becoming `server-1.log`. "
          "The cell's start offset was a byte position in the old log; the read from that offset into the new, shorter "
          "file returned nothing, so zero tasks were matched to the one call (the server-log offset desync). The restart "
          f"changed OLLAMA_KEEP_ALIVE from 5m0s ({ev('s1_config_keepalive_5m', -1)}) to unlimited "
          f"({ev('s0_config_keepalive_inf')}).",
          f"4. The new log holds the request itself: {ev('t105_new_prompt')} `new prompt, n_ctx_slot = 16384, task.n_tokens = "
          f"{t105['prompt_tokens']}`, {ev('t105_release')} `stop processing ... truncated = 0`, {ev('t105_gin')} "
          f"`200 POST /v1/messages`; no truncation or context-shift line follows it "
          f"({len(E.get('t105_loss_lines', []))} found). The token counts match the client's ({t105['prompt_tokens']} in, "
          f"{t105['out']} out). `truncating input prompt` occurs {E.get('count_truncating_server-1.log', '?')} times in "
          f"`server-1.log` and {E.get('count_truncating_server.log', '?')} times in `server.log`; `context shift` "
          f"{E.get('count_context_shift_server-1.log', '?')} and {E.get('count_context_shift_server.log', '?')} times.",
          "", "The evidence is conclusive, so the cell was not re-run. The recorded answer stands; the cell counts as "
          "context-clean.", "",
          "The post-hoc `summary.json` context section reports employee `aligned: false` and so marks no employee call; "
          "it aligns whole sessions, not cells. Redone here: "
          + "; ".join(f"{x['session']} ({x['file']} bytes {x['range'][0]}-{x['range'][1]}): "
                      + (f"{x['server_tasks']} server tasks for {x['calls']} calls" if x['server_tasks'] is not None
                         else "end offset below start offset, the range crosses the log rotation")
                      for x in E.get("posthoc_alignment", []))
          + ". A stopped session's range runs to the next session's start offset and so takes in that session's "
          "preflight request (one task more than calls); session c21cb4664ee2's range crosses the rotation. The "
          "per-cell flags, computed from each cell's own log segment right after it, are aligned for every cell but "
          "t1_05.", f"Evidence source: {E.get('_source')}; excerpts in `evidence/ollama_log_excerpts.txt`.", ""]
    # disclosure facts
    pv = F["prov"]
    blk = [b for d in DOMAINS for b in pv[d]["o2_blocks"]]
    wdc = V["watchdog_cells"]
    s_rows = []
    for s in sessions:
        dur = ((s["end"] or s["last_call"]) - s["start"]) if (s["end"] or s["last_call"]) else 0
        s_rows.append(f"| {s['session']} | {s['domain']} | {utc(s['start'])} | "
                      f"{utc(s['end']) if s['end'] else 'no end record; last call ' + utc(s['last_call'])} | {hms(dur)} | "
                      f"{s['summary'] if s['summary'] else '-'} | {s['n_calls']} |")
    slow = V["slow"]
    L += ["## Disclosure facts", "",
          "| fact | value | evidence |", "|---|---|---|",
          f"| Ollama version during the reported run | 0.35.0 in every session | `run_full.sh:{F['version_check'][0]}-{F['version_check'][1]}` refuses to start on any other version; "
          f"preflight lines `run_full.log:{', '.join(str(i) for i, _ in F['log_preflight'])}`; provenance blocks of the four domains "
          f"({', '.join(sorted({b['ollama_version'] for b in blk}))}, after: {', '.join(sorted({str(b['after_version']) for b in blk}))}) |",
          f"| Auto-update 0.33.3 -> 0.35.0 | the Ollama app started as 0.33.3 at 09:34:35 BST on 2 Oct and updated itself at startup; 0.35.0 from 09:34:41 BST, before the reported run (10:42:31Z = 11:42:31 BST) | "
          f"{ev('app2_start_0333')}, {ev('app2_update')}, {ev('app1_start_0350')}; `run_full.sh:{F['version_lines'][0][0] if F['version_lines'] else '?'}` (comment) |",
          f"| Weights, digest, context | alias digest {blk[0]['digest'][:12] if blk else '?'}, weights blob {blk[0]['weights'][:12] if blk else '?'} = the base model's, num_ctx {blk[0]['num_ctx'] if blk else '?'}, loaded runner context {blk[0]['runner_ctx'] if blk else '?'}, Q4_K_M, in every recorded block; the same blob ran at -c 16384 under 0.33.3 on 1 Oct | "
          f"provenance `o2_local_model` blocks; {ev('s3_runner_16k_blob')} (0.33.3), {ev('s1_runner_loads')} and {ev('s0_runner_load')} (0.35.0). Ollama blobs are content-addressed, so one sha256 is one set of weights. The 0.33.3 alias digest itself is not recorded anywhere in the run directory |",
          f"| Restart from zero | the 1 Oct run (Ollama 0.33.3, from 19:31 BST) kept its directory in a /private/tmp scratchpad, which was lost; its scripts were reconstructed from the session transcript (`o2_localmodel/recovery/`). The reported run started from zero at {utc(sessions[0]['start'])}: its first cell is network t1_01, and every one of the 880 recorded cells has exactly its llm_calls in this run's call logs (mismatches {len(F['calls_count_mismatch'])}, cells with no call {len(F['cells_without_calls'])}), so no cell of the lost run is in it | "
          f"`sessions.jsonl:1`; `run_full.log:{F['log_starts'][0][0] if F['log_starts'] else '?'}` and its first cell line `run_full.log:{min(min(v) for v in F['log_cell'].values())}`; `recovery/dump/bash_26.sh` (OUT=/private/tmp/...); {ev('s3_listen_0333')} (the lost run's server log) |",
          f"| Run location | the reported run executed in `{F['run_location']}` (provenance `corpus_path`), a /private/tmp scratchpad again; `o2_localmodel/run` is the durable copy (results byte-identical to the scratchpad copy: {F['run_location_results_identical']}) | provenance JSON; `o2_localmodel/results_sha256.txt` |",
          f"| Code-agent exec watchdog | {len(wdc)} O2 code-agent cell ended by it: " + ", ".join(f"{r['_dom']} {r['qid']} ({r['tier']}, `{r['error']}`)" for r in wdc)
          + f"; the agent's code follows Manager ID upwards from each VP, and {F['vp_chain_cycles'][0]} of the {F['vp_chain_cycles'][1]} VP chains in the register cycle, so without the bound that exec never returns. The batch-2 / O1 code (5bf429e) has no such bound | "
          + ", ".join(f"run tree `{k}` lines {a}-{b}" for k, (a, b) in F["watchdog_lines"].items()) + "; `per_question.csv`; `local_model.patch` |",
          f"| Abandoned exec thread | the watchdog abandons the thread (Python cannot kill one); this loop never ends on its own, so it ran on in session {V.get('wd_session')} from {utc(V['wd_end']) if V['wd_end'] else '?'} until that process stopped (last call {utc(s_by[V['wd_session']]['last_call']) if V.get('wd_session') in s_by else '?'}). The {len(slow)} cells that session recorded after it took {sum(w for _, w in slow):.0f} s of cell wall time; employee text-to-SQL cells took "
          + "; ".join(f"{min(v):.1f}-{max(v):.1f} s in session {k} ({len(v)} cells)" for k, v in sorted(V["tts_wall"].items()))
          + ". Gap between consecutive calls (median) and client-side call latency (median), per session: "
          + "; ".join(f"{g['session']} {g['phase']} {g['median_gap_s']} s gap, {g['median_latency_s']} s latency ({g['calls']} calls)"
                      for g in V["gapstat"])
          + ". Ollama's own records at the reloads in that window show free system memory of "
          + ", ".join(re.search(r'free="([^"]+)"', h["text"]).group(1) + " (" + ev_line(h) + ")" for h in V["mem_slow"])
          + " of 24.0 GiB with the model unloaded, so memory was not exhausted. That the abandoned thread slowed the "
          "process (CPU and GIL contention in the run's own Python process) is an inference from this timing; no CPU "
          "profile exists. The model calls of those cells were answered normally server-side, and their scored outcomes are "
          "kept | `calls_employee.jsonl` t_start/t_end/latency_s; `results_o2_employee.jsonl` wall_time_s; Ollama logs |",
          f"| Keep-alive restart | in the slow session the gaps between text-to-SQL calls passed Ollama's 5-minute keep-alive, so the model was unloaded and reloaded {len(V['loads_slow'])} times ("
          + ", ".join(ev_line(h) for h in V["loads_slow"]) + "); Ollama was then restarted with unlimited keep-alive: server restart at 13:07:54 BST "
          f"({ev('s1_listen_0350', -1)}), app SIGTERM at 13:08:19 BST ({ev('app1_sigterm')}), new server at 13:08:33 BST with OLLAMA_KEEP_ALIVE unlimited ({ev('s0_config_keepalive_inf')}); one model load for the rest of the run ({ev('s0_runner_load')}) | Ollama logs, BST |",
          f"| React's timeout rate | {V['chat']['react']['runtime_timeout_all']} of {V['chat']['react']['n_all']} cells over all tiers ({pct(V['chat']['react']['runtime_timeout_all'], V['chat']['react']['n_all'])}), every one the 15-iteration bound; scored sets: "
          + ", ".join(f"{sn} {rate_of[(sn, 'timeout', 'react')]['k']}/{rate_of[(sn, 'timeout', 'react')]['n_cells']}"
                      for sn in ("o2_employee_in_contract", "o2_employee_out_of_range", "o2_spider_answer_strict",
                                 "o2_spider_out_of_contract_aggregate", "o2_spider_out_of_scope_register",
                                 "o2_pooled_within_vocab_strict")) + " | `status_counts.csv`; `rates.csv` |",
          "| Wall time per system (sum of cell wall times) | " + ", ".join(f"{names[s]} {V['wall'][s] / 3600:.2f} h" for s in SYSTEMS)
          + f"; total {sum(V['wall'].values()) / 3600:.2f} h. Of this, the {len(slow)} slow-session cells account for "
          + ", ".join(f"{names[s]} {V['wall_slow'][s] / 3600:.2f} h" for s in SYSTEMS if V['wall_slow'][s])
          + ". Model-call time (client-side latency, every call incl. cut episodes): " + ", ".join(f"{names[s]} {V['callsec'][s] / 3600:.2f} h" for s in SYSTEMS)
          + " | `results_o2_<domain>.jsonl` wall_time_s; `calls_<domain>.jsonl` latency_s |",
          f"| Total wall clock | {utc(V['run_start'])} to {utc(V['run_end'])}: {hms(V['run_end'] - V['run_start'])} (h:mm:ss), six sessions, {len(F['abandoned'])} episode(s) cut by a stopped session and re-run: "
          + ", ".join(f"{k[1]} {k[0]} {k[2]} ({n} call(s) in {s_})" for k, s_, n, _ in F["abandoned"]) + " | `sessions.jsonl`; `run_full.log` |",
          f"| Per-query timeout | {F['bounds_line'][1]}: 60 s bounds a text-to-SQL query's execution and, in the O2 tree only, each code-agent exec; react's bound is 15 iterations, the code agent's 12 retries | `o2common.py:{F['bounds_line'][0]}`; provenance `bounds` |",
          f"| Provenance gaps | the employee provenance holds an `o2_local_model` block for session {pv['employee']['o2_blocks'][-1]['session'] if pv['employee']['o2_blocks'] else '?'} only: sessions 59dc8614c532 and c21cb4664ee2 stopped before writing theirs, so their Ollama facts are the preflight lines (`run_full.log:{F['log_preflight'][0][0]}, {F['log_preflight'][1][0]}`). Driver files predate session 1 (mtimes "
          + ", ".join(f"{n} {utc(t)}" for n, t in V['F']['driver_mtimes'].items()) + f"; session 1 started {utc(sessions[0]['start'])}), and every start line prints the same patch sha256 (`run_full.log:{', '.join(str(i) for i, _ in F['log_starts'])}`). The harness's `git_commit` field reads {sorted(set(map(str, F['prov_git_commit'].values())))} because the run tree is not a git checkout; the O2 block records source commit 8605f52 and the patch sha256 | provenance JSON; `run_full.log`; `stat` |",
          "", "| session | domain | start | end | duration | summary | calls |", "|---|---|---|---|---|---|---|"] + s_rows + [""]
    # earlier chat figures
    ch = V["chat"]
    fset = "o2_pooled_within_vocab_strict"
    L += ["## The earlier chat figures", "",
          "| figure | chat value | what it was | figure in the paper's sets | why it differs |", "|---|---|---|---|---|"]
    for s in ("network", "planner", "text_to_sql", "code_agent", "react"):
        k, n = ch[s]["runtime_silent_all"], ch[s]["n_all"]
        L.append(f"| pooled silent error, {names[s]} | {CHAT['pooled_silent_pct'][s]}% | run-time `comparator_verdict` "
                 f"silent_wrong over all {n} cells of every tier: {k}/{n} = {pct(k, n)} | within the vocabulary "
                 f"(f, strict) {rate_of[(fset, 'silent_error', s)]['rate_pct']:.1f}% ({rate_of[(fset, 'silent_error', s)]['k']}/"
                 f"{rate_of[(fset, 'silent_error', s)]['n_cells']}); answer-anyway employee out_of_range "
                 f"{rate_of[('o2_employee_out_of_range', 'answer_anyway', s)]['rate_pct']:.1f}%, Spider out_of_scope_register "
                 f"{rate_of[('o2_spider_out_of_scope_register', 'answer_anyway', s)]['rate_pct']:.1f}%; beyond the vocabulary "
                 f"{rate_of[('o2_spider_out_of_contract_aggregate', 'silent_error', s)]['rate_pct']:.1f}% | it pools "
                 "in-contract silent errors with answer-anyway on out-of-contract tiers, uses run-time tiers and gold "
                 "(wta_26 still a decline question), keeps oor_03, and divides by cells of every tier |")
    g_tot = len(V["g_ref"]) + len(V["g_sil"])
    sil_goal = sum(1 for c in V["g_sil"] if c["has_goal"])
    L.append(f"| GDC's checks turned bad answerable Goals into refusals | 42 of 47 | refusals plus silent errors on the "
             f"within-vocabulary set (f): {len(V['g_ref'])} + {len(V['g_sil'])} = {g_tot} | of the "
             f"{len(V['g_goal_ref']) + sil_goal} emitted Goals that did not give a correct answer, {len(V['g_goal_ref'])} were "
             f"refused by a typed check before execution and {sil_goal} reached the user as silent errors; the other "
             f"{len(V['g_nogoal_ref'])} refusals had no Goal | {len(V['g_nogoal_ref'])} of the {len(V['g_ref'])} refusals are "
             "ParseErrors, the model's output not parsing into a Goal ("
             + ", ".join(f"'{m}' {n}" for m, n in V["parse_msgs"].most_common())
             + "); D8 counts them as failures, not declines, and no check acted on them |")
    x_acc, x_ref = rate_of[(fset, "accuracy", GDC)], rate_of[(fset, "refused_should_answer", GDC)]
    t_acc, t_ref = rate_of[("o2_pooled_within_vocab_tolerant", "accuracy", GDC)], rate_of[("o2_pooled_within_vocab_tolerant", "refused_should_answer", GDC)]
    L.append(f"| GDC within-vocabulary accuracy / refusal | 56.5% / 38.9% | set (f) | strict {x_acc['rate_pct']:.1f}% "
             f"[{x_acc['ci_lo_pct']:.1f}, {x_acc['ci_hi_pct']:.1f}] ({x_acc['k']}/{x_acc['n_cells']}) / {x_ref['rate_pct']:.1f}% "
             f"({x_ref['k']}/{x_ref['n_cells']}); tolerant {t_acc['rate_pct']:.1f}% / {t_ref['rate_pct']:.1f}% | "
             + ("agrees with strict" if abs(x_acc['rate_pct'] - 56.5) < 0.05 and abs(x_ref['rate_pct'] - 38.9) < 0.05 else
                "differs") + " |")
    rt = ch["react"]
    rk = sum(1 for c in cells if c['system'] == 'react' and c['scored'] and c['strict'] == 'timeout')
    rn = sum(1 for c in cells if c['system'] == 'react' and c['scored'])
    L.append(f"| react timed out | 17.6% | run-time status timeout over all cells: {rt['runtime_timeout_all']}/{rt['n_all']} = "
             f"{pct(rt['runtime_timeout_all'], rt['n_all'])} | scored cells (oor_03 dropped): {rk}/{rn} = {pct(rk, rn)}; "
             "per set in the disclosure table | the chat figure is right for all 176 run cells; D11 drops oor_03, one of "
             "react's timeouts, and the rate varies by tier |")
    L += ["", "## D7, D8 and D6", "",
          "D8 is applied as `paper_inputs.decline_channel` implements it; REPORT.md §11 item 8 leaves open whether a "
          "react or code-agent run that ends in a statement and no answer counts as an explicit decline. The O2 declines "
          "on the two decline tiers, by system and detail: "
          + ", ".join(f"{d} {names[s]} {cl.replace('declined_', '')}/{det} {n}" for (d, s, cl, det), n in sorted(V["d8_detail"].items()))
          + ". Counting statement-only endings as failures would move "
          + str(sum(n for (d, s, cl, det), n in V["d8_detail"].items() if det == "statement, no answer"))
          + " cell(s) from declined to failed.", ""]
    L += ["D7 is applied above. REPORT.md §11 item 10 still asks the author to confirm wta_26's re-tier; under the run tree's "
          "pre-D7 gold (a decline question) the five wta_26 cells score: "
          + ", ".join(f"{names[s]} {v['pre_D7']}{' (' + v['d8_pre_D7'] + ')' if v['d8_pre_D7'] else ''}" for s, v in V['d7'].items())
          + "; under D7 (beyond the vocabulary, answer gold): "
          + ", ".join(f"{names[s]} {v['D7']}" for s, v in V['d7'].items()) + ".", "",
          f"D6 (disclosure only; employee stays strict). Employee limit-tie questions {sorted(V['ties_emp'])}: "
          + ", ".join(f"{names[s]} {V['d6'][s].get('correct', 0)} correct and {V['d6'][s].get('tie_alternative', 0)} "
                      f"equal to a tied alternative of {V['d6'][s].get('n', 0)}" for s in SYSTEMS) + ".", ""]
    # proposal for the §6 TODO (the author decides; nothing applied)
    fs = "o2_pooled_within_vocab_strict"
    poss = {"planner": "the planner's", "react": "ReAct's", "code_agent": "the code agent's",
            "text_to_sql": "text-to-SQL's"}
    ordinal = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}

    def andjoin(xs):
        xs = list(xs)
        return " and ".join(xs) if len(xs) < 3 else ", ".join(xs[:-1]) + " and " + xs[-1]
    g_s = rate_of[(fs, "silent_error", GDC)]
    alt_s = [rate_of[(fs, "silent_error", s)]["rate_pct"] for s in ALTS]
    res_alts = [poss[x["alt"]] for x in comps if x["set"] == fs and x["metric"] == "silent_error" and x["resolved_after_holm"]]
    unres_alts = [poss[x["alt"]] for x in comps if x["set"] == fs and x["metric"] == "silent_error" and not x["resolved_after_holm"]]
    acc_below = [poss[x["alt"]] for x in comps if x["set"] == fs and x["metric"] == "accuracy"
                 and x["resolved_after_holm"] and x["diff_pp"] < 0]
    acc_above = [poss[x["alt"]] for x in comps if x["set"] == fs and x["metric"] == "accuracy"
                 and x["resolved_after_holm"] and x["diff_pp"] > 0]
    sp_rank = 1 + sum(1 for s in ALTS if rate_of[("o2_spider_answer_strict", "accuracy", s)]["rate_pct"]
                      > rate_of[("o2_spider_answer_strict", "accuracy", GDC)]["rate_pct"])
    sp_res = [x["alt"] for x in comps if x["set"] in ("o2_spider_answer_strict", "o2_spider_answer_tolerant")
              and x["metric"] == "silent_error" and x["verdict"] == "resolved"]
    the = {"planner": "the planner", "react": "ReAct", "code_agent": "the code agent", "text_to_sql": "text-to-SQL"}
    em_res = [the[x["alt"]] for x in comps if x["set"] == "o2_employee_in_contract" and x["metric"] == "silent_error"
              and x["resolved_after_holm"]]
    g_r = rate_of[(fs, "refused_should_answer", GDC)]
    oo = rate_of[("o2_spider_out_of_scope_register", "answer_anyway", GDC)]
    oe = rate_of[("o2_employee_out_of_range", "answer_anyway", GDC)]
    L += ["## Proposal for the §6 O2 sentence (for the author; not applied anywhere)", "",
          "Candidate text for the `⟦IF O2⟧⟦TODO: O2, one sentence ...⟧` slot in §6. Every number is from `rates.csv` "
          f"(set `{fs}`, `o2_spider_out_of_scope_register`, `o2_employee_out_of_range`) and every verdict from "
          "`comparisons.csv`; whether to put numbers in the sentence or only in the appendix table is the author's call.", "",
          f"> On an open-weights model, Qwen3-Coder 30B at 4 bits (employee and Spider, one seed, all five systems; "
          f"Appendix ⟦TODO⟧), GDC gives a silent error on {g_s['rate_pct']:.1f}% of the in-scope questions within its "
          f"vocabulary against {min(alt_s):.1f}% to {max(alt_s):.1f}% for the alternatives, lower than "
          + andjoin(res_alts) + (" and not distinguishable from " + andjoin(unres_alts) if unres_alts else "")
          + f" under the question-level tests, and refuses {g_r['rate_pct']:.1f}% of them; {len(V['g_nogoal_ref'])} of "
          f"its {len(V['g_ref'])} refusals are output that does not parse into a Goal. It still answers "
          f"{oo['k']} of the {oo['n_cells']} out-of-scope Spider questions and {oe['k']} of the {oe['n_cells']} "
          "out-of-range employee questions. The model is weaker than claude-sonnet-4-5, so this cannot show whether "
          "a stronger model stops substituting.", "",
          "Facts the sentence must not overstate: the pooled silent-error verdicts rest on employee (resolved after Holm "
          f"there against {andjoin(em_res) or 'none'}); on Spider's answer tier alone "
          + ("no silent-error difference is resolved" if not sp_res else f"resolved against {sorted(set(sp_res))}")
          + ". GDC's accuracy within the vocabulary is below " + (andjoin(acc_below) or "no alternative's")
          + " and above " + (andjoin(acc_above) or "no alternative's")
          + " (resolved after Holm); on Spider's answer tier (strict) GDC's accuracy ranks "
          + ordinal.get(sp_rank, str(sp_rank)) + " of five. The O2 code is the released code plus the code-agent exec "
          "watchdog and the local-model client settings (`local_model.patch`), and GDC ran on prompt s48, not the s45 "
          "prompt behind the paper's Spider GDC numbers.", ""]
    L += ["## Inputs", "",
          f"- O2 results: `{V['a'].run / 'results'}`, read only; sha256 against `o2_localmodel/results_sha256.txt`: "
          + (", ".join(f"{n} {'OK' if ok else 'MISMATCH'}" for n, (h, ok) in sorted(V['sums'].items())) or "no checksum file") + ".",
          f"- Claude CSV: `{V['ccsv']}` sha256 {sha256(V['ccsv'])}.",
          f"- Scoring tree: `{V['a'].tree}`; builder functions lifted from `scripts/build_paper_numbers.py` lines "
          f"{T.builder_lines}.",
          f"- Stats functions: `{V['a'].stats}` sha256 {sha256(V['a'].stats)[:16]}.",
          f"- This script: `{Path(__file__).resolve()}` sha256 {sha256(Path(__file__).resolve())[:16]}.", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
