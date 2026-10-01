"""s46 comparator-correctness pass — adversarial gate + intended recoveries.

The s46 changes (scalar unwrap; grouped key-orientation) loosen SHAPE/NAMING
technicalities, never value requirements. This suite is the MANDATORY
acceptance gate: every known-WRONG answer below must STAY wrong after the
loosening — any one of them flipping to correct means the change is too
permissive and must be tightened before it lands.
"""
from __future__ import annotations

from evidence.eval_run.comparator import score


def _scalar_gold(v):
    return {"expected_behavior": "answer", "result_kind": "scalar", "value": v}


def _table_gold(rows):
    return {"expected_behavior": "answer", "result_kind": "table", "value": rows}


# ═══════════════ ADVERSARIAL SET — every one must STAY WRONG ═══════════════

def test_A1_scalar_1x2_with_wrong_value_stays_wrong():
    out, reason, _ = score([{"name": "Wrong Person", "points": 123.0}], _scalar_gold("Serena Williams"))
    assert out == "silent_wrong", reason


def test_A2_correct_headers_wrong_row_values_stays_wrong():
    gold = _table_gold([{"Continent": "Asia", "n": 51}, {"Continent": "Europe", "n": 46}])
    out, reason, _ = score([{"Continent": "Asia", "n": 999}, {"Continent": "Europe", "n": 46}], gold)
    assert out == "silent_wrong", reason


def test_A3_correct_values_missing_required_column_stays_wrong():
    """The t2_07 class (leak-masked under-projection) must not resurrect."""
    gold = _table_gold([{"employee_id": "E-1", "Title": "a", "gap": 1.0},
                        {"employee_id": "E-2", "Title": "b", "gap": 2.0}])
    out, reason, _ = score([{"gap": 1.0}, {"gap": 2.0}], gold)   # no id, no Title
    assert out == "silent_wrong", reason


def test_A4_renamed_column_with_nonmatching_values_stays_wrong():
    """The key header-matching stressor: a differently-named column whose
    VALUES don't match gold must stay wrong (value-verified rename only)."""
    gold = _table_gold([{"employee_id": "E-1", "Title": "a", "gap": 1.0},
                        {"employee_id": "E-2", "Title": "b", "gap": 2.0}])
    produced = [{"Title": "a", "delta": 9.0}, {"Title": "b", "delta": 8.0}]
    out, reason, _ = score(produced, gold)
    assert out == "silent_wrong", reason


def test_A5_wide_row_containing_gold_value_stays_wrong():
    """The pre-S42 world_04 leak shape: a full register row that happens to
    contain the right value must NOT unwrap (>2-column guard)."""
    row = {"Code": "AIA", "Name": "Anguilla", "Continent": "North America",
           "Region": "Caribbean", "Population": 8000}
    out, reason, _ = score([row], _scalar_gold("North America"))
    assert out == "silent_wrong", reason


def test_A6_multirow_column_containing_gold_stays_wrong():
    """A genuinely multi-value result (N rows x 1 col) containing the gold
    value somewhere is not a scalar answer."""
    out, reason, _ = score([{"cost": 10.0}, {"cost": 4836800.0}, {"cost": 7.0}],
                           _scalar_gold(4836800.0))
    assert out == "silent_wrong", reason


def test_A7_grouped_inconsistent_pairing_fails_all_orientations():
    """Right key set + right metric multiset but WRONG pairing must stay wrong
    under the orientation fallback (pairing-consistency, not value-sniffing)."""
    gold = _table_gold([{"count(*)": 12, "year": 2013}, {"count(*)": 158, "year": 2016}])
    produced = [{"year": 2013, "match_count": 158}, {"year": 2016, "match_count": 12}]
    out, reason, _ = score(produced, gold)
    assert out == "silent_wrong", reason


def test_A8_scalar_1x2_no_cell_matching_stays_wrong():
    out, reason, _ = score([{"id": 906211, "duration": 61}], _scalar_gold(906210))
    assert out == "silent_wrong", reason


# ═══════════════ INTENDED RECOVERIES — the four verified target shapes ══════

def test_P1_wta14_shape_grouped_argmax_1x2_scalar_gold():
    out, reason, _ = score([{"year": 2016, "match_count": 158}], _scalar_gold(2016))
    assert out == "correct", reason


def test_P2_wta16_shape_value_beside_ranking_column():
    out, reason, _ = score([{"winner_name": "Serena Williams", "winner_rank_points": 9945}],
                           _scalar_gold("Serena Williams"))
    assert out == "correct", reason


def test_P3_wta22_shape_metric_first_gold_orientation():
    """Spider `SELECT count(*), YEAR` gold: metric-first column order used to
    make the comparator key groups on the counts. Value-perfect tables now
    match because the group key `year` is identified by NAME (s47 guarded
    name-normalization, replacing s46's value-based orientation) — including
    with the executor's `year__group` naming, which the `__group` strip
    normalizes. A wrong metric under the same shape still fails."""
    gold = _table_gold([{"count(*)": 12, "year": 2013},
                        {"count(*)": 108, "year": 2017},
                        {"count(*)": 158, "year": 2016}])
    produced = [{"year__group": 2013, "match_count": 12},
                {"year__group": 2016, "match_count": 158},
                {"year__group": 2017, "match_count": 108}]
    out, reason, _ = score(produced, gold)
    assert out == "correct", reason
    # and a wrong metric under the same shape still fails
    bad = [dict(produced[0], match_count=99)] + produced[1:]
    out2, reason2, _ = score(bad, gold)
    assert out2 == "silent_wrong", reason2


def test_P4_plain_scalar_paths_unchanged():
    assert score(4836800.0, _scalar_gold(4836800.0))[0] == "correct"
    assert score(1.0, _scalar_gold(2.0))[0] == "silent_wrong"
    assert score([{"only": 906210}], _scalar_gold(906210))[0] == "correct"   # 1x1 unwrap


def test_P5_both_cells_equal_gold_is_correct():
    """Ambiguity guard: when BOTH cells equal gold the verdict is identical
    whichever cell is 'the' answer — correct, unambiguously."""
    out, reason, _ = score([{"a": 42.0, "b": 42.0}], _scalar_gold(42.0))
    assert out == "correct", reason


def test_P6_keyfirst_grouped_gold_unchanged():
    """Ordinary key-first grouped gold (risk/employee/world shape) must take the
    original path — same verdicts as before s46."""
    gold = _table_gold([{"Continent": "Asia", "n": 51}, {"Continent": "Europe", "n": 46}])
    assert score([{"Continent": "Asia", "n": 51}, {"Continent": "Europe", "n": 46}], gold)[0] == "correct"
    assert score([{"Continent": "Asia", "n": 51}], gold)[0] == "silent_wrong"
