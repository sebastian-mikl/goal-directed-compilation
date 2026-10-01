#!/usr/bin/env python
"""Extract real column names/types/primary-keys from Spider's official
per-database SQLite files, for the 3 databases selected from the schema-free
survey: wta_1, world_1, employee_hire_evaluation.

WHY THIS SCRIPT EXISTS INSTEAD OF INLINE RESULTS:
  The Spider .sqlite bundle is not vendored into this repo and may not be
  reachable from every environment (network policy varies, and no PyPI package
  vendors it). Rather than hard-coding schema results that could not be
  regenerated, this script extracts them from the real files wherever you do
  have them.

  Run this yourself, against a local copy of Spider's official data bundle
  (per your own description: yale-lily.github.io/spider), which ships one
  directory per database at:
      database/<db_id>/<db_id>.sqlite
  e.g. database/wta_1/wta_1.sqlite, database/world_1/world_1.sqlite,
  database/employee_hire_evaluation/employee_hire_evaluation.sqlite.

USAGE:
    python scripts/extract_spider_schema.py /path/to/spider/database

  (pass the `database/` directory itself — the script looks for
  <db_id>/<db_id>.sqlite under it for each of the 3 target db_ids.) Or, if
  your copy doesn't follow that layout, point at individual files:

    python scripts/extract_spider_schema.py \\
        --db-file wta_1=/path/to/wta_1.sqlite \\
        --db-file world_1=/path/to/world_1.sqlite \\
        --db-file employee_hire_evaluation=/path/to/employee_hire_evaluation.sqlite

WHAT IT PRINTS:
  Per database: every table, and for every table every column's name/type/
  nullable/primary-key flag (via PRAGMA table_info), plus foreign keys (via
  PRAGMA foreign_key_list) as bonus context for the later schema.json-shape
  work (NOT built here — this script only extracts and prints, per this
  task's own explicit scope). Cross-checks the discovered table names against
  what the schema-free survey already inferred purely from SQL text
  (EXPECTED_TABLES below, taken verbatim from that survey's findings) and
  flags any mismatch rather than silently accepting it.

  Read-only: opens each .sqlite file via a read-only sqlite3 URI connection
  (?mode=ro) and never writes anything.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# What the schema-free survey (scripts/survey_spider_candidates.py) already
# inferred purely from FROM/JOIN-clause text across the dev-set questions --
# taken verbatim from that survey's reported findings, cross-checked against
# the REAL schema below, never assumed correct on its own.
EXPECTED_TABLES = {
    "wta_1": {"matches", "players", "rankings"},
    "world_1": {"city", "country", "countrylanguage"},
    "employee_hire_evaluation": {"employee", "evaluation", "hiring", "shop"},
}


def open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name").fetchall()
    return [r[0] for r in rows]


def table_info(conn: sqlite3.Connection, table: str) -> list[dict]:
    # table names here only ever come from sqlite_master itself (list_tables
    # above), never external input -- safe to interpolate without a bind
    # param (PRAGMA doesn't support parameter binding for the target name).
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [{"cid": r[0], "name": r[1], "type": r[2], "notnull": bool(r[3]),
             "default": r[4], "pk": r[5]} for r in rows]


def foreign_keys(conn: sqlite3.Connection, table: str) -> list[dict]:
    rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    return [{"from": r[3], "to_table": r[2], "to": r[4]} for r in rows]


def dump_database(db_id: str, path: Path) -> bool:
    """Returns True if the file was found and readable (regardless of
    whether the table-name cross-check matched)."""
    print("=" * 100)
    print(f"db_id={db_id!r}  file={path}")
    print("=" * 100)
    if not path.exists():
        print(f"  MISSING: {path} does not exist.\n")
        return False
    conn = open_readonly(path)
    try:
        tables = list_tables(conn)
        expected = EXPECTED_TABLES.get(db_id)
        if expected is not None:
            found = set(tables)
            if found != expected:
                print("  ! MISMATCH vs schema-free survey's inferred tables:")
                print(f"    inferred (SQL-text-only): {sorted(expected)}")
                print(f"    real (this file):          {sorted(found)}")
                print(f"    missing from real: {sorted(expected - found)}")
                print(f"    extra in real:      {sorted(found - expected)}\n")
            else:
                print(f"  MATCH: real tables == survey's inferred tables ({sorted(found)})\n")

        for table in tables:
            cols = table_info(conn, table)
            fks = foreign_keys(conn, table)
            print(f"  TABLE {table}  ({len(cols)} columns)")
            for c in cols:
                pk_flag = "  PK" if c["pk"] else ""
                nn_flag = "  NOT NULL" if c["notnull"] else ""
                print(f"    {c['name']:<30s} {c['type']:<12s}{pk_flag}{nn_flag}")
            if fks:
                print("    foreign keys:")
                for fk in fks:
                    print(f"      {table}.{fk['from']} -> {fk['to_table']}.{fk['to']}")
            print()
        return True
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("database_dir", type=Path, nargs="?", default=None,
                   help="Spider's database/ directory (contains <db_id>/<db_id>.sqlite per db)")
    p.add_argument("--db-file", action="append", default=[],
                   help="override for one db: db_id=/path/to/file.sqlite (repeatable)")
    args = p.parse_args()

    targets: dict[str, Path] = {}
    if args.database_dir:
        for db_id in EXPECTED_TABLES:
            targets[db_id] = args.database_dir / db_id / f"{db_id}.sqlite"
    for override in args.db_file:
        db_id, _, path_str = override.partition("=")
        if not db_id or not path_str:
            print(f"ERROR: --db-file must be db_id=/path/to/file.sqlite, got {override!r}")
            return 2
        targets[db_id] = Path(path_str)

    if not targets:
        p.print_help()
        print("\nERROR: pass either the database_dir positional arg or one/more --db-file overrides.")
        return 2

    all_ok = True
    for db_id in EXPECTED_TABLES:            # fixed order: wta_1, world_1, employee_hire_evaluation
        if db_id in targets:
            all_ok = dump_database(db_id, targets[db_id]) and all_ok
        else:
            print(f"(skipping {db_id!r} -- no path given)\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
