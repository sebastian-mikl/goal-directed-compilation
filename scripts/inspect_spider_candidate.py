#!/usr/bin/env python
"""Deep, standalone inspection of ONE Spider candidate database, before
committing to it as an eval domain — a ranking score alone can hide real
problems a human needs to see directly (e.g. "numeric" columns that are
really foreign-key IDs, or a "categorical" column that's really near-unique
free text). Same spirit as extract_spider_schema.py (which dumped wta_1/
world_1/employee_hire_evaluation before those were chosen), generalized to
any db_id/.sqlite/Q&A-data triple so it's reusable for whichever candidate
comes up next, not just one.

WHAT THIS DOES:
  Step 1 — schema: opens the .sqlite file read-only, dumps every table's
    columns (name/type/PK) + foreign keys + row count via PRAGMA, same
    format as extract_spider_schema.py's dumps. Cross-references every
    NUMERIC-typed column on the "best table" (the one rank_all_spider_
    databases.py's pick_best_table() would pick — same function, imported
    directly) against the real PK/FK list: a column that's numeric-typed
    AND a primary/foreign key is flagged distinctly, since counting a bare
    ID as a "numeric" scoring signal (implying something measurable like
    weight/price/count) is exactly the kind of ranking-score artifact this
    check exists to catch. Also flags a "categorical" column whose real
    distinct-value count sits suspiciously close to the row count (i.e.
    near-unique, not a useful bounded category) even though it cleared the
    ranking script's cardinality bound.

  Step 2 — questions: loads a Q&A data file (json/parquet/csv — e.g. the
    combined train+dev file from combine_spider_train_dev.py), filters to
    the target db_id, reports the real total count, classifies every
    question single_table/multi_table/compound using the EXACT SAME
    classify_query() as rank_all_spider_databases.py (imported directly,
    not reimplemented, so this check can't silently diverge from whatever
    actually produced the ranking score), estimates paraphrase density
    (questions sharing byte-identical gold SQL — Spider commonly has 2-3
    phrasings of the same underlying query), and prints a complexity-
    stratified sample (plain / filter / aggregate / group_by / order_by /
    multi-or-compound) rather than just the first N rows in file order, so
    the real variety — or lack of it — is visible.

Read-only: never writes to the .sqlite file or the Q&A data file.

USAGE:
    python scripts/inspect_spider_candidate.py bike_1 /path/to/bike_1.sqlite /path/to/combined_data.parquet

A synthetic fixture covering every claimed check (an ID column masquerading as
numeric, a near-unique "categorical" column, paraphrase duplicates, and a spread
of complexity tiers) is in selftest(); the real Spider .sqlite files and the
combined train+dev parquet are not included in this repo.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import rank_all_spider_databases as ranker  # noqa: E402 — reuse, don't reimplement


# ── Step 1: schema ───────────────────────────────────────────────────────────

def dump_schema(db_id: str, sqlite_path: Path, timeout_s: float) -> dict:
    conn = ranker.open_with_timeout(sqlite_path, timeout_s)
    try:
        tables = ranker.list_tables(conn)
        if not tables:
            raise ValueError(f"{sqlite_path} has no tables")

        print("=" * 100)
        print(f"db_id={db_id!r}  file={sqlite_path}")
        print("=" * 100)

        table_dumps = {}
        for t in tables:
            cols = ranker.table_info(conn, t)
            fks = ranker.foreign_keys(conn, t)
            n_rows = ranker.row_count(conn, t)
            table_dumps[t] = {"columns": cols, "fks": fks, "row_count": n_rows}
            print(f"\n  TABLE {t}  ({len(cols)} columns, {n_rows} rows)")
            fk_from_cols = {fk[0] for fk in fks}
            for c in cols:
                pk_flag = "  PK" if c["pk"] else ""
                fk_flag = "  FK" if c["name"] in fk_from_cols else ""
                nn_flag = "  NOT NULL" if c["notnull"] else ""
                print(f"    {c['name']:<30s} {c['type']:<12s}{pk_flag}{fk_flag}{nn_flag}")
            if fks:
                print("    foreign keys:")
                for from_col, to_table, to_col in fks:
                    print(f"      {t}.{from_col} -> {to_table}.{to_col}")

        picked = ranker.pick_best_table(conn, tables)
        best_name, best_cols = picked
        best = ranker.analyze_best_table(conn, best_name, best_cols)
        fk_cols = {fk[0] for fk in table_dumps[best_name]["fks"]}
        pk_cols = {c["name"] for c in best_cols if c["pk"]}

        print(f"\n  BEST TABLE (most numeric columns) = {best_name!r}")
        print(f"  ranking script's claim: numeric_column_count={best['numeric_column_count']}, "
              f"categorical_column_count={best['categorical_column_count']}, "
              f"row_count={best['best_table_row_count']}")

        numeric_cols = [c["name"] for c in best_cols
                        if ranker.classify_column_type(c["type"]) == "numeric"]
        id_like_numeric = [c for c in numeric_cols if c in fk_cols or c in pk_cols]
        real_numeric = [c for c in numeric_cols if c not in fk_cols and c not in pk_cols]
        print(f"  numeric-typed columns on {best_name!r}: {numeric_cols}")
        if id_like_numeric:
            print(f"  !! {len(id_like_numeric)} of those are PK/FK identifiers, not real "
                  f"quantities: {id_like_numeric} -- these inflate numeric_column_count "
                  f"({best['numeric_column_count']}) without adding measurable variety")
        print(f"  genuinely-measurable numeric columns (excl. PK/FK ids): {len(real_numeric)} "
              f"{real_numeric}")

        if best["categorical_columns"]:
            print(f"  categorical columns (name, distinct count, / row count):")
            for name, nd in best["categorical_columns"]:
                near_unique = nd >= 0.8 * best["best_table_row_count"] and best["best_table_row_count"] > 0
                flag = "  !! near-unique, weak as a real category" if near_unique else ""
                print(f"    {name:<28s} {nd} / {best['best_table_row_count']}{flag}")
        elif best["cardinality_skipped"]:
            print("  categorical check SKIPPED -- best table too large for a real cardinality scan")
        else:
            print("  no categorical columns found on the best table")

        return {"tables": table_dumps, "best_table": best_name, "best": best,
                "real_numeric_columns": real_numeric, "id_like_numeric_columns": id_like_numeric}
    finally:
        conn.close()


# ── Step 2: questions ─────────────────────────────────────────────────────────

_AGG_RE = re.compile(r"\b(count|avg|sum|max|min)\s*\(", re.IGNORECASE)
_GROUPBY_RE = re.compile(r"\bgroup\s+by\b", re.IGNORECASE)
_ORDERBY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bwhere\b", re.IGNORECASE)


def complexity_tier(sql: str, table_class: str) -> str:
    """Rough, transparent tier for SAMPLING variety only — not a scoring
    signal, just so the printed example sample spans real complexity rather
    than whatever happens to be first in file order."""
    if table_class in ("multi_table", "compound"):
        return "multi_or_compound"
    if _GROUPBY_RE.search(sql):
        return "group_by"
    if _AGG_RE.search(sql):
        return "aggregate"
    if _ORDERBY_RE.search(sql):
        return "order_by"
    if _WHERE_RE.search(sql):
        return "filter"
    return "plain"


def analyze_questions(db_id: str, data_path: Path, real_tables: set[str],
                      sample_size: int) -> None:
    all_questions = ranker.load_dev_questions(data_path)
    questions = [q for q in all_questions if q["db_id"] == db_id]
    n = len(questions)
    print("\n" + "=" * 100)
    print(f"QUESTIONS for db_id={db_id!r} in {data_path.name}: {n} found")
    print("=" * 100)
    if n == 0:
        print("  (none -- this data file has no coverage for this db_id)")
        return

    classified = [(q, ranker.classify_query(q["query"], real_tables)) for q in questions]
    n_single = sum(1 for _, c in classified if c == "single_table")
    n_multi = sum(1 for _, c in classified if c == "multi_table")
    n_compound = sum(1 for _, c in classified if c == "compound")
    print(f"  single_table={n_single} ({n_single/n:.0%})  multi_table={n_multi} "
          f"({n_multi/n:.0%})  compound={n_compound} ({n_compound/n:.0%})")

    distinct_queries = {q["query"] for q in questions}
    print(f"\n  paraphrase density: {n} questions / {len(distinct_queries)} distinct gold "
          f"queries = {n/len(distinct_queries):.2f} avg phrasings per underlying query")
    if n / len(distinct_queries) >= 1.8:
        print(f"  !! high paraphrase density -- building a ~25-30 query subset without heavy "
              f"repetition will need deliberate de-duplication by query text, not just by "
              f"question count")

    tiers: dict[str, list[tuple[dict, str]]] = {}
    for q, cls in classified:
        tier = complexity_tier(q["query"], cls)
        tiers.setdefault(tier, []).append((q, cls))
    print(f"\n  complexity tiers present: {[(t, len(v)) for t, v in tiers.items()]}")

    print(f"\n  SAMPLE ({sample_size} questions, spread across tiers, deduped by query text):")
    sample: list[tuple[dict, str, str]] = []
    seen_queries: set[str] = set()
    tier_order = ["plain", "filter", "aggregate", "group_by", "order_by", "multi_or_compound"]
    round_robin = True
    while len(sample) < sample_size and round_robin:
        round_robin = False
        for tier in tier_order:
            if len(sample) >= sample_size:
                break
            for q, cls in tiers.get(tier, []):
                if q["query"] not in seen_queries:
                    sample.append((q, cls, tier))
                    seen_queries.add(q["query"])
                    round_robin = True
                    break
    for q, cls, tier in sample:
        print(f"    [{tier:<17s}{cls:>12s}]  Q: {q.get('question', '<no question field>')}")
        print(f"    {'':<32s}SQL: {q['query']}")


# ── driver ────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("db_id")
    p.add_argument("sqlite_path", type=Path)
    p.add_argument("qa_data_path", type=Path,
                   help="combined/dev/train Q&A data: .json / .parquet / .csv")
    p.add_argument("--sample-size", type=int, default=12)
    p.add_argument("--timeout-s", type=float, default=15.0)
    args = p.parse_args()

    if not args.sqlite_path.exists():
        print(f"ERROR: {args.sqlite_path} does not exist.")
        return 2
    if not args.qa_data_path.exists():
        print(f"ERROR: {args.qa_data_path} does not exist.")
        return 2

    schema = dump_schema(args.db_id, args.sqlite_path, args.timeout_s)
    real_tables = set(schema["tables"].keys())
    analyze_questions(args.db_id, args.qa_data_path, real_tables, args.sample_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
