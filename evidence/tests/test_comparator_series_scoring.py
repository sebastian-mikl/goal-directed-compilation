"""# regression: a pandas Series result (grouped code_agent output) once went
# through the normalise -> compare -> verdict chain unexercised end-to-end.
Deterministic end-to-end test for the Series scoring chain.

`df_to_result` resets a pandas Series' index so the group/owner key survives
into a key COLUMN before records-conversion. That path was never exercised
LIVE (code_agent had always refused the query it would have shown up on
instead of returning a Series), so the full normalise -> compare -> verdict
chain on a Series was unconfirmed. A bug there would systematically mis-score
EVERY grouped query for ANY Series-returning system across the whole run.

This test drives the REAL path — the actual `df_to_result` (adapters/base.py) and
the actual `comparator.score` the harness uses — against the REAL gold entries,
on pandas Series that mimic a code_agent groupby result (the group/owner key in
the INDEX, the metric as the values). It re-implements neither the normaliser nor
the comparator.

Four cases (positives -> correct; negatives -> NOT correct, proving the comparator
discriminates rather than being broken-open "always correct"):
  1. ordered_list (t4_06), positive — Series in gold order            -> correct
  2. ordered_list (t4_06), negative — one value clearly changed       -> not correct
  3. table grouped (t3_15), positive — Series in a DIFFERENT row order -> correct
  4. table grouped (t3_15), negative — one value clearly wrong        -> not correct
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from evidence.eval_run.adapters.base import df_to_result      # the REAL normaliser
from evidence.eval_run.comparator import score                # the REAL comparator

ROOT = Path(__file__).resolve().parents[2]

import pytest

# Risk-domain source data is withheld from the public release; these tests skip
# cleanly rather than fail when it is absent.
_RISK_DATA = [ROOT / "environment" / "register.pkl",
              ROOT / "eval_corpus.json",
              ROOT / "evidence" / "gold" / "gold.json"]
pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in _RISK_DATA),
    reason="risk domain data not included in this release")



def _gold():
    return json.loads((ROOT / "evidence" / "gold" / "gold.json").read_text())


def _series_from_gold_rows(rows: list[dict], *, order: list[str] | None = None):
    """Build a code_agent-style groupby Series from gold rows: the group key in
    the INDEX, the metric as the values. `order` optionally reorders the rows
    (to exercise order-(in)sensitivity); default is the gold's own order."""
    keys = list(rows[0].keys())
    group_key, metric_key = keys[0], keys[1]
    by_key = {r[group_key]: r[metric_key] for r in rows}
    index_vals = order if order is not None else [r[group_key] for r in rows]
    return pd.Series([by_key[k] for k in index_vals],
                     index=pd.Index(index_vals, name=group_key),
                     name=metric_key)


# ── case 1: ordered_list, positive ──────────────────────────────────────────

def test_series_ordered_list_positive_scores_correct():
    """t4_06: a Series (Owner in index, avg_gap as values) in the gold's order
    normalises and scores CORRECT through the real chain."""
    g = _gold()["t4_06"]
    assert g["result_kind"] == "ordered_list", "gold drift"
    s = _series_from_gold_rows(g["value"])              # gold order
    norm = df_to_result(s)
    # the normaliser must have preserved the group key as a column (the FIX-1 point)
    assert isinstance(norm, list) and all("Owner" in r for r in norm), \
        f"key column dropped by normaliser: {norm}"
    verdict, reason, _ = score(norm, g)
    assert verdict == "correct", f"FIX-1 chain broken: {verdict} — {reason}\n  normalised: {norm}"


# ── case 2: ordered_list, negative (discrimination guard) ───────────────────

def test_series_ordered_list_negative_not_correct():
    """t4_06 with ONE value clearly changed (584300 -> 600000) must NOT score
    correct — proves the comparator discriminates (not broken-open)."""
    g = _gold()["t4_06"]
    rows = [dict(r) for r in g["value"]]
    rows[0]["avg_gap"] = 600000.0                       # was 584300.0
    s = _series_from_gold_rows(rows)
    verdict, reason, _ = score(df_to_result(s), g)
    assert verdict != "correct", f"comparator failed to discriminate: {reason}"


# ── case 3: table (grouped, unordered), positive ────────────────────────────

def test_series_grouped_table_positive_different_order_scores_correct():
    """t3_15 (max_cost per Strategy): a Series in a DIFFERENT row order than gold
    must still score CORRECT — grouped tables are order-insensitive."""
    g = _gold()["t3_15"]
    assert g["result_kind"] == "table", "gold drift"
    gold_order = [r["Strategy"] for r in g["value"]]
    shuffled = list(reversed(gold_order))               # deliberately not gold order
    assert shuffled != gold_order
    s = _series_from_gold_rows(g["value"], order=shuffled)
    norm = df_to_result(s)
    assert isinstance(norm, list) and all("Strategy" in r for r in norm), \
        f"key column dropped by normaliser: {norm}"
    verdict, reason, _ = score(norm, g)
    assert verdict == "correct", f"FIX-1 chain broken: {verdict} — {reason}\n  normalised: {norm}"


# ── case 4: table (grouped), negative (discrimination guard) ────────────────

def test_series_grouped_table_negative_not_correct():
    """t3_15 with ONE value clearly wrong must NOT score correct."""
    g = _gold()["t3_15"]
    rows = [dict(r) for r in g["value"]]
    rows[-1]["max_cost"] = 9_999_999.0                  # clearly wrong
    s = _series_from_gold_rows(rows)
    verdict, reason, _ = score(df_to_result(s), g)
    assert verdict != "correct", f"comparator failed to discriminate: {reason}"
