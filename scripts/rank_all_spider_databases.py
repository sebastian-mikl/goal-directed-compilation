#!/usr/bin/env python
"""Rank ALL local Spider databases for join-free-friendliness, to pick a 3rd
evaluation domain (wta_1 and world_1 are already chosen — see EXCLUDE below).

Context: the GDC engine's 4 operators (filter, select, aggregate, compute) have no
join primitive, so a good candidate database needs a high fraction of
dev-set questions answerable from ONE table, plus enough numeric/categorical
column variety on that table for a T1-T5-style depth range, plus enough rows
and questions to be a meaningful benchmark. Where the earlier survey
(survey_spider_candidates.py) infers table names from SQL text when no schema
is reachable, this script requires the real .sqlite files and the real dev-set
Q&A data and uses BOTH directly — no inference needed.

WHAT THIS DOES, for every subfolder of the given database/ directory (each
expected to contain <db_id>/<db_id>.sqlite, Spider's standard layout):
  1. Opens the .sqlite file read-only, walks every table via PRAGMA
     table_info() (columns, types, primary keys) and PRAGMA foreign_key_list()
     (foreign keys).
  2. Classifies every column as numeric / text / date from its REAL PRAGMA
     type string (SQLite type affinity means the stored string varies --
     "INT", "INTEGER", "int(11)", "DOUBLE", "DECIMAL(10,2)" etc. are all
     matched, not guessed from a single expected spelling).
  3. Picks the table with the most numeric columns ("best table") and, ONLY
     for that table, runs a real COUNT(DISTINCT ...) per non-numeric column
     to find genuinely categorical columns (bounded, non-unique cardinality)
     -- type alone can't tell a 3-value status column from a free-text name
     column, both are typed TEXT. Row-count-gated (see MAX_ROWS_FOR_CARDINALITY)
     so this can't turn into a slow full-table scan on a large database.
  4. Matches this db_id's dev-set questions (from the given dev-data file --
     .json/.parquet/.csv, auto-detected) and classifies each gold query as
     single_table / multi_table / compound. Unlike the earlier text-only
     heuristic, this is schema-AWARE: a self-join like
     "FROM student a, student b WHERE a.advisor = b.id" resolves both `a`
     and `b` against the REAL table list and correctly reads as ONE table,
     not two -- the old heuristic could not distinguish this from a genuine
     2-table join.
  5. Scores the database (formula spelled out in named constants + SCORE_*
     component fields below, never hidden in one opaque number) and flags a
     simple keyword-based "this might overlap wta_1 (sports) / world_1
     (geography-government)" signal -- NOT an authoritative Spider domain
     taxonomy (Spider does not ship one), just a transparent heuristic for a
     human to weigh.

SAFETY: an unbounded query against cyclic data can hang SQLite indefinitely, so
each database gets its own wall-clock bound via sqlite3's progress handler (the
same mechanism as evidence/eval_run/tools_sql.py) — one pathological file cannot
hang a ~200-database run. Progress is printed per database so a long run never
looks silently stuck.

OUTPUT:
  - Full ranked list of every database processed -> --output (default
    reports/spider_full_ranking.txt), plain text, one line per db_id.
  - Top --top-n (default 15) printed to console with every score component
    broken out, full best-table schema, categorical-column cardinality, and
    example single-table questions -- enough for a human to pick the 3rd
    domain from actual content, not just a composite number.
  - wta_1 / world_1 (--exclude) are scored and shown in the full list for
    reference/calibration, but excluded from the top-N candidate ranking.

Read-only: never writes to any .sqlite file. Does not touch any existing
repo file — only writes the new --output report file.

USAGE:
    python scripts/rank_all_spider_databases.py /path/to/spider/database /path/to/dev_data.parquet

A synthetic multi-database fixture exercising both paths is in selftest() at the
bottom; the real Spider bundle is not included in this repo.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ── SQL classification (gold `query` text, schema-AWARE) ────────────────────

_JOIN_RE = re.compile(r"\bjoin\b", re.IGNORECASE)
_SETOP_RE = re.compile(r"\b(intersect|union|except)\b", re.IGNORECASE)
_FROM_RE = re.compile(
    r"\bfrom\b(.*?)(?:\bwhere\b|\bgroup\s+by\b|\bhaving\b|\border\s+by\b|\blimit\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_TABLE_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def extract_from_clause(sql: str) -> str | None:
    m = _FROM_RE.search(sql)
    return m.group(1).strip() if m else None


def table_ref_count(from_clause: str) -> int:
    """Fallback slot-count heuristic (no schema cross-check): 1 (base) +
    JOINs + commas. A parenthesized subquery is treated as 2 (conservative)."""
    if "(" in from_clause:
        return 2
    return 1 + len(_JOIN_RE.findall(from_clause)) + from_clause.count(",")


def resolve_table_tokens(from_clause: str, real_tables: set[str]) -> set[str] | None:
    """Extract table-name tokens and resolve each against the database's REAL
    table list (case-insensitive), so a self-join like 'FROM student a,
    student b' — both aliases of the SAME real table — correctly resolves to
    ONE table, not two. Returns None (caller falls back to table_ref_count)
    if a subquery is present or any token doesn't match a real table name —
    never guesses past what it can actually verify against the schema."""
    if "(" in from_clause:
        return None
    real_lower = {t.lower(): t for t in real_tables}
    resolved: set[str] = set()
    for join_piece in _JOIN_RE.split(from_clause):
        for comma_piece in join_piece.split(","):
            toks = _TABLE_TOKEN_RE.findall(comma_piece.strip())
            if not toks:
                return None
            name = toks[0].lower()
            if name not in real_lower:
                return None
            resolved.add(real_lower[name])
    return resolved


def classify_query(sql: str, real_tables: set[str]) -> str:
    """'single_table' | 'multi_table' | 'compound'. Prefers resolving every
    FROM-clause table token against the REAL schema (catches self-joins
    correctly); falls back to the conservative slot-counting heuristic only
    when that resolution isn't possible (subquery, or an unresolvable token)."""
    if _SETOP_RE.search(sql):
        return "compound"
    from_clause = extract_from_clause(sql)
    if from_clause is None:
        return "multi_table"
    resolved = resolve_table_tokens(from_clause, real_tables)
    if resolved is not None:
        return "single_table" if len(resolved) == 1 else "multi_table"
    return "single_table" if table_ref_count(from_clause) == 1 else "multi_table"


# ── column type classification (real PRAGMA type strings) ───────────────────

_NUMERIC_TYPE_RE = re.compile(
    r"\b(int|integer|tinyint|smallint|mediumint|bigint|real|float|double|"
    r"numeric|decimal|number)\b", re.IGNORECASE)
_DATE_TYPE_RE = re.compile(r"\b(date|datetime|timestamp|time|year)\b", re.IGNORECASE)


def classify_column_type(type_str: str | None) -> str:
    """'numeric' | 'date' | 'text' from the REAL PRAGMA table_info() type
    string. SQLite type affinity means the string varies a lot ('INT',
    'int(11)', 'DOUBLE PRECISION', 'DECIMAL(10,2)', ...) so this matches
    substrings/regex against the actual string, never assumes a spelling."""
    t = (type_str or "").strip()
    if not t:
        return "text"
    if _NUMERIC_TYPE_RE.search(t):
        return "numeric"
    if _DATE_TYPE_RE.search(t):
        return "date"
    return "text"


# ── schema extraction (real .sqlite file, read-only) ─────────────────────────

def open_with_timeout(path: Path, timeout_s: float) -> "sqlite3.Connection":
    """Read-only connection with a wall-clock bound via sqlite3's progress
    handler (the same mechanism as evidence/eval_run/tools_sql.py's
    per_query_timeout_s): a pathological file can't hang the whole
    ~200-database run."""
    import sqlite3
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    deadline = time.monotonic() + timeout_s
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
    return conn


def list_tables(conn) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name").fetchall()
    return [r[0] for r in rows]


def table_info(conn, table: str) -> list[dict]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [{"name": r[1], "type": r[2], "notnull": bool(r[3]), "pk": r[5]} for r in rows]


def foreign_keys(conn, table: str) -> list[tuple[str, str, str]]:
    rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    return [(r[3], r[2], r[4]) for r in rows]          # (from_col, to_table, to_col)


def row_count(conn, table: str) -> int:
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def distinct_count(conn, table: str, column: str) -> int:
    return conn.execute(f'SELECT COUNT(DISTINCT "{column}") FROM "{table}"').fetchone()[0]


# ── categorical-column thresholds (used only on the best table) ─────────────

CATEGORICAL_MIN_DISTINCT = 2
CATEGORICAL_MAX_DISTINCT_ABS = 50
CATEGORICAL_MAX_DISTINCT_FRAC = 0.5     # relative cap: not more than half the rows
MAX_ROWS_FOR_CARDINALITY = 100_000      # skip real COUNT(DISTINCT) above this


def pick_best_table(conn, tables: list[str]) -> tuple[str, list[dict]] | None:
    """The table with the most numeric-typed columns (ties broken by table
    name, alphabetically, for determinism). None if there are no tables."""
    if not tables:
        return None
    best_name, best_cols, best_numeric = None, [], -1
    for t in tables:
        cols = table_info(conn, t)
        n_numeric = sum(1 for c in cols if classify_column_type(c["type"]) == "numeric")
        if n_numeric > best_numeric:
            best_name, best_cols, best_numeric = t, cols, n_numeric
    return best_name, best_cols


def analyze_best_table(conn, table: str, cols: list[dict]) -> dict:
    n_rows = row_count(conn, table)
    numeric_cols = [c["name"] for c in cols if classify_column_type(c["type"]) == "numeric"]
    non_numeric_cols = [c["name"] for c in cols if classify_column_type(c["type"]) != "numeric"]
    categorical_cols: list[tuple[str, int]] = []
    cardinality_skipped = n_rows > MAX_ROWS_FOR_CARDINALITY
    if not cardinality_skipped:
        cap = min(CATEGORICAL_MAX_DISTINCT_ABS, max(CATEGORICAL_MAX_DISTINCT_FRAC * n_rows, 1))
        for col in non_numeric_cols:
            n_distinct = distinct_count(conn, table, col)
            if CATEGORICAL_MIN_DISTINCT <= n_distinct <= cap:
                categorical_cols.append((col, n_distinct))
    return {
        "best_table_name": table, "best_table_columns": cols,
        "best_table_row_count": n_rows,
        "numeric_column_count": len(numeric_cols),
        "categorical_column_count": len(categorical_cols),
        "categorical_columns": categorical_cols,
        "cardinality_skipped": cardinality_skipped,
    }


# ── domain-overlap heuristic (transparent keyword match, not authoritative) ─

DOMAIN_KEYWORDS = {
    "sports (wta_1-like)": ["sport", "team", "player", "match", "race", "athlete",
                            "club", "league", "tournament", "coach", "referee",
                            "stadium", "olympic", "wta", "nba", "soccer", "football"],
    "geography/government (world_1-like)": ["country", "city", "world", "government",
                                            "nation", "state", "region", "election",
                                            "county", "province", "capital", "continent"],
}


_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def guess_domain_flags(db_id: str, table_names: list[str]) -> list[str]:
    """Keyword match on db_id + table names only -- Spider does not ship an
    authoritative domain taxonomy, so this is a transparent, inspectable
    heuristic for human review, not ground truth.

    Matches whole TOKENS (split on underscore/non-alphanumeric), not raw
    substrings: a plain `"match" in haystack` check would false-positive on
    e.g. a db_id like 'toy_nomatch' (caught by this script's own selftest --
    "match" is a substring of "nomatch" but never the intended keyword)."""
    haystack = " ".join([db_id] + table_names).lower()
    tokens = set(_TOKEN_SPLIT_RE.split(haystack))
    return [cat for cat, kws in DOMAIN_KEYWORDS.items() if tokens & set(kws)]


# ── dev-set question loading (json / parquet / csv, auto-detected) ──────────

def load_dev_questions(path: Path) -> list[dict]:
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


# ── scoring — every weight/threshold named here, formula spelled out ────────

W_SINGLE_TABLE = 100          # frac_single_table [0,1] * this -- dominant term
NUMERIC_SATURATION = 6        # numeric_column_count score saturates at this many
W_NUMERIC = 15
CATEGORICAL_SATURATION = 4    # categorical_column_count score saturates at this many
W_CATEGORICAL = 10
QUESTION_SATURATION = 45      # question_count score saturates at this many
W_QUESTION = 10
TABLE_PENALTY_PER_TABLE = 4   # subtracted per table beyond the first
ROW_THIN_CUTOFF = 20
ROW_SPARSE_CUTOFF = 50
ROW_PENALTY_THIN = -30        # best_table_row_count < ROW_THIN_CUTOFF
ROW_PENALTY_SPARSE = -10      # ROW_THIN_CUTOFF <= best_table_row_count < ROW_SPARSE_CUTOFF


def score_database(row: dict) -> dict:
    """Adds 'score' plus every named component to `row` (in place, also
    returned) -- the composite is never presented without its parts. See the
    W_*/*_SATURATION/*_PENALTY* constants above for the complete, exact
    formula; nothing here is a magic number not named above."""
    frac = row["single_table_fraction"]
    c_single = (frac * W_SINGLE_TABLE) if frac is not None else 0.0
    c_numeric = W_NUMERIC * min(row["numeric_column_count"] / NUMERIC_SATURATION, 1.0)
    c_categorical = W_CATEGORICAL * min(row["categorical_column_count"] / CATEGORICAL_SATURATION, 1.0)
    c_question = W_QUESTION * min(row["question_count"] / QUESTION_SATURATION, 1.0)
    c_table = -TABLE_PENALTY_PER_TABLE * max(row["table_count"] - 1, 0)
    rc = row["best_table_row_count"]
    c_row = ROW_PENALTY_THIN if rc < ROW_THIN_CUTOFF else (
        ROW_PENALTY_SPARSE if rc < ROW_SPARSE_CUTOFF else 0.0)
    row.update({
        "score": c_single + c_numeric + c_categorical + c_question + c_table + c_row,
        "component_single_table": c_single, "component_numeric": c_numeric,
        "component_categorical": c_categorical, "component_question": c_question,
        "component_table_penalty": c_table, "component_row_penalty": c_row,
    })
    return row


# ── per-database processing ──────────────────────────────────────────────────

def process_database(db_id: str, sqlite_path: Path, dev_questions_for_db: list[dict],
                      *, timeout_s: float) -> dict | None:
    """None if the file is missing/unreadable/has no tables -- logged and
    skipped, never crashes the whole ~200-database run over one bad file."""
    if not sqlite_path.exists():
        print(f"  [{db_id}] SKIP: {sqlite_path} does not exist")
        return None
    try:
        conn = open_with_timeout(sqlite_path, timeout_s)
        try:
            tables = list_tables(conn)
            if not tables:
                print(f"  [{db_id}] SKIP: no tables found")
                return None
            picked = pick_best_table(conn, tables)
            best_name, best_cols = picked
            best = analyze_best_table(conn, best_name, best_cols)
            fks = foreign_keys(conn, best_name)

            real_tables = set(tables)
            classified = [(q, classify_query(q["query"], real_tables)) for q in dev_questions_for_db]
            n = len(classified)
            n_single = sum(1 for _, c in classified if c == "single_table")
            n_multi = sum(1 for _, c in classified if c == "multi_table")
            n_compound = sum(1 for _, c in classified if c == "compound")
            frac_single = (n_single / n) if n else None
            examples = [q for q, c in classified if c == "single_table"][:4]
        finally:
            conn.close()
    except Exception as exc:                              # noqa: BLE001
        print(f"  [{db_id}] SKIP: {type(exc).__name__}: {exc}")
        return None

    row = {
        "db_id": db_id, "table_count": len(tables), "table_names": tables,
        "best_table_fks": fks,
        "question_count": n, "single_table_fraction": frac_single,
        "n_single_table": n_single, "n_multi_table": n_multi, "n_compound": n_compound,
        "examples": examples,
        "domain_flags": guess_domain_flags(db_id, tables),
        **best,
    }
    return score_database(row)


# ── reporting ─────────────────────────────────────────────────────────────

def write_full_ranking(rows: list[dict], excluded: set[str], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"Spider full database ranking — {len(rows)} databases processed",
        f"(EXCLUDED = already chosen as a domain, shown for reference only, not eligible)",
        "-" * 100,
        f"{'rank':<5s}{'db_id':<32s}{'score':>8s}{'single%':>9s}{'tables':>7s}"
        f"{'num_cols':>9s}{'cat_cols':>9s}{'quest':>7s}{'best_rows':>10s}  flags",
        "-" * 100,
    ]
    ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(ranked, 1):
        tag = " [EXCLUDED]" if r["db_id"] in excluded else ""
        frac = r["single_table_fraction"]
        frac_s = f"{frac:.0%}" if frac is not None else "N/A"
        flags = ", ".join(r["domain_flags"]) if r["domain_flags"] else ""
        lines.append(
            f"{i:<5d}{r['db_id']:<32s}{r['score']:>8.1f}{frac_s:>9s}{r['table_count']:>7d}"
            f"{r['numeric_column_count']:>9d}{r['categorical_column_count']:>9d}"
            f"{r['question_count']:>7d}{r['best_table_row_count']:>10d}  {flags}{tag}")
    out_path.write_text("\n".join(lines) + "\n")


def print_top_candidates(rows: list[dict], excluded: set[str], top_n: int) -> None:
    candidates = [r for r in rows if r["db_id"] not in excluded]
    candidates.sort(key=lambda r: r["score"], reverse=True)

    print("=" * 100)
    print("REFERENCE — already-chosen domains (for score calibration, not re-suggested):")
    for r in rows:
        if r["db_id"] in excluded:
            frac = r["single_table_fraction"]
            print(f"  {r['db_id']:<24s} score={r['score']:.1f}  "
                  f"single_table={f'{frac:.0%}' if frac is not None else 'N/A'}  "
                  f"tables={r['table_count']}  questions={r['question_count']}")
    print("=" * 100)

    print(f"TOP {min(top_n, len(candidates))} CANDIDATES (of {len(candidates)} scored, "
          f"excluding {sorted(excluded)})")
    print("=" * 100)
    skew = [r["db_id"] for r in candidates[:top_n] if r["domain_flags"]]
    if skew:
        print(f"NOTE: {len(skew)} of the top {top_n} candidates hit a domain-overlap keyword "
              f"flag (sports/geography-government, like wta_1/world_1) -- see [flags] per row "
              f"below; consider weighting a genuinely different-domain option if several "
              f"similarly-scored choices exist.")
        print("-" * 100)

    for r in candidates[:top_n]:
        frac = r["single_table_fraction"]
        print(f"\ndb_id={r['db_id']!r}  score={r['score']:.1f}"
              f"{'  [' + ', '.join(r['domain_flags']) + ']' if r['domain_flags'] else ''}")
        print(f"  score components: single_table={r['component_single_table']:.1f}  "
              f"numeric={r['component_numeric']:.1f}  categorical={r['component_categorical']:.1f}  "
              f"question={r['component_question']:.1f}  table_penalty={r['component_table_penalty']:.1f}  "
              f"row_penalty={r['component_row_penalty']:.1f}")
        print(f"  tables ({r['table_count']}): {r['table_names']}")
        print(f"  best_table={r['best_table_name']!r}  rows={r['best_table_row_count']}  "
              f"numeric_cols={r['numeric_column_count']}  categorical_cols={r['categorical_column_count']}"
              f"{'  (cardinality check SKIPPED -- table too large)' if r['cardinality_skipped'] else ''}")
        print("  best_table columns:")
        for c in r["best_table_columns"]:
            pk = "  PK" if c["pk"] else ""
            print(f"    {c['name']:<28s} {c['type']:<12s}{pk}")
        if r["categorical_columns"]:
            print("  categorical columns (name, real distinct-value count):")
            for name, nd in r["categorical_columns"]:
                print(f"    {name:<28s} {nd} distinct values")
        if r["best_table_fks"]:
            print("  foreign keys:")
            for from_col, to_table, to_col in r["best_table_fks"]:
                print(f"    {r['best_table_name']}.{from_col} -> {to_table}.{to_col}")
        frac_s = f"{frac:.0%}" if frac is not None else "N/A"
        print(f"  questions={r['question_count']}  single_table={r['n_single_table']} "
              f"({frac_s})  multi_table={r['n_multi_table']}  "
              f"compound={r['n_compound']}")
        if r["examples"]:
            print("  example single-table questions:")
            for q in r["examples"]:
                print(f"    Q: {q.get('question', '<no question field>')}")
                print(f"    SQL: {q['query']}")
    print("\n" + "=" * 100)


# ── self-test (dry-check without the real Spider bundle) ────────────────────

def selftest() -> None:
    """Builds a small SQLite fixture on disk (temp dir) covering: a strong
    single-table candidate, a multi-table-heavy database, a thin-rows
    database, a self-join case, and stand-ins for the two excluded domains --
    proves classification/scoring/exclusion/self-join logic without the real
    Spider bundle, which this environment does not have."""
    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_dir = root / "database"

        def make_db(db_id: str, ddl: str) -> Path:
            d = db_dir / db_id
            d.mkdir(parents=True)
            path = d / f"{db_id}.sqlite"
            conn = sqlite3.connect(path)
            conn.executescript(ddl)
            conn.commit()
            conn.close()
            return path

        # strong candidate: 1 table, rich numeric+categorical columns, decent rows
        make_db("toy_strong", """
            CREATE TABLE widget (
                id INTEGER PRIMARY KEY, name TEXT, status TEXT,
                price REAL, weight REAL, stock INTEGER, rating REAL
            );
            """ + "\n".join(
                f"INSERT INTO widget VALUES ({i}, 'w{i}', "
                f"'{'active' if i % 3 else 'retired'}', {i * 1.5}, {i * 0.2}, {i}, {i % 5});"
                for i in range(1, 61)))

        # multi-table-heavy: many tables, should score low on table_penalty
        make_db("toy_heavy", """
            CREATE TABLE a (id INTEGER PRIMARY KEY, val REAL);
            CREATE TABLE b (id INTEGER PRIMARY KEY, a_id INTEGER, val REAL);
            CREATE TABLE c (id INTEGER PRIMARY KEY, b_id INTEGER, val REAL);
            CREATE TABLE d (id INTEGER PRIMARY KEY, c_id INTEGER, val REAL);
            INSERT INTO a VALUES (1, 1.0);
            INSERT INTO b VALUES (1, 1, 1.0);
            INSERT INTO c VALUES (1, 1, 1.0);
            INSERT INTO d VALUES (1, 1, 1.0);
            """)

        # thin: good columns but almost no rows -- row penalty should apply
        make_db("toy_thin", """
            CREATE TABLE item (id INTEGER PRIMARY KEY, price REAL, category TEXT);
            INSERT INTO item VALUES (1, 5.0, 'a');
            INSERT INTO item VALUES (2, 6.0, 'b');
            """)

        # self-join case: FROM student a, student b -- ONE real table
        make_db("toy_selfjoin", """
            CREATE TABLE student (id INTEGER PRIMARY KEY, advisor_id INTEGER, gpa REAL);
            """ + "\n".join(f"INSERT INTO student VALUES ({i}, {max(i-1,1)}, {2.0+i*0.1});"
                            for i in range(1, 31)))

        # stand-ins for the excluded domains
        make_db("wta_1", "CREATE TABLE players (id INTEGER PRIMARY KEY, name TEXT);"
                         "INSERT INTO players VALUES (1, 'p');")
        make_db("world_1", "CREATE TABLE city (id INTEGER PRIMARY KEY, name TEXT);"
                           "INSERT INTO city VALUES (1, 'c');")

        # domain-flag false-positive regression: "nomatch" contains the substring
        # "match" but must NOT trip the sports flag (token-boundary match, not
        # substring) -- caught empirically by an earlier CLI dry-run, not guessed.
        make_db("toy_nomatch", "CREATE TABLE recipe (id INTEGER PRIMARY KEY, title TEXT);"
                               "INSERT INTO recipe VALUES (1, 'r');")
        # genuine positive: a real 'team' token must still trip the flag.
        make_db("toy_team_flag", "CREATE TABLE team (id INTEGER PRIMARY KEY, name TEXT);"
                                 "INSERT INTO team VALUES (1, 't');")

        dev = (
            [{"db_id": "toy_strong", "question": f"q{i}", "query": "SELECT name FROM widget WHERE price > 10"}
             for i in range(30)]
            + [{"db_id": "toy_heavy", "question": f"hq{i}",
                "query": "SELECT a.id FROM a JOIN b ON a.id = b.a_id JOIN c ON b.id = c.b_id"}
               for i in range(10)]
            + [{"db_id": "toy_thin", "question": f"tq{i}", "query": "SELECT price FROM item WHERE category = 'a'"}
               for i in range(25)]
            + [{"db_id": "toy_selfjoin", "question": f"sq{i}",
                "query": "SELECT a.gpa, b.gpa FROM student a, student b WHERE a.advisor_id = b.id"}
               for i in range(20)]
            + [{"db_id": "toy_nomatch", "question": "nq0", "query": "SELECT title FROM recipe"}]
            + [{"db_id": "toy_team_flag", "question": "fq0", "query": "SELECT name FROM team"}]
        )
        dev_path = root / "dev.json"
        dev_path.write_text(json.dumps(dev))

        rows = run_ranking(db_dir, dev_path, exclude={"wta_1", "world_1"}, timeout_s=5.0)
        by_id = {r["db_id"]: r for r in rows}

        assert by_id["toy_strong"]["numeric_column_count"] == 5, by_id["toy_strong"]["numeric_column_count"]
        assert by_id["toy_strong"]["categorical_column_count"] >= 1
        assert by_id["toy_strong"]["single_table_fraction"] == 1.0
        assert by_id["toy_strong"]["best_table_row_count"] == 60

        assert by_id["toy_heavy"]["single_table_fraction"] == 0.0
        assert by_id["toy_heavy"]["table_count"] == 4
        assert by_id["toy_heavy"]["component_table_penalty"] == -TABLE_PENALTY_PER_TABLE * 3

        assert by_id["toy_thin"]["best_table_row_count"] == 2
        assert by_id["toy_thin"]["component_row_penalty"] == ROW_PENALTY_THIN

        # the self-join must resolve to ONE real table, not two
        assert by_id["toy_selfjoin"]["single_table_fraction"] == 1.0, \
            "self-join (FROM student a, student b) must classify as single_table via schema resolution"

        # domain-flag token-boundary regression (see comment at fixture build)
        assert by_id["toy_nomatch"]["domain_flags"] == [], \
            f"'toy_nomatch' must NOT trip the sports flag (substring 'match' != token 'match'), got {by_id['toy_nomatch']['domain_flags']}"
        assert "sports (wta_1-like)" in by_id["toy_team_flag"]["domain_flags"], \
            "a real 'team' token must still trip the sports flag"

        ranked_ids = [r["db_id"] for r in sorted(rows, key=lambda r: r["score"], reverse=True)]
        assert ranked_ids[0] == "toy_strong", f"toy_strong must rank first, got order {ranked_ids}"
        assert "toy_heavy" == ranked_ids[-1] or by_id["toy_heavy"]["score"] < by_id["toy_strong"]["score"]

        excluded = {"wta_1", "world_1"}
        candidates = [r["db_id"] for r in rows if r["db_id"] not in excluded]
        assert "wta_1" not in candidates and "world_1" not in candidates
        assert set(candidates) == {"toy_strong", "toy_heavy", "toy_thin", "toy_selfjoin",
                                   "toy_nomatch", "toy_team_flag"}

        out_path = root / "ranking.txt"
        write_full_ranking(rows, excluded, out_path)
        text = out_path.read_text()
        assert "toy_strong" in text and "[EXCLUDED]" in text and "wta_1" in text

    print("SELFTEST PASSED (synthetic multi-database fixture only — no real Spider content involved)\n")


# ── driver (shared by main() and selftest()) ─────────────────────────────────

def run_ranking(database_dir: Path, dev_data_path: Path, *, exclude: set[str],
                timeout_s: float, limit: int | None = None) -> list[dict]:
    dev_questions = load_dev_questions(dev_data_path)
    by_db: dict[str, list[dict]] = {}
    for q in dev_questions:
        by_db.setdefault(q["db_id"], []).append(q)

    subdirs = sorted(p for p in database_dir.iterdir() if p.is_dir())
    if limit is not None:
        subdirs = subdirs[:limit]

    rows = []
    for i, sub in enumerate(subdirs, 1):
        db_id = sub.name
        print(f"[{i}/{len(subdirs)}] {db_id}")
        sqlite_path = sub / f"{db_id}.sqlite"
        result = process_database(db_id, sqlite_path, by_db.get(db_id, []), timeout_s=timeout_s)
        if result is not None:
            rows.append(result)
    return rows


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("database_dir", type=Path, nargs="?", default=None,
                   help="Spider's database/ directory (one <db_id>/<db_id>.sqlite per db)")
    p.add_argument("dev_data", type=Path, nargs="?", default=None,
                   help="dev-set Q&A data: .json / .parquet / .csv")
    p.add_argument("--exclude", default="wta_1,world_1",
                   help="comma-separated db_ids to exclude from candidacy (still shown, tagged)")
    p.add_argument("--top-n", type=int, default=15)
    p.add_argument("--limit", type=int, default=None,
                   help="process only the first N database subfolders (quick local smoke test)")
    p.add_argument("--timeout-s", type=float, default=10.0,
                   help="per-database wall-clock bound (sqlite progress handler)")
    p.add_argument("--output", type=Path, default=ROOT / "reports" / "spider_full_ranking.txt")
    p.add_argument("--skip-selftest", action="store_true")
    args = p.parse_args()

    if not args.skip_selftest:
        selftest()

    if args.database_dir is None or args.dev_data is None:
        p.print_help()
        print("\nERROR: both database_dir and dev_data are required (unless only running --skip-selftest).")
        return 2

    exclude = {s.strip() for s in args.exclude.split(",") if s.strip()}
    rows = run_ranking(args.database_dir, args.dev_data, exclude=exclude,
                       timeout_s=args.timeout_s, limit=args.limit)
    if not rows:
        print("ERROR: no databases were successfully processed.")
        return 1

    write_full_ranking(rows, exclude, args.output)
    print(f"\nFull ranking of {len(rows)} databases written to {args.output}\n")
    print_top_candidates(rows, exclude, args.top_n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
