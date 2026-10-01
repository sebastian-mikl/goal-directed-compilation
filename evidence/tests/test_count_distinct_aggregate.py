"""S45 / Spider Fix 2 — count_distinct aggregate (deterministic; no API).

The Goal language had no distinct concept: distinct-requiring queries emitted
plain count(col) (row counts, not distinct counts — wta_08 answered 278 vs
gold 49) and the SQL spelling count(DISTINCT col), when it appeared, was
mangled by the fn(EXPR) normalizer into a "distinct col" compute — the
Finding-8 fail-open path. This suite pins:

  - the CANONICAL spelling count_distinct(col); count(DISTINCT col)
    canonicalises to it at parse time (single spelling everywhere is what
    keeps fingerprint()/the A* closed set collapsing identical goals);
  - contract semantics: count_distinct counts as THE aggregate (≤1 rule,
    grouped-grain requirement);
  - execution: nunique threading through the aggregate primitive, WITHOUT the
    numeric coercion that would corrupt distinct-counts of string columns;
  - the projection-side decision: bare SELECT DISTINCT is OUT OF CONTRACT —
    bike_09 is retiered out_of_contract_aggregate with the new
    distinct_projection architectural_gap label, gold values untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from gdc.contract import parse_goal, validate_goal, lint_goal
from gdc.state import Goal, fingerprint
from gdc.search import plan, Unreachable
from gdc.state import State
from gdc.integrate import execute_plan_ordered, aliased_view
from gdc.order import OrderSpec

ROOT = Path(__file__).resolve().parents[2]


def _payload(expr, columns=("n_distinct",), grain=None):
    return {"columns": list(columns),
            "grain": grain if grain is not None else {"grouped_by": []},
            "where": [],
            "derive": [{"name": "n_distinct", "expr": expr}]}


# ── canonical spelling + fingerprint identity (the Fix 2 interaction rule) ───

def test_sql_spelling_canonicalises_to_count_distinct():
    g = parse_goal(_payload("count(DISTINCT loser_name)"))
    assert dict(g.derive)["n_distinct"] == "count_distinct(loser_name)"
    g2 = parse_goal(_payload("count( distinct   loser_name )"))
    assert dict(g2.derive)["n_distinct"] == "count_distinct(loser_name)"


def test_fingerprints_collide_across_distinct_spellings():
    """Two goals differing ONLY in distinct-spelling must be ONE goal to the
    planner — same canonical derive, same fingerprint — or identical goals stop
    colliding in the closed set (SPIDER_FIX_PLAN.md Fix 2 interaction note)."""
    a = parse_goal(_payload("count_distinct(loser_name)"))
    b = parse_goal(_payload("count(DISTINCT loser_name)"))
    assert a == b
    assert fingerprint(a) == fingerprint(b)


def test_count_distinct_over_expression_splits_like_other_aggregates():
    g = parse_goal(_payload("count(distinct cost + emv)"))
    d = dict(g.derive)
    assert d["_n_distinct_pre"] == "cost + emv"
    assert d["n_distinct"] == "count_distinct(_n_distinct_pre)"


# ── contract semantics ───────────────────────────────────────────────────────

def test_count_distinct_counts_as_the_one_allowed_aggregate():
    g = parse_goal(_payload("count_distinct(loser_name)"))
    assert validate_goal(g).ok
    two = parse_goal({"columns": ["a", "b"], "grain": {"grouped_by": []}, "where": [],
                      "derive": [{"name": "a", "expr": "count_distinct(x)"},
                                 {"name": "b", "expr": "sum(y)"}]})
    v = validate_goal(two)
    assert not v.ok and "more than one aggregate" in v.reason


def test_count_distinct_requires_grouped_grain():
    row_grain = parse_goal(_payload("count_distinct(loser_name)", grain="row"))
    v = validate_goal(row_grain)
    assert not v.ok and "aggregate requires grouped grain" in v.reason


def test_dead_count_distinct_still_lint_refused():
    """wta_26's live shape: a count_distinct derive consumed by nothing must
    keep refusing via the S35 dead-derive lint (recognition must not create a
    path around the lint)."""
    g = parse_goal({"columns": ["winner_name"], "grain": "row", "where": [],
                    "derive": [{"name": "year_count", "expr": "count_distinct(year)"}]})
    assert not lint_goal(g).ok


# ── execution (global + grouped, string column, no numeric coercion) ─────────

def _frame():
    return pd.DataFrame({
        "name":   ["a", "b", "a", "c", "b", "a"],
        "region": ["N", "N", "N", "S", "S", "S"],
        "cost":   [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })


def _run(goal, frame):
    _amap, adf, schema = aliased_view(frame)
    p = plan(State.from_base(schema), goal)
    assert not isinstance(p, Unreachable), getattr(p, "reason", None)
    return execute_plan_ordered(p, adf, OrderSpec.make())


def test_global_count_distinct_on_string_column():
    g = parse_goal({"columns": ["n_distinct"], "grain": {"grouped_by": []}, "where": [],
                    "derive": [{"name": "n_distinct", "expr": "count_distinct(name)"}]})
    out = _run(g, _frame())
    assert out.shape == (1, 1)
    assert int(out.iloc[0, 0]) == 3          # a, b, c — NOT 6 (row count)


def test_grouped_count_distinct_per_group():
    g = parse_goal({"columns": ["region", "n_distinct"],
                    "grain": {"grouped_by": ["region"]}, "where": [],
                    "derive": [{"name": "n_distinct", "expr": "count_distinct(name)"}]})
    out = _run(g, _frame())
    got = dict(zip(out["region"], out["n_distinct"]))
    assert got == {"N": 2, "S": 3}           # N: a,b ; S: c,b,a


def test_plain_count_still_counts_rows():
    """count(col) semantics unchanged: rows, not distinct values."""
    g = parse_goal({"columns": ["n"], "grain": {"grouped_by": []}, "where": [],
                    "derive": [{"name": "n", "expr": "count(name)"}]})
    out = _run(g, _frame())
    assert int(out.iloc[0, 0]) == 6


# ── projection-side decision: bike_09 retier ─────────────────────────────────

def test_bike_09_retiered_out_of_contract_distinct_projection():
    corpus = {q["id"]: q for q in json.loads(
        (ROOT / "evaluation/domains/spider_bike_1/eval_corpus_spider_bike_1.json").read_text())["queries"]}
    gold = json.loads((ROOT / "evidence" / "gold" / "gold_spider_bike_1.json").read_text())
    for rec in (corpus["bike_09"], gold["bike_09"]):
        assert rec["tier"] == "out_of_contract_aggregate"
        assert rec["architectural_gap"] == ["distinct_projection"]
    # gold VALUE untouched: still the distinct station list, answer-expected
    assert gold["bike_09"]["expected_behavior"] == "answer"
    assert isinstance(gold["bike_09"]["value"], list) and len(gold["bike_09"]["value"]) > 1
