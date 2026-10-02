#!/usr/bin/env python
"""Build the held-out Spider domains by the pre-registered rule (heldout/PREREGISTRATION.md).

For each database in HELDOUT: every Spider question for it (combined_data.parquet,
file order), deduplicated by exact gold SQL text with the first phrasing kept (the
rule that reproduces the released corpora's 31 / 65 / 53 distinct queries); the
register table chosen by heldout/sql_tier.register_table; every question tiered by
heldout/sql_tier.tier_of; the register exported from the real .sqlite; schema.json
derived from the .sqlite by the column rule below. Nothing is hand-picked, reworded
or dropped. pandas + stdlib + sqlite3 only, zero engine imports.

Column rule (schema.json), applied to the register's columns in table order:
  id           primary key, a foreign-key column, or a name `id` / `*_id` (any case)
  numeric      declared numeric affinity (rank_all_spider_databases.classify_column_type)
  date         declared DATE / DATETIME / TIMESTAMP
  categorical  any other column with 2 <= distinct values <= 50 and distinct values <=
               half its non-null rows; its sorted values are listed
  text         the rest
  match_strategy: numeric_compare (numeric), date_compare (date), exact (all others)
  nullable / null_count where a column has nulls; dataset.row_count.
  domain.description names the database and all of its tables, nothing else.

Outputs, under heldout/:
  eval_corpus_spider_<db>.json, <db>/<register>.pkl, <db>/schema.json

    python heldout/build_domains.py            # every database in HELDOUT
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
sys.path.insert(0, str(HERE))
from sql_tier import register_table, tier_of  # noqa: E402

RULE_VERSION = "heldout-r1"
COMBINED = ROOT / "combined_data.parquet"
DEV = ROOT / "dev_data.parquet"
# db_id -> (sqlite path relative to ROOT, question-id prefix)
HELDOUT = {
    "employee_hire_evaluation": ("spider/employee_hire_evaluation.sqlite", "hire"),
    "train_station": ("database/train_station/train_station.sqlite", "station"),
}
ANSWER_TIERS = ("filter", "aggregate", "group_by", "order_by")

_NUMERIC_TYPE_RE = re.compile(r"\b(int|integer|tinyint|smallint|mediumint|bigint|real|float|double|"
                              r"numeric|decimal|number)\b", re.IGNORECASE)
_DATE_TYPE_RE = re.compile(r"\b(date|datetime|timestamp)\b", re.IGNORECASE)
_ID_NAME_RE = re.compile(r"^(id|.*_id)$", re.IGNORECASE)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    return conn


def db_tables(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]


def questions_for(db_id: str) -> tuple[list[dict], int]:
    """(deduplicated questions in Spider file order, raw count)."""
    comb = pd.read_parquet(COMBINED)
    dev = pd.read_parquet(DEV)
    dev_keys = set(zip(dev.db_id, dev.question, dev["query"]))
    sub = comb[comb.db_id == db_id]
    seen, out = set(), []
    for idx, r in sub.iterrows():
        if r["query"] in seen:
            continue
        seen.add(r["query"])
        split = "dev" if (r.db_id, r.question, r["query"]) in dev_keys else "train"
        out.append({"question": r.question, "gold_sql": r["query"], "spider_split": split,
                    "spider_row": int(idx)})
    return out, len(sub)


def column_schema(conn, table: str, df: pd.DataFrame) -> dict:
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    fk_cols = {r[3] for r in conn.execute(f'PRAGMA foreign_key_list("{table}")')}
    for other in db_tables(conn):
        for r in conn.execute(f'PRAGMA foreign_key_list("{other}")'):
            if r[2].lower() == table.lower():
                fk_cols.add(r[4])
    cols, types = {}, {"text": [], "categorical": [], "numeric": [], "id": [], "date": []}
    for _cid, name, decl, _nn, _dflt, pk in info:
        s = df[name]
        nonnull = int(s.notna().sum())
        nuniq = int(s.nunique(dropna=True))
        if pk or name in fk_cols or _ID_NAME_RE.match(name):
            typ = "id"
        elif _NUMERIC_TYPE_RE.search(decl or ""):
            typ = "numeric"
        elif _DATE_TYPE_RE.search(decl or ""):
            typ = "date"
        elif 2 <= nuniq <= 50 and nuniq <= nonnull / 2:
            typ = "categorical"
        else:
            typ = "text"
        entry = {"type": typ}
        if typ == "categorical":
            entry["unique_count"] = nuniq
            entry["categories"] = sorted(map(str, s.dropna().unique()))
        entry["match_strategy"] = {"numeric": "numeric_compare", "date": "date_compare"}.get(typ, "exact")
        if typ == "date":
            entry.update({"format": "as_stored", "non_null_count": nonnull,
                          "min": None if not nonnull else s.dropna().min(),
                          "max": None if not nonnull else s.dropna().max()})
        if nonnull < len(s):
            entry.update({"nullable": True, "null_count": int(len(s) - nonnull)})
        cols[name] = entry
        types[typ].append(name)
    return {"column_types": types, "columns": cols}


def build(db_id: str) -> dict:
    rel, prefix = HELDOUT[db_id]
    sqlite_path = ROOT / rel
    conn = connect(sqlite_path)
    tables = db_tables(conn)
    rows = {t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
    qs, n_raw = questions_for(db_id)
    reg, alone = register_table(qs, tables, rows)

    queries = []
    for i, q in enumerate(qs, 1):
        t = tier_of(q["gold_sql"], tables, reg)
        rec = {"id": f"{prefix}_{i:02d}", "tier": t["tier"]}
        if t["tier"] == "out_of_contract_aggregate":
            rec["architectural_gap"] = t["architectural_gap"]
        rec.update({"tables": t["tables"], "question": q["question"], "gold_sql": q["gold_sql"],
                    "expected_behavior": t["expected_behavior"]})
        if t["tier"] == "out_of_scope_register":
            others = [x for x in t["tables"] if x.lower() != reg.lower()]
            rec["reason"] = (f"reads table(s) {others} -- this domain's single-table register only "
                             f"exposes {reg!r}; the data this question needs is not visible to the system.")
        rec.update({"sql_structure": t["sql_structure"], "spider_split": q["spider_split"],
                    "spider_row": q["spider_row"]})
        queries.append(rec)

    counts = {"answer": sum(q["tier"] in ANSWER_TIERS for q in queries),
              "out_of_scope_register": sum(q["tier"] == "out_of_scope_register" for q in queries),
              "out_of_contract_aggregate": sum(q["tier"] == "out_of_contract_aggregate" for q in queries)}
    splits = sorted({q["spider_split"] for q in queries})
    meta = {
        "domain": db_id,
        "source": (f"Spider (Yu et al., 2018) {'/'.join(splits)}-split questions + official gold SQL "
                   f"(combined_data.parquet), verbatim -- third-party authored, not hand-written for this project."),
        "purpose": ("Held-out evaluation domain: a Spider database no system, prompt or engine fix was "
                    "developed on. Built mechanically by heldout/build_domains.py under the rule fixed in "
                    "heldout/PREREGISTRATION.md before any held-out cell ran."),
        "rule_version": RULE_VERSION,
        "selection_note": (f"{n_raw} Spider questions covering {len(qs)} distinct gold queries, deduplicated by "
                           f"exact gold SQL text (first phrasing kept). Every distinct query kept; none "
                           f"authored, reworded, dropped or padded."),
        "register_table": reg,
        "register_rule": (f"the table the most deduplicated questions read alone, ties to more rows then "
                          f"name: {alone}"),
        "tier_counts": counts,
        "tiers": ("Mechanical (heldout/sql_tier.py): out_of_scope_register if the gold SQL reads any "
                  "table other than the register; else out_of_contract_aggregate if it uses a construct "
                  "outside the Goal vocabulary (architectural_gap lists each); else the SQL structure "
                  "(filter/aggregate/group_by/order_by), the answer tier."),
        "tier_convention_note": ("As in the released Spider corpora: out_of_scope_register questions are "
                                 "answerable in principle but need a table outside the register "
                                 "(expected_behavior=refuse); out_of_contract_aggregate questions are "
                                 "in scope by tau and excluded from scoring because the Goal vocabulary "
                                 "cannot express them (expected_behavior=answer, gold executed)."),
    }
    out_dir = HERE / db_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (HERE / f"eval_corpus_spider_{db_id}.json").write_text(
        json.dumps({"meta": meta, "queries": queries}, indent=2, ensure_ascii=False) + "\n")

    df = pd.read_sql_query(f'SELECT * FROM "{reg}"', conn)
    df.to_pickle(out_dir / f"{reg}.pkl")
    schema = {"dataset": {"row_count": int(len(df))}}
    schema.update(column_schema(conn, reg, df))
    schema["domain"] = {
        "description": (f"The Spider (Yu et al., 2018) database {db_id}, with tables "
                        f"{', '.join(tables[:-1])} and {tables[-1]}." if len(tables) > 1 else
                        f"The Spider (Yu et al., 2018) database {db_id}."),
        "source_note": (f"Spider (Yu et al., 2018) benchmark database {db_id}, used as-is -- schema and "
                        f"data are third-party, not authored for this project."),
        "register_table": reg,
        "register_table_note": (f"Single-table register by the pre-registered rule (heldout/PREREGISTRATION.md): "
                                f"{reg!r} is the only visible table; a question needing another table is a "
                                f"refusal case in this domain's corpus."),
    }
    (out_dir / "schema.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False, default=str) + "\n")
    conn.close()
    return {"db_id": db_id, "register": reg, "alone": alone, "raw": n_raw, "distinct": len(qs),
            "counts": counts, "rows": int(len(df)), "splits": splits}


def main(argv=None) -> int:
    for db_id in HELDOUT:
        s = build(db_id)
        print(f"{db_id}: {s['raw']} questions, {s['distinct']} distinct ({'/'.join(s['splits'])}); register "
              f"{s['register']} ({s['rows']} rows; read alone {s['alone']}); tiers {s['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
