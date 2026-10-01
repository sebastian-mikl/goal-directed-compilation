#!/usr/bin/env python
"""Data-grounded tie detection for in-contract ranked queries.

For every in-contract answer-tier query whose gold SQL ends in ORDER BY ... LIMIT,
re-execute the ranking against the register in SQLite (the SAME engine that built
gold) and find EVERY row tied with gold at the ranking boundary. A query is
"tie-affected" only if >1 row ties at the boundary IN THE DATA — never inferred
from an answer "looking plausible". Emits reports/tie_alternatives.json:
qid -> list of gold-shaped alternative values (the tied rows' projected columns),
which the tie-tolerant scorer accepts in addition to gold.

The method is conservative by construction: for a NON-tie query exactly one row
sits at the boundary, so the accepted set is {gold} alone and strict == tolerant.
"""
from __future__ import annotations
import json, re, sqlite3, sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ANSWER = {"aggregate", "filter", "group_by", "order_by"}
DOMTBL = {"wta_1": ("evaluation/domains/spider_wta_1/matches.pkl", "matches"),
          "world_1": ("evaluation/domains/spider_world_1/country.pkl", "country"),
          "bike_1": ("evaluation/domains/spider_bike_1/trip.pkl", "trip")}

def load_corpus(d):
    c = json.loads((ROOT / f"evaluation/domains/spider_{d}/eval_corpus_spider_{d}.json").read_text())
    entries = c["queries"] if isinstance(c, dict) else c
    return {e["id"]: e for e in entries}

def inject_rank_expr(gold_sql: str):
    """Split '<body> ORDER BY <expr>[ ASC|DESC] LIMIT <k>' and return
    (rank_query, direction, k): rank_query selects the body PLUS the ORDER BY
    expression as __rv, so the boundary value is observable even when it is an
    aggregate (COUNT(*)) computed in the body."""
    m = re.search(r"\bORDER\s+BY\b", gold_sql, re.I)
    if not m:
        return None
    body, tail = gold_sql[:m.start()].rstrip(), gold_sql[m.end():].strip()
    lm = re.search(r"\bLIMIT\b", tail, re.I)
    if not lm:
        return None
    ob = tail[:lm.start()].strip().rstrip(",")
    k = int(re.findall(r"\d+", tail[lm.end():])[0])
    direction = "DESC" if re.search(r"\bDESC\b", ob, re.I) else "ASC"
    ob_expr = re.sub(r"\b(ASC|DESC)\b", "", ob, flags=re.I).strip()
    # inject ", <ob_expr> AS __rv" before the first FROM
    fm = re.search(r"\bFROM\b", body, re.I)
    rank_query = body[:fm.start()].rstrip() + f", {ob_expr} AS __rv " + body[fm.start():]
    return rank_query, direction, k

def main():
    alternatives = {}
    report = []
    for d, (pkl, tbl) in DOMTBL.items():
        gold = json.loads((ROOT / "evidence/gold" / f"gold_spider_{d}.json").read_text())
        corpus = load_corpus(d)
        df = pd.read_pickle(ROOT / pkl)
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
            rows = rows.sort_values("__rv", ascending=(direction == "ASC"),
                                    kind="mergesort").reset_index(drop=True)
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
                    return {kk: (None if isinstance(vv, float) and pd.isna(vv) else vv)
                            for kk, vv in r.items()}
                # shape each tied row to gold.value's Python type so the tie-tolerant
                # scorer can pass it straight to s47 as an alternative gold value.
                if isinstance(gv, list):          # LIMIT-1 -> each alt is a 1-row list
                    alt_values = [[clean(r)] for r in alt_rows]
                elif isinstance(gv, dict):
                    alt_values = [clean(r) for r in alt_rows]
                else:                             # scalar gold -> single projected column
                    alt_values = [list(clean(r).values())[0] for r in alt_rows]
                alternatives[qid] = {"domain": d, "tier": g["tier"], "rank_by": rank_query,
                                     "direction": direction,
                                     "boundary": (float(boundary) if isinstance(boundary, (int, float)) else str(boundary)),
                                     "multiplicity": mult, "gold": gv, "alt_values": alt_values,
                                     "alt_rows": [clean(r) for r in alt_rows]}
        con.close()

    print(f"{'qid':10} {'tier':12} {'k':>2} {'ties@boundary':>13}  boundary")
    print("-" * 60)
    for d, qid, tier, k, mult, boundary, direction in report:
        flag = "  <== DATA TIE" if mult > 1 else ""
        print(f"{qid:10} {tier:12} {k:>2} {mult:>13}  {boundary} ({direction}){flag}")
    print(f"\ntie-affected in-contract qids: {sorted(alternatives)}")
    out = ROOT / "reports" / "tie_alternatives.json"
    out.write_text(json.dumps(alternatives, indent=2, default=str))
    print(f"wrote {out}")
    # show the tied rows for each affected qid
    for qid, info in alternatives.items():
        print(f"\n{qid} [{info['domain']}] — {info['multiplicity']} rows tied at boundary "
              f"{info['boundary']} ({info['direction']}):")
        for r in info["alt_rows"]:
            print("   ", json.dumps(r, default=str))
        print("    gold:", json.dumps(info["gold"], default=str))

if __name__ == "__main__":
    sys.exit(main())
