#!/usr/bin/env python
"""INDEPENDENT gold key for eval_corpus_spider_wta_1.json -- third-party, EXECUTED
Spider gold SQL, not hand-authored. Mirrors evidence/gold/build_gold.py's discipline
(zero engine imports -- no gdc/ engine modules, contract, or primitives) but the "gold
logic" IS the verbatim Spider gold SQL itself, executed directly against the real
wta_1.sqlite -- there is no per-query hand-written pandas block the way build_gold.py/
build_gold_employee.py have, because the whole point of a Spider domain is using
third-party-authored answers, not ours.

wta_1/*.pkl (built for a future harness-wiring step's register) are NOT used here --
gold is computed straight from the real .sqlite for byte-exact fidelity to Spider's
authored intent; that's a separate concern from what a live adapter's register holds.

Outputs: evidence/gold/gold_spider_wta_1.json + gold_spider_wta_1_digest.txt + a
printed sanity summary. Discipline: FLAG anomalies, never guess or edit the corpus.

    python evidence/gold/build_gold_spider_wta_1.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from spider_gold_common import execute_gold  # noqa: E402

CORPUS = ROOT / "evaluation/domains/spider_wta_1/eval_corpus_spider_wta_1.json"
SQLITE = ROOT / "spider" / "wta_1.sqlite"
OUT_DIR = Path(__file__).resolve().parent
FLAGS: list[str] = []


def main() -> None:
    corpus = json.loads(CORPUS.read_text())
    queries = corpus["queries"]

    gold = {}
    for q in queries:
        qid = q["id"]
        if q["expected_behavior"] == "refuse":
            gold[qid] = {"id": qid, "tier": q["tier"], "architectural_gap": q.get("architectural_gap"),
                         "result_kind": None, "ordered": False,
                         "value": None, "expected_behavior": "refuse", "row_count": 0}
            continue
        try:
            r = execute_gold(q["gold_sql"], SQLITE)
        except Exception as exc:                             # noqa: BLE001
            FLAGS.append(f"{qid}: SQL EXECUTION FAILED -- {type(exc).__name__}: {exc}")
            continue
        gold[qid] = {"id": qid, "tier": q["tier"], "architectural_gap": q.get("architectural_gap"),
                     "result_kind": r["result_kind"],
                     "ordered": r["ordered"], "value": r["value"],
                     "expected_behavior": "answer", "row_count": r["row_count"]}

    # ── sanity checks ──
    sane = []

    def chk(name, ok, detail=""):
        sane.append((name, ok, detail))
        if not ok:
            FLAGS.append(f"SANITY {name}: {detail}")

    answers = [g for g in gold.values() if g["expected_behavior"] == "answer"]
    refusals = [g for g in gold.values() if g["expected_behavior"] == "refuse"]
    n_expected_answer = sum(1 for q in queries if q["expected_behavior"] == "answer")
    n_expected_refuse = sum(1 for q in queries if q["expected_behavior"] == "refuse")
    chk("ids_present", len(gold) == len(queries), f"{len(gold)}/{len(queries)}")
    chk("answer_count", len(answers) == n_expected_answer, f"{len(answers)} vs {n_expected_answer}")
    chk("refuse_count", len(refusals) == n_expected_refuse, f"{len(refusals)} vs {n_expected_refuse}")

    def has_nan(v):
        if isinstance(v, float):
            return v != v
        if isinstance(v, list):
            return any(has_nan(x) for rec in v for x in (rec.values() if isinstance(rec, dict) else [rec]))
        return False
    nan_ok = not any(has_nan(g["value"]) for g in answers if g["value"] is not None)
    chk("no_nan_in_answers", nan_ok)

    none_scalar = [g["id"] for g in answers if g["result_kind"] == "scalar" and g["value"] is None]
    chk("no_null_scalars", not none_scalar, str(none_scalar))

    large = [(g["id"], g["row_count"]) for g in answers if g["row_count"] > 1000]
    if large:
        FLAGS.append(f"large gold results (row_count>1000): {large} -- real Spider queries with no "
                     f"WHERE filter over a big table (e.g. all ~20k players); not an error, just "
                     f"worth knowing before wiring to a live comparator.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "gold_spider_wta_1.json").write_text(json.dumps(gold, indent=2, default=str))

    qmap = {q["id"]: q for q in queries}
    lines = ["GOLD DIGEST -- wta_1 (Spider third-party gold, executed verbatim against the real .sqlite)", ""]
    for qid in [q["id"] for q in queries]:
        g, q = gold.get(qid), qmap[qid]
        if g is None:
            lines += [f"[{qid}] EXECUTION FAILED -- see FLAGS", ""]
            continue
        lines.append(f"[{qid}] ({g['tier']})  {q['question']}")
        lines.append(f"    SQL: {q['gold_sql']}")
        if g["expected_behavior"] == "refuse":
            lines.append(f"    GOLD: REFUSE -- {q.get('reason', '')}")
        else:
            v = g["value"]
            vs = (json.dumps(v) if g["result_kind"] == "scalar"
                  else json.dumps(v[:6]) + (f" … (+{len(v) - 6} more)" if len(v) > 6 else ""))
            lines.append(f"    GOLD ({g['result_kind']}, ordered={g['ordered']}, rows={g['row_count']}): {vs}")
        lines.append("")
    if FLAGS:
        lines += ["", "================ FLAGS (architect to resolve; NOT auto-fixed) ================"] + [f"  - {f}" for f in FLAGS]
    else:
        lines += ["", "FLAGS: none"]
    (OUT_DIR / "gold_spider_wta_1_digest.txt").write_text("\n".join(lines))

    print("=== gold_spider_wta_1 sanity summary ===")
    for name, ok, detail in sane:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} {('(' + detail + ')') if detail else ''}")
    print(f"\n  ids={len(gold)}  answers={len(answers)}  refusals={len(refusals)}")
    print(f"  FLAGS: {len(FLAGS)}")
    for f in FLAGS:
        print(f"    - {f}")
    print(f"\n  -> {OUT_DIR}/gold_spider_wta_1.json, gold_spider_wta_1_digest.txt")


if __name__ == "__main__":
    main()
