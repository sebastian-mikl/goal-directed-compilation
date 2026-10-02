#!/usr/bin/env python
"""Build the FINAL locked paper numbers (all five decisions applied).

Scoring:
  STRICT   = comparator s48 (exact-match; s48 = s47 + rules gated to the 10 algebra_boundary golds).
  TOLERANT = s48t = s48 + data-verified ranking ties (scripts/score_tie_tolerant;
             affects only Spider world_13/bike_06 — the pandas-vs-SQLite tie-break
             artifact; adversarially checked — and, since D9, employee t3_04/t3_13,
             whose limits cut through a tie: reports/tie_alternatives_synthetic.json).
             Reported alongside strict, footnote. Risk has no tie, so no risk row.

Decisions:
  1  Spider network = post-fix. The multi-key GROUP BY fix changes NO in-contract
     outcome-class (regression-verified); wta_15 is retiered to
     out_of_contract_aggregate (gold), so it leaves the answer tier (140->135).
     Network scored by RESCORING recorded postfix_s40off raw_result under s47
     (== the native Phase-R outcome_class). Baselines recorded, rescored s47.
     (Network and planner scoring superseded by Decision 7.)
  2  risk/employee network = REPLAY recorded goals through the CURRENT engine
     (not rescore-only), for parity with Spider's "current system" framing.
     (Extended to every domain and to the planner by Decision 7.)
  3/4 risk-T5 for code_agent + text_to_sql = filled from the live re-run
     (results_risk_t5_missing.jsonl, merged into the risk in-contract set).
  5  Temperature parity (post-hoc). The two-directional temperature probe found
     the baseline/GDC temperature asymmetry partly real (silent->correct 20.6%,
     correct->silent 0.0%); a full in-contract parity re-run of the three
     baselines at temperature 0 (scripts/rerun_temp0_parity.py) then found the
     effect small and non-directional (all deltas <=1.3 points, no relative
     standing changed) — so temperature-0 is adopted as canonical throughout.
     reports/paper_numbers.csv IS the temperature-0 (parity) run; the original
     default-temperature baselines are kept for provenance in
     reports/paper_numbers_default_temp.csv (scripts/report_parity_delta.py
     diffs the two). To regenerate paper_numbers.csv from source: run
     scripts/rerun_temp0_parity.py (needs ANTHROPIC_API_KEY, ~2379 cells,
     ~$38.5), then `PARITY_TEMP0=1 python scripts/build_paper_numbers.py`.
     Safety: a vanilla invocation (PARITY_TEMP0 unset) writes ONLY
     paper_numbers_default_temp.csv — it can never silently regress the
     canonical paper_numbers.csv back to pre-parity numbers.
  6  Re-runs at temperature 0 under the recorded prompts. The risk domain's own
     re-run is withheld with its data. The second temperature-0 batch
     (results/results_temp0_batch2_<domain>.jsonl: employee, wta_1, world_1,
     bike_1) drops in once it exists: with PARITY_TEMP0=1 parity_overlay puts
     each re-run row in for its recorded row by (system, qid, seed), any system.
     parity_overlay declares that a batch2 file supersedes the parity rows of the
     cells it re-runs, and refuses one that is partial, holds rows the run did not
     complete, or ran on another engine than the one this tree ports
     (PORTED_ENGINE; PARTIAL_BATCH2=1 admits a partial file and says so in
     `source`). GDC rows are still REPLAYED. Rows that use re-run cells name the
     file in `source`; every replaced cell is listed with its overlay file in
     reports/paper_numbers.overlay_provenance.csv.
  7  GDC and planner = REPLAY, every domain. Every GDC Goal (risk, employee,
     Spider s40off and s40on) and every planner plan (risk, employee, Spider, and
     both models of the Haiku panel) is re-executed on the current engine and
     scored from the replayed result (scripts/paper_inputs.py). The two systems
     share gdc/primitives.py, so they are scored on one engine. Before this,
     Spider GDC and the planner everywhere were scored from recorded raw_result,
     so an engine change reached GDC's risk/employee cells but not the planner's
     cells beside them. react, code_agent and text_to_sql stay recorded-rescored.
     The engine is recorded beside the CSV: <csv stem>.engine.json holds one
     sha256 over the engine files (parity_overlay.ENGINE_GLOBS), each file's
     hash, the contract and comparator versions and the CSV's own sha256.

Every figure/table renders from reports/paper_numbers.csv.
"""
from __future__ import annotations
import csv, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evidence.eval_run.comparator import score, COMPARATOR_VERSION           # noqa: E402
from scripts.score_tie_tolerant import score_tt, score_tt_syn, SCORER_VERSION  # noqa: E402
from scripts.parity_overlay import (coverage_report, PARITY, rerun_note,     # noqa: E402
                                    provenance_rows, check_overlays, engine_hashes, engine_sha)
from scripts import paper_inputs as pi                                       # noqa: E402
assert COMPARATOR_VERSION == "s48", COMPARATOR_VERSION

SPIDER = pi.SPIDER
SYSTEMS = pi.SYSTEMS
SPIDER_BOUNDARY = {"out_of_scope_register", "out_of_contract_aggregate"}
GOLD_SPIDER = {d: pi.GOLD[d] for d in SPIDER}
# The risk domain's source data is withheld from the public release (its register
# is modelled on a real facility's schema). Every risk section below is guarded by
# HAS_RISK (paper_inputs.HAS_RISK), so this script produces the employee + Spider
# numbers cleanly when the risk files are absent, and the full set when they are present.
HAS_RISK = pi.HAS_RISK
GOLD_RISK = pi.GOLD.get("risk", {})
if not HAS_RISK:
    print("[note] risk domain data not included in this release — "
          "skipping risk tables, the cost frontier, and the Haiku robustness panel.")
GOLD_EMP = pi.GOLD["employee"]
_load = pi.load
scored_input = pi.scored_input   # (result, status): GDC/planner replayed (Decision 7), others recorded

def outcome(dom, row, g):
    """Strict s48 outcome class of one row (GDC/planner replayed, others recorded)."""
    res, status = scored_input(dom, row)
    return score(res, g, status=status)[0]

def prov_of(sysname, recorded):
    """`source` text: what was scored for `sysname`, read from `recorded`."""
    return (f"{recorded} REPLAY current engine" if sysname in pi.REPLAYED
            else f"{recorded} rescored {COMPARATOR_VERSION}")

def acc(scored):
    n = len(scored); c = sum(1 for v in scored if v == "correct")
    return c, n, (0.0 if not n else round(100 * c / n, 1))

def rate(scored, cls):
    n = len(scored); k = sum(1 for v in scored if v == cls)
    return k, n, (0.0 if not n else round(100 * k / n, 1))

ROWS = []
def emit(**kw): ROWS.append(kw)

# ══ A. Spider in-contract, 5 systems, STRICT + TOLERANT (Fig1/2/Table3) ══════
for sysname in SYSTEMS:
    strict, tol, used = [], [], []
    for d in SPIDER:
        for r in _load(pi.spider_file(d, sysname)):
            if r.get("system") != sysname:
                continue
            g = GOLD_SPIDER[d].get(r["qid"])
            if g is None or g["tier"] in SPIDER_BOUNDARY:   # wta_15 now out_of_contract -> excluded
                continue
            res, status = scored_input(d, r)
            sv, _, _ = score(res, g, status=status)
            tv, _, _ = score_tt(res, g, r["qid"], status=status)
            strict.append(sv); tol.append(tv); used.append(r)
    for label, scored in (("strict", strict), ("tolerant", tol)):
        c, n, a = acc(scored); k, _, sw = rate(scored, "silent_wrong")
        prov = prov_of(sysname, "PhaseR s40off (post-fix)" if sysname == "network" else "recorded") + rerun_note(used)
        emit(fig_table="Fig1/Fig2/Table3", domain="spider_pooled", system=sysname,
             tier_group="in_contract_answer", scoring=label, metric="accuracy",
             value_pct=a, numerator=c, denominator=n, source=prov, comparator=SCORER_VERSION)
        emit(fig_table="Fig1/Fig2/Table3", domain="spider_pooled", system=sysname,
             tier_group="in_contract_answer", scoring=label, metric="silent_wrong",
             value_pct=sw, numerator=k, denominator=n, source=prov, comparator=SCORER_VERSION)
        for cls in ("refused_should_answer", "crash", "timeout"):        # full five-way partition (Table 2, A.3)
            k2, _, p2 = rate(scored, cls)
            emit(fig_table="Fig1/Fig2/Table3", domain="spider_pooled", system=sysname,
                 tier_group="in_contract_answer", scoring=label, metric=cls,
                 value_pct=p2, numerator=k2, denominator=n, source=prov, comparator=SCORER_VERSION)

# ══ B. Spider network per-tier per domain (Fig3/Fig4) ════════════════════════
for d in SPIDER:
    off = _load(pi.spider_file(d, "network", "off"))
    on = _load(pi.spider_file(d, "network", "on"))
    def scored_tier(rows, sel):
        out = []
        for r in rows:
            g = GOLD_SPIDER[d].get(r["qid"])
            if g and sel(g["tier"]):
                out.append(outcome(d, r, g))
        return out
    for tg, rows, sel in [
        ("answer", off, lambda t: t not in SPIDER_BOUNDARY),
        ("out_of_contract_aggregate", off, lambda t: t == "out_of_contract_aggregate"),
        ("out_of_scope_register_s40off", off, lambda t: t == "out_of_scope_register"),
        ("out_of_scope_register_s40on", on, lambda t: t == "out_of_scope_register"),
    ]:
        sc = scored_tier(rows, sel)
        for cls in ("correct", "silent_wrong", "refused_should_answer"):
            k, n, p = rate(sc, cls)
            emit(fig_table="Fig3/Fig4", domain=d, system="network", tier_group=tg,
                 scoring="strict", metric=cls, value_pct=p, numerator=k, denominator=n,
                 source="PhaseR REPLAY current engine", comparator=COMPARATOR_VERSION)

# ══ D. risk + employee per-tier, 5 systems (Table1) ═════════════════════════
_retier = pi.retier   # tier of record = the gold record's (risk out_of_range -> algebra_boundary)
IN_CONTRACT_SYN = ["T1_single_op", "T2_two_ops", "T3_three_ops", "T4_business_rule_multi_op",
                   "T5_high_complexity", "house_vocab", "algebra_boundary"]

RISK_OFF = pi.risk_off()
# risk-T5: network/planner/react from results_t5; code_agent/text_to_sql from the
# live fill run (Decisions 3/4). Concatenate -> all 5 systems, uniform 415 cells.
RISK_T5 = pi.risk_t5()
EMP = pi.employee()
SYN_TIERS = ["T1_single_op", "T2_two_ops", "T3_three_ops", "T4_business_rule_multi_op",
             "T5_high_complexity", "house_vocab", "algebra_boundary", "out_of_range"]

def syn_rows(dom, sysname, tier):
    if dom == "employee":
        return [r for r in EMP if r["system"] == sysname and r["tier"] == tier], GOLD_EMP, "results_employee.jsonl"
    pool = RISK_T5 if tier == "T5_high_complexity" else RISK_OFF
    fn = "results_t5.rescored.jsonl" if tier == "T5_high_complexity" else "results_off.rescored.jsonl"
    return [r for r in pool if r["system"] == sysname and r["tier"] == tier], GOLD_RISK, fn

def score_syn(dom, sysname, rows, gold):
    """network/planner -> REPLAY (Decisions 2, 7); baselines -> rescore recorded. Returns outcome list."""
    out = []
    for r in rows:
        g = gold.get(r["qid"])
        if g is None:
            continue
        out.append(outcome(dom, r, g))
    return out

def score_syn_tol(dom, sysname, rows, gold):
    """score_syn under s48t (D9): the same cells, a ranked answer that matches a data-verified
    tied filling of the limit scores correct. Only employee t3_04 and t3_13 can differ."""
    out = []
    for r in rows:
        g = gold.get(r["qid"])
        if g is None:
            continue
        res, status = scored_input(dom, r)
        out.append(score_tt_syn(dom, res, g, r["qid"], status=status)[0])
    return out

for dom in (("risk", "employee") if HAS_RISK else ("employee",)):
    for sysname in SYSTEMS:
        for tier in SYN_TIERS:
            if not any(g.get("tier") == tier for g in (GOLD_RISK if dom == "risk" else GOLD_EMP).values()):
                continue                    # tier absent from this domain (risk has no out_of_range)
            rows, gold, fn = syn_rows(dom, sysname, tier)
            if not rows:
                emit(fig_table="Table1", domain=dom, system=sysname, tier_group=tier, scoring="strict",
                     metric="accuracy", value_pct=None, numerator=0, denominator=0,
                     source=fn + " (NO DATA - PENDING live re-run)" if (dom == "risk" and tier == "T5_high_complexity"
                     and sysname in ("code_agent", "text_to_sql")) else fn + " (no data)", comparator=COMPARATOR_VERSION)
                continue
            sc = score_syn(dom, sysname, rows, gold)
            c, n, a = acc(sc); k, _, sw = rate(sc, "silent_wrong")
            prov = prov_of(sysname, fn) + rerun_note(rows)
            emit(fig_table="Table1", domain=dom, system=sysname, tier_group=tier, scoring="strict",
                 metric="accuracy", value_pct=a, numerator=c, denominator=n, source=prov, comparator=COMPARATOR_VERSION)
            emit(fig_table="Table1", domain=dom, system=sysname, tier_group=tier, scoring="strict",
                 metric="silent_wrong", value_pct=sw, numerator=k, denominator=n, source=prov, comparator=COMPARATOR_VERSION)
            for cls in ("refused_should_answer", "crash", "timeout"):    # full five-way partition (App. A.1/A.2)
                k2, _, p2 = rate(sc, cls)
                emit(fig_table="Table1", domain=dom, system=sysname, tier_group=tier, scoring="strict",
                     metric=cls, value_pct=p2, numerator=k2, denominator=n, source=prov, comparator=COMPARATOR_VERSION)
            if dom == "employee" and tier in IN_CONTRACT_SYN:            # D9: tolerant beside strict
                sct = score_syn_tol(dom, sysname, rows, gold)
                c, n, a = acc(sct); k, _, sw = rate(sct, "silent_wrong")
                emit(fig_table="Table1", domain=dom, system=sysname, tier_group=tier, scoring="tolerant",
                     metric="accuracy", value_pct=a, numerator=c, denominator=n, source=prov, comparator=SCORER_VERSION)
                emit(fig_table="Table1", domain=dom, system=sysname, tier_group=tier, scoring="tolerant",
                     metric="silent_wrong", value_pct=sw, numerator=k, denominator=n, source=prov, comparator=SCORER_VERSION)
                for cls in ("refused_should_answer", "crash", "timeout"):
                    k2, _, p2 = rate(sct, cls)
                    emit(fig_table="Table1", domain=dom, system=sysname, tier_group=tier, scoring="tolerant",
                         metric=cls, value_pct=p2, numerator=k2, denominator=n, source=prov, comparator=SCORER_VERSION)

# ══ E. cross-domain in-contract (Table2): risk/employee = T1-T5+hv ═══════════
# (IN_CONTRACT_SYN is defined above, with section D)
# risk is also reported split: within GDC's vocabulary (the original 415 cells) and
# beyond it (the 50 algebra_boundary cells); in_contract_answer stays the combined set.
BEYOND_VOCAB = {"algebra_boundary"}
VOCAB_SPLIT = [("in_contract_answer", lambda t: True),
               ("in_contract_within_vocab", lambda t: t not in BEYOND_VOCAB),
               ("in_contract_beyond_vocab", lambda t: t in BEYOND_VOCAB)]
for dom in (("risk", "employee") if HAS_RISK else ("employee",)):
    for sysname in SYSTEMS:
        for group, keep in (VOCAB_SPLIT if dom == "risk" else VOCAB_SPLIT[:1]):
            sc, used = [], []
            pending = False
            for tier in [t for t in IN_CONTRACT_SYN if keep(t)]:
                rows, gold, _ = syn_rows(dom, sysname, tier)
                if not rows and dom == "risk" and tier == "T5_high_complexity" and sysname in ("code_agent", "text_to_sql"):
                    pending = True
                sc += score_syn(dom, sysname, rows, gold); used += rows
            if not sc:
                continue
            prov = f"{sysname} REPLAY" if sysname in pi.REPLAYED else f"recorded rescored {COMPARATOR_VERSION}"
            prov += rerun_note(used)
            if pending:
                prov += " (INCOMPLETE - risk-T5 pending live re-run)"
            for cls in ("correct", "silent_wrong", "refused_should_answer", "crash", "timeout"):
                k, n, p = rate(sc, cls)
                emit(fig_table="Table2", domain=dom, system=sysname, tier_group=group,
                     scoring="strict", metric="accuracy" if cls == "correct" else cls, value_pct=p,
                     numerator=k, denominator=n, source=prov, comparator=COMPARATOR_VERSION)
            if dom == "employee":                                          # D9: tolerant beside strict
                sct = []
                for tier in [t for t in IN_CONTRACT_SYN if keep(t)]:
                    rows, gold, _ = syn_rows(dom, sysname, tier)
                    sct += score_syn_tol(dom, sysname, rows, gold)
                for cls in ("correct", "silent_wrong", "refused_should_answer", "crash", "timeout"):
                    k, n, p = rate(sct, cls)
                    emit(fig_table="Table2", domain=dom, system=sysname, tier_group=group,
                         scoring="tolerant", metric="accuracy" if cls == "correct" else cls, value_pct=p,
                         numerator=k, denominator=n, source=prov, comparator=SCORER_VERSION)

# ══ F. Cost-accuracy frontier (risk in-contract, Sonnet, s47) — ADDITIVE ═════
# mean $/query, tokens/query (in+out), LLM calls/query, per system, over the SAME
# risk in-contract sets as Table 2 (T1-T4/hv/algebra_boundary from results_off; T5
# from results_t5 + the risk-T5 fill; whole, within- and beyond-vocabulary).
# Cost = cost_usd (actual, prompt-cached run). Accuracy is scored as in Table 2:
# GDC and planner replayed (Decision 7), the other three recorded.
import statistics as _stats  # noqa: E402
_IN_OFF = {"T1_single_op", "T2_two_ops", "T3_three_ops", "T4_business_rule_multi_op", "house_vocab",
           "algebra_boundary"}
def _risk_ic_rows(sysname, keep=lambda t: True):
    r = [x for x in RISK_OFF if x["system"] == sysname and x["tier"] in _IN_OFF and keep(x["tier"])]
    r += [x for x in RISK_T5 if x["system"] == sysname and x["tier"] == "T5_high_complexity" and keep(x["tier"])]
    return r
_COST_PROV = "results_off+results_t5+results_risk_t5_missing (Sonnet) cost/token fields"
for sysname, (group, keep) in [(s, g) for s in (SYSTEMS if HAS_RISK else []) for g in VOCAB_SPLIT]:
    R = _risk_ic_rows(sysname, keep); n = len(R)
    corr = sum(1 for x in R if outcome("risk", x, GOLD_RISK[x["qid"]]) == "correct")
    mcost = round(_stats.mean([x.get("cost_usd") or 0 for x in R]), 5)
    mtok = round(_stats.mean([(x.get("input_tokens") or 0) + (x.get("output_tokens") or 0) for x in R]))
    mcalls = round(_stats.mean([x.get("llm_calls") or 0 for x in R]), 2)
    for metric, v, num in [("accuracy", round(100 * corr / n, 1), corr),
                           ("mean_cost_usd_per_query", mcost, 0),
                           ("mean_tokens_per_query", mtok, 0),
                           ("mean_llm_calls_per_query", mcalls, 0)]:
        emit(fig_table="CostFrontier", domain="risk", system=sysname, tier_group=group,
             scoring="strict", metric=metric, value_pct=v, numerator=num, denominator=n,
             source=_COST_PROV + ("; accuracy REPLAY current engine" if sysname in pi.REPLAYED else "")
             + rerun_note(R), comparator=COMPARATOR_VERSION)

# ══ G. Haiku model-robustness (risk, network+planner, REPLAYED, s48) ═════════
# Sonnet AND Haiku emissions are both REPLAYED on the current engine (Decision 7),
# so the comparison holds the engine constant by construction. (Both were scored
# from RECORDED raw_result before, which is why Sonnet-network here read 97.6/1.2
# against Table 2's replay-based 96.4/2.4.) fig_table encodes the model:
# Haiku_sonnet vs Haiku_haiku. Only network+planner have Haiku emission data.
HAIKU = _retier(_load(pi.HAIKU_FILE), GOLD_RISK) if HAS_RISK else []
# Sonnet side = the June recorded emissions, read WITHOUT the temperature-0 overlay
# (the risk re-run of Decision 6), so both models stay on the emissions this panel
# was built from.
_SONNET_OFF = pi.risk_off(overlay=False)
_SONNET_T5 = pi.risk_t5(overlay=False)
_H_TIERS = ["T1_single_op", "T2_two_ops", "T3_three_ops", "T4_business_rule_multi_op",
            "T5_high_complexity", "house_vocab", "algebra_boundary"]
def _sonnet_pool(tier):
    return _SONNET_T5 if tier == "T5_high_complexity" else _SONNET_OFF
for model, pool_of in ([("sonnet", _sonnet_pool), ("haiku", lambda t: HAIKU)] if HAS_RISK else []):
    for sysname in ("network", "planner"):
        for tier in _H_TIERS:
            R = [x for x in pool_of(tier) if x["system"] == sysname and x["tier"] == tier]
            if not R:
                continue
            outs = [outcome("risk", x, GOLD_RISK[x["qid"]]) for x in R]
            n = len(outs); c = outs.count("correct"); sw = outs.count("silent_wrong")
            src = ("results_haiku.rescored.jsonl (Haiku) REPLAY current engine" if model == "haiku"
                   else "results_off/results_t5 June (Sonnet) REPLAY current engine")
            emit(fig_table=f"Haiku_{model}", domain="risk", system=sysname, tier_group=tier,
                 scoring="strict", metric="accuracy", value_pct=round(100 * c / n, 1),
                 numerator=c, denominator=n, source=src, comparator=COMPARATOR_VERSION)
            emit(fig_table=f"Haiku_{model}", domain="risk", system=sysname, tier_group=tier,
                 scoring="strict", metric="silent_wrong", value_pct=round(100 * sw / n, 1),
                 numerator=sw, denominator=n, source=src, comparator=COMPARATOR_VERSION)

# ══ D6. limit ties on the synthetic domains (counts per tie question; D9 adds the employee
#    tolerant rows beside strict in Table1/Table2 above) ══
# reports/tie_alternatives_synthetic.json (scripts/synthetic_ties.py) holds every
# ranked employee question whose limit cuts through a tie (risk too, where its data
# is present; this repository's file has no risk key), found from the gold builders,
# with every other valid filling of the tied slots. Per system and tie
# question: strict `correct`, and `tie_alternative` = cells strict scores wrong that
# equal a tied alternative (what the tie-tolerant rule accepts; employee tolerant rows,
# D9, score_tt_syn, read the same alternatives). Domain row (system "gold"): tie questions of the ranked questions scanned.
import json as _json  # noqa: E402
_TIES_SYN = _json.loads((ROOT / "reports" / "tie_alternatives_synthetic.json").read_text())
for dom, gold, pool in ((([("risk", GOLD_RISK, RISK_OFF + RISK_T5)] if HAS_RISK else [])
                          + [("employee", GOLD_EMP, EMP)])):    # no risk row without the risk data
    ties = {q: t for q, t in _TIES_SYN["ties"].get(dom, {}).items() if q in gold}
    ranked = [q for q in _TIES_SYN["scanned"].get(dom, []) if q in gold]
    emit(fig_table="Ties", domain=dom, system="gold", tier_group="ranked_questions", scoring="strict",
         metric="limit_ties", value_pct=round(100 * len(ties) / len(ranked), 1) if ranked else None,
         numerator=len(ties), denominator=len(ranked),
         source=f"reports/tie_alternatives_synthetic.json: {', '.join(sorted(ties)) or 'none'}",
         comparator=COMPARATOR_VERSION)
    for sysname in SYSTEMS if ties else ():
        tot = {"correct": 0, "tie_alternative": 0, "n": 0}
        for qid, t in sorted(ties.items()):
            R = [r for r in pool if r["system"] == sysname and r["qid"] == qid]
            c = alt = 0
            for r in R:
                res, status = scored_input(dom, r)
                if score(res, gold[qid], status=status)[0] == "correct":
                    c += 1
                elif any(score(res, {**gold[qid], "value": v}, status=status)[0] == "correct"
                         for v in t["alt_values"]):
                    alt += 1
            for tg, metric, k, n in ((qid, "correct", c, len(R)), (qid, "tie_alternative", alt, len(R))):
                emit(fig_table="Ties", domain=dom, system=sysname, tier_group=tg, scoring="strict",
                     metric=metric, value_pct=round(100 * k / n, 1) if n else None, numerator=k, denominator=n,
                     source=prov_of(sysname, "results_employee.jsonl" if dom == "employee" else "risk")
                     + f"; top-{t['k']} on {t['by']!r}, {t['tied_rows']} rows tied for {t['slots']} slots"
                     + rerun_note(R), comparator=COMPARATOR_VERSION)
            tot["correct"] += c; tot["tie_alternative"] += alt; tot["n"] += len(R)
        for metric in ("correct", "tie_alternative"):
            emit(fig_table="Ties", domain=dom, system=sysname, tier_group="all_limit_ties", scoring="strict",
                 metric=metric, value_pct=round(100 * tot[metric] / tot["n"], 1) if tot["n"] else None,
                 numerator=tot[metric], denominator=tot["n"],
                 source=f"{', '.join(sorted(ties))}; " + prov_of(sysname, dom), comparator=COMPARATOR_VERSION)

# ══ H. out-of-scope refusal, 5 systems (Table 5) ═════════════════════════════
# Declining is correct on these tiers. Spider out_of_scope_register for all five
# systems (network from the s40off Phase R run, as in B; GDC and planner replayed,
# the other three recorded), and employee out_of_range. Every outcome class is
# emitted, because declined + silent does not sum to 100 for systems that time
# out. Baseline cells on both tiers were never re-run at temperature 0
# (parity_overlay: out-of-scope tiers left default) until the second batch.
# D8, one decline rule: `declined` counts explicit decline statements and typed
# refusals only (paper_inputs.decline_channel); a refusal-scored cell that is neither
# (an SQL error, an unparseable plan with no decline text, an empty result) is
# `failed`. declined_explicit + declined_typed = declined, and declined + failed +
# silent_wrong + crash + timeout = n. Before D8, `declined` was every refusal.
_OOS_CLASSES = ("declined", "silent_wrong", "crash", "timeout",
                "declined_explicit", "declined_typed", "failed")
def oos_class(dom, r, g):
    """The Table 5 class of one decline-tier cell: the s48 outcome, with `correct`
    (any refusal) split by decline channel."""
    o = outcome(dom, r, g)
    if o != "correct":
        return o
    ch = pi.decline_channel(dom, r)[0]
    return "failed" if ch == "failed" else "declined_" + ch
def oos_rate(sc, cls):
    if cls == "declined":
        return rate([("declined" if v.startswith("declined_") else v) for v in sc], cls)
    return rate(sc, cls)
for sysname in SYSTEMS:
    per_dom, used = {}, {}
    for d in SPIDER:
        used[d] = [r for r in _load(pi.spider_file(d, sysname)) if r.get("system") == sysname
                   and GOLD_SPIDER[d].get(r["qid"], {}).get("tier") == "out_of_scope_register"]
        per_dom[d] = [oos_class(d, r, GOLD_SPIDER[d][r["qid"]]) for r in used[d]]
    groups = [(d, per_dom[d]) for d in SPIDER] + [("spider_pooled", sum(per_dom.values(), []))]
    used["spider_pooled"] = sum((used[d] for d in SPIDER), [])
    rows, gold, fn = syn_rows("employee", sysname, "out_of_range")
    rows = pi.scored(rows, gold)
    groups.append(("employee", [oos_class("employee", r, gold[r["qid"]]) for r in rows]))
    used["employee"] = rows
    for dom, sc in groups:
        for cls in _OOS_CLASSES:
            k, n, p = oos_rate(sc, cls)
            emit(fig_table="Table5", domain=dom, system=sysname,
                 tier_group="out_of_range" if dom == "employee" else "out_of_scope_register",
                 scoring="strict", metric=cls, value_pct=p,
                 numerator=k, denominator=n,
                 source=(f"{sysname} REPLAY" if sysname in pi.REPLAYED else f"recorded rescored {COMPARATOR_VERSION}")
                 + rerun_note(used[dom]) if dom == "employee"
                 else prov_of(sysname, "PhaseR s40off" if sysname == "network" else "recorded") + rerun_note(used[dom]),
                 comparator=COMPARATOR_VERSION)

# write CSV. paper_numbers.csv is CANONICAL and holds the temperature-0 (parity) run
# (PARITY_TEMP0=1) — a vanilla invocation (PARITY unset) writes the provenance
# snapshot instead, so it can never silently regress the canonical file back to
# pre-parity baseline-default-temperature numbers.
check_overlays()   # every cell a present re-run file holds must have replaced a recorded cell
out = ROOT / "reports" / ("paper_numbers.csv" if PARITY else "paper_numbers_default_temp.csv")
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["fig_table", "domain", "system", "tier_group", "scoring",
                                       "metric", "value_pct", "numerator", "denominator", "source", "comparator"])
    w.writeheader()
    for r in ROWS:
        w.writerow(r)
print(f"wrote {out} ({len(ROWS)} rows)")
# The engine the REPLAY cells (GDC, planner) ran on: one sha over the engine files'
# hashes, beside the CSV it produced. The CSV itself is unchanged by this record.
import hashlib, json                                                         # noqa: E402
from gdc.contract import CONTRACT_VERSION                                # noqa: E402
_eh = engine_hashes()
eng_out = out.with_name(out.stem + ".engine.json")
eng_out.write_text(json.dumps({
    "csv": out.name, "csv_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
    "replayed_systems": list(pi.REPLAYED), "engine_sha256": engine_sha(_eh),
    "contract_version": CONTRACT_VERSION, "comparator_version": COMPARATOR_VERSION,
    "engine_files": _eh}, indent=1) + "\n")
print(f"wrote {eng_out} (engine {engine_sha(_eh)[:12]})")
if PARITY:
    # per-cell provenance (Decision 6): one row per replaced cell per recorded file holding it
    prov_out = ROOT / "reports" / "paper_numbers.overlay_provenance.csv"
    with open(prov_out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["source_file", "overlay_file", "system", "qid", "seed"])
        w.writerows(provenance_rows())
    print(f"wrote {prov_out} ({len(provenance_rows())} replaced cells)")
    print("\n" + coverage_report())

print("\nEmployee in-contract (Table2) — STRICT vs TOLERANT (D9):")
for label in ("strict", "tolerant"):
    print(f"  [{label}]")
    for r in ROWS:
        if r["fig_table"] == "Table2" and r["domain"] == "employee" and r["scoring"] == label and r["metric"] == "silent_wrong":
            acc_r = next(x for x in ROWS if x["fig_table"] == "Table2" and x["domain"] == "employee" and x["scoring"] == label
                         and x["system"] == r["system"] and x["metric"] == "accuracy")
            print(f"    {r['system']:<12} acc {acc_r['value_pct']:>5}% ({acc_r['numerator']}/{acc_r['denominator']})"
                  f"   silent {r['value_pct']:>5}% ({r['numerator']}/{r['denominator']})")
print("\nDecline readings (D8):", len(pi.DECLINE_READINGS), "cells read by hand; Table 5 classes use them")
print("\nSpider in-contract (Fig1/2/Table3) — STRICT vs TOLERANT:")
for label in ("strict", "tolerant"):
    print(f"  [{label}]")
    for r in ROWS:
        if r["fig_table"] == "Fig1/Fig2/Table3" and r["scoring"] == label and r["metric"] == "silent_wrong":
            acc_r = next(x for x in ROWS if x["fig_table"] == "Fig1/Fig2/Table3" and x["scoring"] == label
                         and x["system"] == r["system"] and x["metric"] == "accuracy")
            print(f"    {r['system']:<12} acc {acc_r['value_pct']:>5}% ({acc_r['numerator']}/{acc_r['denominator']})"
                  f"   silent {r['value_pct']:>5}% ({r['numerator']}/{r['denominator']})")
