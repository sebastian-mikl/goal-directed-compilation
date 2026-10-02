#!/usr/bin/env python
"""INDEPENDENT gold key for the held-out Spider corpora (heldout/eval_corpus_spider_<db>.json)
-- third-party, EXECUTED Spider gold SQL, not hand-authored. A parameterised copy of
evidence/gold/build_gold_spider_wta_1.py: the same record shape, the same
spider_gold_common.execute_gold, the same sanity checks and digest; only the paths and
output names differ. Zero engine imports (no planner/, gdc/, contract or primitives).

Outputs: heldout/gold/gold_spider_<db>.json + gold_spider_<db>_digest.txt + a printed
sanity summary. Discipline: FLAG anomalies, never guess or edit the corpus.

    python heldout/build_gold.py            # every database in heldout/build_domains.HELDOUT
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "evidence" / "gold"))
sys.path.insert(0, str(HERE))
from spider_gold_common import execute_gold  # noqa: E402
from build_domains import HELDOUT  # noqa: E402

OUT_DIR = HERE / "gold"
FLAGS: list[str] = []


def main(db: str) -> None:
    FLAGS.clear()
    CORPUS = HERE / f"eval_corpus_spider_{db}.json"
    SQLITE = ROOT / HELDOUT[db][0]
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
    (OUT_DIR / f"gold_spider_{db}.json").write_text(json.dumps(gold, indent=2, default=str))

    qmap = {q["id"]: q for q in queries}
    lines = [f"GOLD DIGEST -- {db} (Spider third-party gold, executed verbatim against the real .sqlite)", ""]
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
    (OUT_DIR / f"gold_spider_{db}_digest.txt").write_text("\n".join(lines))

    print(f"=== gold_spider_{db} sanity summary ===")
    for name, ok, detail in sane:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} {('(' + detail + ')') if detail else ''}")
    print(f"\n  ids={len(gold)}  answers={len(answers)}  refusals={len(refusals)}")
    print(f"  FLAGS: {len(FLAGS)}")
    for f in FLAGS:
        print(f"    - {f}")
    print(f"\n  -> {OUT_DIR}/gold_spider_{db}.json, gold_spider_{db}_digest.txt")


if __name__ == "__main__":
    flagged = []
    for _db in HELDOUT:
        main(_db)
        flagged += FLAGS
    sys.exit(1 if flagged else 0)
