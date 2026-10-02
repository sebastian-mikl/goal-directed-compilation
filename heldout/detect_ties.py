#!/usr/bin/env python
"""Data-grounded ranking ties for the held-out answer tier: scripts/detect_ties.py's
method (its inject_rank_expr, the same boundary rule and the same alternative-value
shaping) applied to the held-out registers. Writes heldout/tie_alternatives_heldout.json,
the tie set the tie-tolerant score of the held-out run uses. Built from the register and
the gold SQL only, before any held-out cell runs.

    python heldout/detect_ties.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from scripts.detect_ties import ANSWER, inject_rank_expr  # noqa: E402
from build_domains import HELDOUT  # noqa: E402

OUT = HERE / "tie_alternatives_heldout.json"


def main() -> int:
    alternatives, report = {}, []
    for d in HELDOUT:
        corpus = {q["id"]: q for q in json.loads((HERE / f"eval_corpus_spider_{d}.json").read_text())["queries"]}
        gold = json.loads((HERE / "gold" / f"gold_spider_{d}.json").read_text())
        schema = json.loads((HERE / d / "schema.json").read_text())
        tbl = schema["domain"]["register_table"]
        df = pd.read_pickle(HERE / d / f"{tbl}.pkl")
        con = sqlite3.connect(":memory:")
        df.to_sql(tbl, con, index=False)
        for qid, g in gold.items():
            if g.get("tier") not in ANSWER:
                continue
            sql = corpus[qid]["gold_sql"]
            if not re.search(r"\bLIMIT\b", sql, re.I):
                continue
            inj = inject_rank_expr(sql)
            if inj is None:
                continue
            rank_query, direction, k = inj
            rows = pd.read_sql_query(rank_query, con)
            rows = rows.sort_values("__rv", ascending=(direction == "ASC"), kind="mergesort").reset_index(drop=True)
            if len(rows) == 0:
                continue
            boundary = rows.loc[k - 1, "__rv"] if len(rows) >= k else rows["__rv"].iloc[-1]
            tied = rows[rows["__rv"] == boundary]
            mult = len(tied)
            proj_cols = [c for c in rows.columns if c != "__rv"]
            alt_rows = tied[proj_cols].to_dict("records")
            report.append((d, qid, g["tier"], k, mult, boundary, direction))
            if mult > 1:
                gv = g["value"]

                def clean(r):
                    return {kk: (None if isinstance(vv, float) and pd.isna(vv) else vv) for kk, vv in r.items()}
                if isinstance(gv, list):
                    alt_values = [[clean(r)] for r in alt_rows]
                elif isinstance(gv, dict):
                    alt_values = [clean(r) for r in alt_rows]
                else:
                    alt_values = [list(clean(r).values())[0] for r in alt_rows]
                alternatives[qid] = {"domain": d, "tier": g["tier"], "rank_by": rank_query,
                                     "direction": direction,
                                     "boundary": (float(boundary) if isinstance(boundary, (int, float)) else str(boundary)),
                                     "multiplicity": mult, "gold": gv, "alt_values": alt_values,
                                     "alt_rows": [clean(r) for r in alt_rows]}
        con.close()
    print(f"{'qid':12} {'tier':10} {'k':>2} {'ties@boundary':>13}  boundary")
    for d, qid, tier, k, mult, boundary, direction in report:
        print(f"{qid:12} {tier:10} {k:>2} {mult:>13}  {boundary} ({direction}){'  <== DATA TIE' if mult > 1 else ''}")
    OUT.write_text(json.dumps(alternatives, indent=2, default=str) + "\n")
    print(f"tie-affected held-out qids: {sorted(alternatives)}; wrote {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
