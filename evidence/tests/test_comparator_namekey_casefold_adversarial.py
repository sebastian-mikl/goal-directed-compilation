"""s47 — de-risked comparator pass: name-based guarded normalization + case-fold.

s46's grouped fallback was value-based (inferred column role from value-set
consistency) and carried a mis-pairing hole. s47 replaces it with NAME-based
group-key identification (conservative normalization: case + `__group` +
underscores, NOT aggregate wrappers, NOT punctuation) and adds case-insensitive
header matching, both guarded so distinct columns are never merged. This suite
is the adversarial gate: the collision cases the investigation surfaced must
NOT flip any wrong answer to correct, and distinct-but-similar columns must
stay distinct.
"""
from __future__ import annotations

from evidence.eval_run.comparator import score, _norm_col, _ci_find


def _tbl(rows):
    return {"expected_behavior": "answer", "result_kind": "table", "value": rows}


def _scalar(v):
    return {"expected_behavior": "answer", "result_kind": "scalar", "value": v}


# ═══════════ INTENDED RECOVERIES (name-based, no value-role inference) ═══════

def test_wta22_recovers_via_name_not_value():
    gold = _tbl([{"count(*)": 12, "year": 2013}, {"count(*)": 158, "year": 2016}])
    for prod in ([{"year__group": 2013, "match_count": 12},
                  {"year__group": 2016, "match_count": 158}],           # network
                 [{"year": 2013, "number_of_matches": 12},
                  {"year": 2016, "number_of_matches": 158}]):           # text_to_sql
        assert score(prod, gold)[0] == "correct", prod


def test_world15_recovers_via_name_key():
    gold = _tbl([{"sum(Population)": 78000, "GovernmentForm": "A"},
                 {"sum(Population)": 3947000, "GovernmentForm": "B"}])
    prod = [{"government_form": "A", "total_population": 78000},
            {"government_form": "B", "total_population": 3947000}]       # code_agent naming
    assert score(prod, gold)[0] == "correct"


def test_case_only_grouped_key_recovers():
    """The literal case-fold target: group key differs only by case."""
    gold = _tbl([{"CONTINENT": "Asia", "n": 51}, {"CONTINENT": "Europe", "n": 46}])
    prod = [{"continent": "Asia", "n": 51}, {"continent": "Europe", "n": 46}]
    assert score(prod, gold)[0] == "correct"


# ═══════════ ADVERSARIAL — collisions must NOT merge / mis-match ═════════════

def test_norm_keeps_pct_vs_dollar_distinct():
    """The employee collision: Avg Gap (%) vs Avg Gap ($) must NOT normalize
    together (punctuation preserved)."""
    assert _norm_col("Avg Gap (%)") != _norm_col("Avg Gap ($)")


def test_norm_keeps_count_of_key_distinct_from_key():
    """The bike_13 collision: count(zip_code) vs zip_code__group must stay
    distinct (aggregate wrapper NOT stripped)."""
    assert _norm_col("count(zip_code)") != _norm_col("zip_code__group")


def test_norm_collapses_group_artifact_and_case():
    """Intended merges: __group suffix, underscores, and case."""
    assert _norm_col("year__group") == _norm_col("year") == "year"
    assert _norm_col("GovernmentForm") == _norm_col("government_form") == "governmentform"


def test_grouped_collision_guard_falls_back_not_mispairs():
    """A 2x2 produced table whose two columns normalize to the SAME string
    (winner_ioc vs winner_ioc__group — the vc==gb executor artifact) must NOT
    be name-resolved: the within-side collision guard skips the fix, so a wrong
    answer cannot slip through it."""
    gold = _tbl([{"count(*)": 5, "ioc": "USA"}, {"count(*)": 3, "ioc": "GER"}])
    # produced counts are WRONG (swapped) and both cols collide under norm
    prod = [{"winner_ioc": "USA", "winner_ioc__group": 3},
            {"winner_ioc": "GER", "winner_ioc__group": 5}]
    assert score(prod, gold)[0] == "silent_wrong"


def test_grouped_inconsistent_pairing_still_wrong_under_name_id():
    """Right keys, right metric multiset, WRONG pairing: name-ID finds the key,
    the strict metric compare rejects the wrong association."""
    gold = _tbl([{"count(*)": 12, "year": 2013}, {"count(*)": 158, "year": 2016}])
    prod = [{"year": 2013, "match_count": 158}, {"year": 2016, "match_count": 12}]
    assert score(prod, gold)[0] == "silent_wrong"


def test_grouped_wrong_metric_value_still_wrong():
    gold = _tbl([{"count(*)": 12, "year": 2013}, {"count(*)": 158, "year": 2016}])
    prod = [{"year": 2013, "match_count": 99}, {"year": 2016, "match_count": 158}]
    assert score(prod, gold)[0] == "silent_wrong"


def test_grouped_wrong_key_set_still_wrong():
    gold = _tbl([{"count(*)": 12, "year": 2013}, {"count(*)": 158, "year": 2016}])
    prod = [{"year": 2013, "match_count": 12}, {"year": 9999, "match_count": 158}]
    assert score(prod, gold)[0] == "silent_wrong"


# ═══════════ CASE-FOLD helper: ambiguity is NOT collapsed ════════════════════

def test_ci_find_exact_and_unique_case_match():
    assert _ci_find({"Continent": 1}, "continent") == "Continent"
    assert _ci_find({"continent": 1}, "CONTINENT") == "continent"


def test_ci_find_ambiguous_case_returns_none():
    """Two keys differing ONLY by case: matching either would be a coin-flip, so
    _ci_find returns None (the case-fold analog of the mis-pairing guard)."""
    assert _ci_find({"Cost": 1, "cost": 2}, "COST") is None


def test_ci_find_absent_returns_none():
    assert _ci_find({"a": 1, "b": 2}, "c") is None


# ═══════════ 3+-column grouped tables keep the strict first-column rule ══════

def test_three_column_grouped_not_name_resolved():
    """The guarded fix is 2x2 only; a 3-column table takes the strict path
    unchanged (no name-normalization escape hatch)."""
    gold = {"expected_behavior": "answer", "result_kind": "table",
            "value": [{"Continent": "Asia", "n": 51, "avg_pop": 100.0}]}
    prod = [{"continent": "Asia", "count": 51, "average": 100.0}]
    # first-column key is 'Continent'; produced 'continent' matches case-insensitively,
    # metric 'n' is absent by name -> value-verified against 'count'/'average'
    assert score(prod, gold)[0] == "correct"
    bad = [{"continent": "Asia", "count": 999, "average": 100.0}]
    assert score(bad, gold)[0] == "silent_wrong"
