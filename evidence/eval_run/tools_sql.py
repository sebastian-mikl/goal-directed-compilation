"""text-to-SQL tool: emit a single SELECT against a sqlite load of the same data.

The register is loaded into an in-memory sqlite database with the table named
`register`. Column names with spaces / parentheses (e.g. "Unit Price (Net)") are
quoted in standard SQL ("Unit Price (Net)"). The LLM is told exactly that and given
the column list verbatim. SQL execution errors are caught and returned as a
REFUSAL (status=refused) — silent_wrong cannot arise from a syntax error.

A model-authored query can be genuinely pathological, not just wrong: e.g. a
`WITH RECURSIVE` walk over a self-referential column (a manager/reports-to chain)
has no SQL-level guarantee of terminating if that chain contains a cycle in the
data — SQLite will keep expanding the recursion forever. run_sql() bounds
execution wall-clock via sqlite3's own progress handler (checked periodically
during the query, not just at the end) so a hung query aborts and surfaces as an
ordinary caught exception — the SAME refusal path as a syntax error — instead of
blocking the whole process indefinitely.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing

import pandas as pd


SQL_TOOLS = [
    {"name": "run_sql",
     "description": ("Run a single SELECT statement against the `register` table "
                     "in sqlite. Quote column names with spaces / parens like "
                     "\"Unit Price (Net)\". On success a DataFrame is returned and "
                     "becomes the final answer."),
     "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}},
                      "required": ["sql"]}},
]


def load_sqlite(df: pd.DataFrame) -> sqlite3.Connection:
    """In-memory sqlite db with one table named `register` mirroring df."""
    conn = sqlite3.connect(":memory:")
    df.to_sql("register", conn, index=False, if_exists="replace")
    return conn


def run_sql(sql: str, df: pd.DataFrame, timeout_s: float | None = None):
    """Execute SQL against an ephemeral sqlite mirror of df.

    `timeout_s`: wall-clock bound on this one query, enforced via sqlite3's
    progress handler (invoked periodically DURING execution, unlike a plain
    try/except which can't interrupt a query that's still running). None
    disables the bound entirely (matches the old, unbounded behaviour).

    Returns (summary_text, result_obj|None, error|None). result_obj is the
    DataFrame on success; None + error str on failure (including a timeout —
    the caller can't tell them apart, by design: both are "no answer", not
    silent_wrong, exactly like every other SQL execution failure here).
    """
    try:
        with closing(load_sqlite(df)) as conn:
            if timeout_s is not None:
                deadline = time.monotonic() + timeout_s
                # SQLite calls this every N VM instructions; a non-zero return
                # aborts the currently-executing statement with an OperationalError,
                # caught below like any other SQL failure. N=1000 keeps the check
                # cheap relative to typical query cost while still catching a
                # runaway query promptly rather than after the fact.
                conn.set_progress_handler(
                    lambda: 1 if time.monotonic() > deadline else 0, 1000)
            out = pd.read_sql_query(sql, conn)
    except Exception as exc:                              # noqa: BLE001
        return f"error: {type(exc).__name__}: {exc}", None, str(exc)
    return _summary(out, "result"), out, None


def _summary(frame: pd.DataFrame, name: str, n: int = 5) -> str:
    cols = [str(c) for c in frame.columns]
    try:
        head = frame.head(n).to_dict("records")
    except Exception:
        head = "<unprintable>"
    return (f"frame '{name}': {len(frame)} rows x {len(cols)} cols {cols}\n"
            f"head: {head}")
