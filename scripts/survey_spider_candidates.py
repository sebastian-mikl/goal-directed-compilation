#!/usr/bin/env python
"""Spider dataset survey — find dev-set databases friendly to the GDC engine's
join-free operator vocabulary, as candidates for a third (independently
authored) evaluation domain.

Context: gdc/operators.py's OPERATORS dict has exactly four primitives —
filter, select, aggregate, compute — and NO join operator. A Spider (Yu et al.,
2018) database whose dev-set questions mostly require multi-table joins would
mostly produce automatic Unreachable refusals, not a meaningful test; what is
wanted is the same flat single-table shape as probe_domain/employees.pkl.
This is a READ-ONLY survey: it does not write any corpus/gold file, and does
not touch any existing repo file.

WHAT THIS DOES:
  1. Loads Spider's dev-set questions+gold SQL (db_id/question/query records)
     and, SEPARATELY and OPTIONALLY, its schema file (tables.json equivalent:
     db_id -> tables/columns/column types). The two are sourced independently
     — a schema failure no longer discards dev-set data that loaded fine.
  2. For each db_id: what fraction of its dev questions are answerable from
     ONE table (a conservative text heuristic on the gold SQL alone — see
     classify_query()'s docstring) — this is the PRIMARY signal and needs
     only the dev-set data, not tables.json. Table count / column type mix
     are a SECONDARY signal: authoritative if tables.json loaded, else a
     lower-bound estimate inferred from FROM-clause text (SCHEMA-FREE MODE,
     see infer_schema_from_queries()) rather than blocking the whole survey
     on a file that may be unobtainable.
  3. Ranks db_ids by: high single-table fraction, few tables, some column-type
     variety (when known), and a minimum question count — then prints the
     top N with 2-3 example (question, gold SQL) pairs per candidate.

WHY SCHEMA-FREE MODE EXISTS:
  Spider's tables.json is not always obtainable (network policy, package
  availability), while the dev-set Q&A data usually is. Since classify_query()
  only needs the gold SQL TEXT, the single-table-fraction ranking — the primary
  signal this survey is about — works from that data ALONE, so schema-free mode
  keeps the script runnable rather than blocked on a file that may not be
  gettable. It is labelled wherever it's used: table counts in that mode are a
  LOWER BOUND (only tables appearing in at least one available query's
  FROM/JOIN clause are counted — a table no sampled question touches is
  invisible to this method), and column type mix is not derivable this way at
  all.

  Run it:

      pip install datasets huggingface_hub --break-system-packages
      python scripts/survey_spider_candidates.py

  If you already have xlangai/spider pulled locally, export it and point
  --dev-json at the export (accepts .json / .parquet / .csv, auto-detected):

      ds['validation'].to_pandas().to_parquet('/tmp/spider_dev.parquet')
      python scripts/survey_spider_candidates.py --dev-json /tmp/spider_dev.parquet

  If you also obtain a real tables.json (official Spider release, or a
  community HF mirror reachable from your machine), add it for the full,
  non-inferred picture:

      python scripts/survey_spider_candidates.py \\
          --dev-json /tmp/spider_dev.parquet --tables-json /path/to/tables.json

Scope note: per Step 2's own instruction, this script does NOT fetch the
per-database .sqlite files — schema/questions are enough to rank candidates.
Pulling the actual data for whichever 3 databases get picked is a separate,
later step.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ── SQL classification (text heuristic on the gold `query` string) ──────────

_JOIN_RE = re.compile(r"\bjoin\b", re.IGNORECASE)
_SETOP_RE = re.compile(r"\b(intersect|union|except)\b", re.IGNORECASE)
_FROM_RE = re.compile(
    r"\bfrom\b(.*?)(?:\bwhere\b|\bgroup\s+by\b|\bhaving\b|\border\s+by\b|\blimit\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_TABLE_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def extract_from_clause(sql: str) -> str | None:
    """Raw text between FROM and the next WHERE/GROUP BY/HAVING/ORDER BY/LIMIT
    (or end of string). Naive on purpose — does not parse nested subqueries —
    but errs in the SAFE direction: a query this can't cleanly read is
    classified multi_table below, never silently assumed single_table."""
    m = _FROM_RE.search(sql)
    return m.group(1).strip() if m else None


def table_ref_count(from_clause: str) -> int:
    """Rough count of distinct table references: 1 (base) + JOINs + commas.
    A parenthesized subquery in FROM is treated as 2 (conservative — it is
    never a flat single-table read even though this doesn't parse it)."""
    if "(" in from_clause:
        return 2
    return 1 + len(_JOIN_RE.findall(from_clause)) + from_clause.count(",")


def classify_query(sql: str) -> str:
    """'single_table' | 'multi_table' | 'compound'.

    compound = INTERSECT/UNION/EXCEPT of two sub-selects — structurally
    different from a join (each side could individually be single-table) so
    it is kept as its own bucket rather than silently folded into either
    single or multi; report it separately, don't let it skew the fraction."""
    if _SETOP_RE.search(sql):
        return "compound"
    from_clause = extract_from_clause(sql)
    if from_clause is None:
        return "multi_table"
    return "single_table" if table_ref_count(from_clause) == 1 else "multi_table"


def infer_tables_from_from_clause(from_clause: str) -> set[str]:
    """Best-effort table-NAME extraction from a FROM clause: split on JOIN,
    then on comma within each piece, keep the first identifier token of each
    (the table name, before any alias/AS/ON). A parenthesized subquery can't
    be safely attributed a name, so it contributes nothing — the SAFE
    direction for a lower-bound estimate (undercounts, never overcounts)."""
    if "(" in from_clause:
        return set()
    names: set[str] = set()
    for join_piece in _JOIN_RE.split(from_clause):
        for comma_piece in join_piece.split(","):
            toks = _TABLE_TOKEN_RE.findall(comma_piece.strip())
            if toks:
                names.add(toks[0].lower())
    return names


# ── schema summary (Spider's tables.json shape) — authoritative path ────────

def summarize_schema(table_entry: dict) -> dict:
    """table_entry: one record of Spider's tables.json — db_id,
    table_names_original, column_names_original ([[table_idx, col_name], ...],
    table_idx=-1 for the '*' pseudo-column), column_types (parallel list of
    'text'/'number'/'time'/'boolean'/'others')."""
    table_names = table_entry["table_names_original"]
    col_names = table_entry["column_names_original"]
    col_types = table_entry["column_types"]
    per_table_cols = {i: 0 for i in range(len(table_names))}
    type_counts: dict[str, int] = {}
    for (tbl_idx, _col_name), ctype in zip(col_names, col_types):
        if tbl_idx == -1:
            continue
        per_table_cols[tbl_idx] = per_table_cols.get(tbl_idx, 0) + 1
        type_counts[ctype] = type_counts.get(ctype, 0) + 1
    return {
        "db_id": table_entry["db_id"],
        "n_tables": len(table_names),
        "n_columns": sum(per_table_cols.values()),
        "per_table_columns": {table_names[i]: n for i, n in per_table_cols.items()},
        "type_counts": type_counts,
        "inferred": False,
    }


# ── schema-free fallback — no tables.json available ─────────────────────────

def infer_schema_from_queries(queries: list[str]) -> dict:
    """A LOWER-BOUND, schema-free stand-in for summarize_schema(): the union
    of table names seen across every query's FROM clause for one db_id. NO
    type information is derivable this way. `n_tables` can UNDER-count the
    real schema (a table no sampled question touches is invisible) — never
    presented as authoritative, always tagged inferred=True and printed with
    an explicit lower-bound warning (see print_report())."""
    tables: set[str] = set()
    for sql in queries:
        fc = extract_from_clause(sql)
        if fc:
            tables |= infer_tables_from_from_clause(fc)
    return {
        "n_tables": len(tables),
        "n_columns": None,
        "per_table_columns": {},
        "type_counts": {},
        "inferred": True,
        "inferred_table_names": sorted(tables),
    }


# ── data loading — dev questions and schema are sourced INDEPENDENTLY ───────

def load_dev_questions_local(path: Path) -> list[dict]:
    """.json (official Spider dev.json shape: a list of records) or
    .parquet/.csv (the shape a HF `datasets` pull naturally exports to, e.g.
    ds['validation'].to_pandas().to_parquet(...)) — auto-detected by
    extension. Every record needs at least 'db_id' and 'query'; 'question' is
    used for the printed examples but its absence doesn't block ranking."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(path.read_text())
    if suffix in (".parquet", ".pq"):
        import pandas as pd
        return pd.read_parquet(path).to_dict("records")
    if suffix == ".csv":
        import pandas as pd
        return pd.read_csv(path).to_dict("records")
    raise ValueError(f"unrecognized dev-data file type {path.suffix!r} (expected .json/.parquet/.csv)")


def load_schemas_local(path: Path) -> dict:
    schemas_raw = json.loads(path.read_text())
    return {t["db_id"]: summarize_schema(t) for t in schemas_raw}


def fetch_dev_questions_hf() -> list[dict]:
    from datasets import load_dataset
    ds = load_dataset("xlangai/spider", split="validation")
    return [dict(row) for row in ds]


def fetch_schemas_hf() -> dict:
    """Best-effort live fetch. NOT verified against the real repo in this
    session (see module docstring: tables.json does not appear to exist in
    xlangai/spider) — reports exactly what failed rather than guessing."""
    from huggingface_hub import hf_hub_download
    tables_path = hf_hub_download(
        repo_id="xlangai/spider", repo_type="dataset", filename="tables.json")
    schemas_raw = json.loads(Path(tables_path).read_text())
    return {t["db_id"]: summarize_schema(t) for t in schemas_raw}


# ── ranking ───────────────────────────────────────────────────────────────

def rank_candidates(dev_questions: list[dict], schemas: dict, *,
                     min_questions: int, max_tables: int) -> list[dict]:
    """`schemas`: db_id -> summarize_schema() dict; may be empty or partial.
    Any db_id present in dev_questions but ABSENT from schemas falls back to
    infer_schema_from_queries() instead of being silently dropped — this is
    what makes ranking usable from dev-set data alone (see module docstring).
    `max_tables` is applied against whichever n_tables is available (real or
    inferred) — in schema-free mode this can under-filter (let a genuinely
    larger database through), which is why that mode is always flagged in
    the report rather than presented as equivalent to the real thing."""
    by_db: dict[str, list[dict]] = {}
    for q in dev_questions:
        by_db.setdefault(q["db_id"], []).append(q)

    rows = []
    for db_id, qs in by_db.items():
        n = len(qs)
        if n < min_questions:
            continue
        sch = schemas.get(db_id)
        if sch is None:
            sch = infer_schema_from_queries([q["query"] for q in qs])
            sch["db_id"] = db_id
        if sch["n_tables"] > max_tables:
            continue
        classified = [(q, classify_query(q["query"])) for q in qs]
        n_single = sum(1 for _, c in classified if c == "single_table")
        n_multi = sum(1 for _, c in classified if c == "multi_table")
        n_compound = sum(1 for _, c in classified if c == "compound")
        frac_single = n_single / n if n else 0.0
        type_variety = len(sch["type_counts"])
        score = frac_single * 100 - sch["n_tables"] * 5 + min(type_variety, 3) * 3
        examples = [q for q, c in classified if c == "single_table"][:3]
        rows.append({
            **sch, "n_questions": n, "n_single_table": n_single,
            "n_multi_table": n_multi, "n_compound": n_compound,
            "frac_single_table": frac_single, "score": score, "examples": examples,
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def print_report(rows: list[dict], top_n: int) -> None:
    shown = rows[:top_n]
    any_inferred = any(r.get("inferred") for r in shown)
    print("=" * 100)
    print(f"TOP {len(shown)} CANDIDATE DATABASES (of {len(rows)} passing the min-questions/"
          f"max-tables filter)")
    if any_inferred:
        print("-" * 100)
        print("NOTE: rows marked [INFERRED SCHEMA] have no real tables.json. Their table count")
        print("is a LOWER BOUND from FROM/JOIN-clause text across available queries only -- a")
        print("table no sampled question touches is invisible to this method, and column type")
        print("mix is not derivable at all this way. Verify against a real schema before final")
        print("selection; treat these rows as promising leads, not confirmed rankings.")
    print("=" * 100)
    for r in shown:
        tag = "  [INFERRED SCHEMA -- lower bound]" if r.get("inferred") else ""
        print(f"\ndb_id={r['db_id']!r}  score={r['score']:.1f}{tag}")
        print(f"  tables={r['n_tables']}  columns={r['n_columns']}  types={r['type_counts']}")
        if r.get("inferred"):
            print(f"  inferred_table_names={r.get('inferred_table_names')}")
        else:
            print(f"  per_table_columns={r['per_table_columns']}")
        print(f"  questions={r['n_questions']}  single_table={r['n_single_table']} "
              f"({r['frac_single_table']:.0%})  multi_table={r['n_multi_table']}  "
              f"compound={r['n_compound']}")
        print("  example single-table questions:")
        for q in r["examples"]:
            print(f"    Q: {q.get('question', '<no question field>')}")
            print(f"    SQL: {q['query']}")
    print("\n" + "=" * 100)
    print("LICENSE: Spider is commonly cited as CC BY-SA 4.0 (per its official repo/dataset")
    print("card) -- not independently verified here; confirm on the dataset card /")
    print("official repo before citing in a paper.")
    print("=" * 100)


# ── self-test (dry-check without live data) ──────────────────────────────────

def selftest() -> None:
    """Synthetic fixtures mirroring Spider's known JSON shapes, proving both
    the real-schema path and the schema-free/inferred path are correct
    independent of live data access. Does not assert anything about real
    Spider content."""
    synthetic_schemas_raw = [
        {"db_id": "toy_single", "table_names_original": ["employee"],
         "column_names_original": [[-1, "*"], [0, "id"], [0, "name"], [0, "salary"], [0, "hired"]],
         "column_types": ["text", "number", "text", "number", "time"]},
        {"db_id": "toy_multi", "table_names_original": ["a", "b"],
         "column_names_original": [[-1, "*"], [0, "id"], [0, "b_id"], [1, "id"], [1, "val"]],
         "column_types": ["number", "number", "number", "number", "text"]},
    ]
    synthetic_dev = (
        [{"db_id": "toy_single", "question": f"q{i}", "query": "SELECT name FROM employee WHERE salary > 1000"}
         for i in range(15)]
        + [{"db_id": "toy_single", "question": "compound q", "query": "SELECT name FROM employee INTERSECT SELECT name FROM employee"}]
        + [{"db_id": "toy_multi", "question": f"mq{i}", "query": "SELECT a.id FROM a JOIN b ON a.b_id = b.id"}
           for i in range(15)]
        + [{"db_id": "toy_multi", "question": "implicit join", "query": "SELECT a.id FROM a, b WHERE a.b_id = b.id"}]
    )
    schemas = {t["db_id"]: summarize_schema(t) for t in synthetic_schemas_raw}
    assert schemas["toy_single"]["n_tables"] == 1
    assert schemas["toy_single"]["n_columns"] == 4
    assert schemas["toy_multi"]["n_tables"] == 2
    assert schemas["toy_single"]["inferred"] is False

    assert classify_query("SELECT name FROM employee WHERE salary > 1000") == "single_table"
    assert classify_query("SELECT a.id FROM a JOIN b ON a.b_id = b.id") == "multi_table"
    assert classify_query("SELECT a.id FROM a, b WHERE a.b_id = b.id") == "multi_table"
    assert classify_query("SELECT name FROM employee INTERSECT SELECT name FROM employee") == "compound"

    rows = rank_candidates(synthetic_dev, schemas, min_questions=10, max_tables=6)
    by_id = {r["db_id"]: r for r in rows}
    assert by_id["toy_single"]["frac_single_table"] == 15 / 16
    assert by_id["toy_single"]["n_compound"] == 1
    assert by_id["toy_multi"]["frac_single_table"] == 0.0
    assert by_id["toy_multi"]["n_multi_table"] == 16
    assert rows[0]["db_id"] == "toy_single", "higher single-table fraction must rank first"

    # schema-free / inferred path — no tables.json at all (schemas={})
    inferred_dev_single = [
        {"db_id": "toy_inferred_single", "question": f"iq{i}",
         "query": "SELECT name FROM widget WHERE cost > 5"} for i in range(20)]
    inferred_dev_multi = [
        {"db_id": "toy_inferred_multi", "question": f"jq{i}",
         "query": "SELECT w.name FROM widget w LEFT JOIN part p ON w.id = p.widget_id"} for i in range(20)]
    rows2 = rank_candidates(inferred_dev_single + inferred_dev_multi, {},
                            min_questions=10, max_tables=6)
    by_id2 = {r["db_id"]: r for r in rows2}
    r_single = by_id2["toy_inferred_single"]
    r_multi = by_id2["toy_inferred_multi"]
    assert r_single["inferred"] is True and r_multi["inferred"] is True
    assert r_single["n_tables"] == 1 and r_single["inferred_table_names"] == ["widget"]
    assert r_single["frac_single_table"] == 1.0
    assert r_multi["n_tables"] == 2 and set(r_multi["inferred_table_names"]) == {"widget", "part"}
    assert r_multi["frac_single_table"] == 0.0
    assert rows2[0]["db_id"] == "toy_inferred_single", "inferred-mode ranking must also favor single-table"

    print("SELFTEST PASSED (synthetic fixtures only — no real Spider content involved)\n")


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tables-json", type=Path, default=None,
                   help="local path to Spider's tables.json. Optional: if omitted (and "
                        "the best-effort HF fetch also fails), ranking falls back to a "
                        "schema-free mode using table names inferred from the SQL text.")
    p.add_argument("--dev-json", type=Path, default=None,
                   help="local path to Spider dev-set data: .json (official dev.json), "
                        "or .parquet/.csv (e.g. a datasets.load_dataset('xlangai/spider') "
                        "export). Skips the HF fetch if given.")
    p.add_argument("--min-questions", type=int, default=20)
    p.add_argument("--max-tables", type=int, default=4)
    p.add_argument("--top-n", type=int, default=8)
    p.add_argument("--skip-selftest", action="store_true")
    args = p.parse_args()

    if not args.skip_selftest:
        selftest()

    if args.dev_json:
        dev_questions = load_dev_questions_local(args.dev_json)
    else:
        try:
            dev_questions = fetch_dev_questions_hf()
        except Exception as exc:                              # noqa: BLE001
            print(f"ERROR: could not obtain Spider dev-set questions: {type(exc).__name__}: {exc}")
            print("Pass --dev-json pointing at a local dev.json/.parquet/.csv and re-run.")
            return 2

    if args.tables_json:
        schemas = load_schemas_local(args.tables_json)
    else:
        try:
            schemas = fetch_schemas_hf()
        except Exception as exc:                              # noqa: BLE001
            print(f"NOTE: could not obtain tables.json ({type(exc).__name__}: {exc}) -- "
                  f"continuing in SCHEMA-FREE mode (table counts inferred from SQL text, "
                  f"no column-type info). Pass --tables-json for the full picture.\n")
            schemas = {}

    rows = rank_candidates(dev_questions, schemas,
                           min_questions=args.min_questions, max_tables=args.max_tables)
    if not rows:
        print(f"No db_id passed the filter (min_questions={args.min_questions}, "
              f"max_tables={args.max_tables}) -- loosen --min-questions/--max-tables and retry.")
        return 1
    print_report(rows, args.top_n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
