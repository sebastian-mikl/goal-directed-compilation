"""Shared helpers for the Spider third-party-gold scripts (build_gold_spider_<domain>.py).

Pandas-free, stdlib only (sqlite3 + re), zero engine imports. These functions just
execute a given gold SQL string against a given sqlite file and reshape the raw
result into the project's gold.json record shape ({result_kind, ordered, value,
row_count}) -- the SAME shape build_gold.py/build_gold_employee.py produce by hand,
but here the "gold logic" IS the verbatim, third-party Spider SQL itself; there is no
per-query hand-authored block, because the whole point of a Spider domain is using
independently-authored answers, not ours.

No domain-specific logic lives here (paths, corpus, sanity checks) -- each
build_gold_spider_<domain>.py script owns that.
"""
from __future__ import annotations

import re
import sqlite3

_ORDERBY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)


def is_ordered(sql: str) -> bool:
    """True if the gold SQL has an explicit ORDER BY -- Spider's own convention (and
    this project's existing TABLE()/OLIST() convention) for when row order is
    semantically meaningful vs an unordered set/table."""
    return bool(_ORDERBY_RE.search(sql))


def _round_value(v):
    return round(v, 6) if isinstance(v, float) else v


def _sort_key_value(v):
    """Total order across None/numeric/text, matching SQLite's own default storage-
    class ordering (NULL < INTEGER/REAL < TEXT) -- used only to make an unordered
    'table' result deterministic for comparison, never to change what a query with
    ORDER BY actually returns."""
    if v is None:
        return (0, "")
    if isinstance(v, (int, float)):
        return (1, v)
    return (2, str(v))


def execute_gold(sql: str, sqlite_path) -> dict:
    """Executes `sql` read-only against `sqlite_path` and reshapes the raw result into
    {result_kind, ordered, value, row_count}.

    result_kind: 'scalar' for a 1-row/1-column result; else 'ordered_list' (SQL has
    ORDER BY -- row order preserved exactly as returned) or 'table' (no ORDER BY --
    sorted deterministically by every returned column, mirroring build_gold.py's
    TABLE() convention, so two semantically-equal-but-differently-ordered engine
    results still compare equal).
    """
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    # some Spider source files have rare, genuinely-corrupted (non-UTF-8) text bytes
    # (see build_gold_spider_wta_1.py's FLAGS) -- replace rather than crash, exactly
    # like the .pkl export does, so gold computation can't fail on the same rows.
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        conn.close()

    ordered = is_ordered(sql)
    row_count = len(rows)

    if row_count == 1 and len(cols) == 1:
        return {"result_kind": "scalar", "ordered": False,
                "value": _round_value(rows[0][0]), "row_count": 1}

    records = [dict(zip(cols, (_round_value(v) for v in row))) for row in rows]
    if not ordered:
        records.sort(key=lambda r: tuple(_sort_key_value(v) for v in r.values()))
    return {"result_kind": "ordered_list" if ordered else "table", "ordered": ordered,
            "value": records, "row_count": row_count}
