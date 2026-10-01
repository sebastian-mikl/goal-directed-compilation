"""Deterministic metrics — fed synthetic per-query records, asserts the
computed cumulative Call-2 rate, rolling token cost, and served-by
breakdown are correct.

No LLM, no DB, no orchestrator — pure pandas transforms.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from evidence.report import (
    SERVED_COLS,
    cumulative_call2_rate,
    promotion_events,
    rolling_token_cost,
    served_by_breakdown,
    total_tokens_by_mode,
)


def _row(idx, mode, **kw):
    """Build a single synthetic row with sensible defaults for the optional
    columns the report doesn't touch (so the DataFrame column set matches
    a real runs.csv)."""
    base = {
        "index": idx, "mode": mode, "family": "x", "query": f"q{idx}",
        "call1_in": 0, "call1_out": 0,
        "call2_in": 0, "call2_out": 0,
        "call3_in": 0, "call3_out": 0,
        "tokens_total_in": 0, "tokens_total_out": 0, "tokens_total": 0,
        "call1_made": 1, "call2_invoked": 0, "call2_gap_size": 0,
        "call3_answered": 1, "call3_iterations": 1,
        "served_by_graph_edge": 0,
        "served_by_astar_template": 0,
        "served_by_template": 0,
        "served_by_composite": 0,
        "served_by_llm": 0,
        "n_edges_executed": 0, "n_targets": 0,
        "n_verify_pass": 0, "n_verify_fail": 0,
        "template_promotions": 0, "composite_promotions": 0,
        "wall_clock_seconds": 0.0,
        "templated_edges_after": 0, "composite_nodes_after": 0,
    }
    base.update(kw)
    return base


def test_cumulative_call2_rate_monotone_non_increasing():
    """A run where Call 2 fires on queries 1 and 3 only: the rate is
    1.0, 0.5, 0.67, 0.5, 0.4 — monotone non-increasing after the second
    fire? No, it jumps back up. We only care that the math is correct."""
    df = pd.DataFrame([
        _row(0, "treatment", call2_invoked=1),
        _row(1, "treatment", call2_invoked=0),
        _row(2, "treatment", call2_invoked=1),
        _row(3, "treatment", call2_invoked=0),
        _row(4, "treatment", call2_invoked=0),
    ])
    out = cumulative_call2_rate(df, "treatment")
    assert list(out["cumulative_call2"]) == [1, 1, 2, 2, 2]
    expected_rates = [1.0, 0.5, 2/3, 0.5, 0.4]
    for got, want in zip(out["cum_call2_rate"], expected_rates):
        assert math.isclose(got, want, rel_tol=1e-9), (got, want)


def test_cumulative_call2_rate_filters_by_mode():
    df = pd.DataFrame([
        _row(0, "treatment", call2_invoked=1),
        _row(0, "baseline",  call2_invoked=1),
        _row(1, "treatment", call2_invoked=0),
        _row(1, "baseline",  call2_invoked=1),
    ])
    t = cumulative_call2_rate(df, "treatment")
    b = cumulative_call2_rate(df, "baseline")
    assert list(t["cum_call2_rate"]) == [1.0, 0.5]
    assert list(b["cum_call2_rate"]) == [1.0, 1.0]


def test_cumulative_call2_rate_empty():
    df = pd.DataFrame(columns=["mode", "index", "call2_invoked"])
    out = cumulative_call2_rate(df, "treatment")
    assert out.empty


def test_rolling_token_cost_window():
    df = pd.DataFrame([
        _row(0, "treatment", tokens_total=100),
        _row(1, "treatment", tokens_total=200),
        _row(2, "treatment", tokens_total=300),
        _row(3, "treatment", tokens_total=400),
    ])
    out = rolling_token_cost(df, "treatment", window=2)
    # window=2, min_periods=1: [100, (100+200)/2, (200+300)/2, (300+400)/2]
    expected = [100.0, 150.0, 250.0, 350.0]
    for got, want in zip(out["rolling_tokens"], expected):
        assert math.isclose(got, want, rel_tol=1e-9), (got, want)


def test_served_by_breakdown_has_all_columns():
    df = pd.DataFrame([
        _row(0, "treatment", served_by_graph_edge=2, served_by_llm=1),
        _row(1, "treatment", served_by_astar_template=3, served_by_composite=1),
    ])
    out = served_by_breakdown(df, "treatment")
    for c in SERVED_COLS:
        assert c in out.columns
    # Row 0: graph_edge=2, llm=1, rest=0
    assert out.iloc[0]["served_by_graph_edge"] == 2
    assert out.iloc[0]["served_by_llm"] == 1
    assert out.iloc[0]["served_by_composite"] == 0
    # Row 1: astar=3, composite=1
    assert out.iloc[1]["served_by_astar_template"] == 3
    assert out.iloc[1]["served_by_composite"] == 1


def test_promotion_events_picks_up_both_kinds():
    df = pd.DataFrame([
        _row(0, "treatment", template_promotions=1),
        _row(1, "treatment"),
        _row(2, "treatment", composite_promotions=1),
        _row(3, "treatment"),
        _row(4, "treatment", template_promotions=1, composite_promotions=1),
    ])
    out = promotion_events(df, mode="treatment")
    assert list(out["index"]) == [0, 2, 4]


def test_total_tokens_by_mode():
    df = pd.DataFrame([
        _row(0, "treatment", tokens_total=10),
        _row(1, "treatment", tokens_total=20),
        _row(0, "baseline",  tokens_total=100),
        _row(1, "baseline",  tokens_total=200),
    ])
    totals = total_tokens_by_mode(df)
    assert totals == {"treatment": 30, "baseline": 300}
    assert totals["baseline"] > totals["treatment"]


def test_build_report_produces_three_figures(tmp_path):
    """End-to-end: synthesize a runs.csv and call build_report. Three
    PNG + SVG figures must land in out_dir along with summary.json."""
    from evidence.report import build_report

    rows = [
        _row(0, "treatment", call2_invoked=1, tokens_total=500,
             template_promotions=1, served_by_llm=1),
        _row(1, "treatment", call2_invoked=0, tokens_total=50,
             served_by_astar_template=1),
        _row(2, "treatment", call2_invoked=0, tokens_total=50,
             composite_promotions=1, served_by_composite=1),
        _row(0, "baseline",  call2_invoked=1, tokens_total=500,
             served_by_llm=1),
        _row(1, "baseline",  call2_invoked=1, tokens_total=500,
             served_by_llm=1),
        _row(2, "baseline",  call2_invoked=1, tokens_total=500,
             served_by_llm=1),
    ]
    csv = tmp_path / "runs.csv"
    pd.DataFrame(rows).to_csv(csv, index=False)

    summary = build_report(csv, tmp_path)
    for stem in ("headline_call2_rate", "cost_per_query",
                 "stage_decomposition", "served_by_stacked"):
        assert (tmp_path / f"{stem}.png").exists(), stem
        assert (tmp_path / f"{stem}.svg").exists(), stem
    assert (tmp_path / "summary.json").exists()

    # Item 2 cost reframe: per-query MEANS by mode (not sums), headline = Call-2
    # rate reduction.
    cost = summary["cost"]
    assert cost["tokens_per_query_by_mode"]["baseline"] > cost["tokens_per_query_by_mode"]["treatment"]
    assert cost["call2_rate_reduction"] == pytest.approx(2 / 3)  # baseline 1.0 - treatment 1/3
    assert cost["equal_n"] is True
    # stage decomposition present
    assert "call2" in cost["stage_means_by_mode"]["treatment"]
    assert summary["promotions_by_mode"]["treatment"]["template"]  >= 1
    assert summary["promotions_by_mode"]["treatment"]["composite"] >= 1
