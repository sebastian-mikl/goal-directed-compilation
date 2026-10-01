"""Comparator correctness — general matching rules (deterministic, no LLM).

Red-tested on SYNTHETIC gold records only (no eval queries, no gold values — see
the integrity guard):

  1a  id-less ordered fallback: an ordered list whose produced rows omit
      employee_id is keyed positionally on the gold non-id columns
      (ORDER-SENSITIVE) instead of scoring "got None".
  1b  renamed-metric guard (VALUE-verified): a gold numeric column absent by NAME
      must be matched by VALUE by some produced NUMERIC column at every position;
      if no produced column carries the gold values, the result is NOT correct.
      Without this, numeric checks are silently skipped and a result is graded on
      key order alone.
  1c  fallback superset projection (VALUE-verified rename tolerance, symmetric
      with 1b): the id-less fallbacks require produced to cover all gold non-id
      columns, but an absent column may be matched by a renamed produced column
      carrying the same values; an under-projection (no value match) still fails.
      Extra columns stay tolerated.
  1d  internal-tie set rule: within a run of gold rows tied on EVERY numeric
      column, any permutation of the tied rows is correct (compared as a
      multiset); a foreign entity inside the tie is still wrong. Boundary
      substitutions (the excluded (k+1)-th row) remain wrong — not in gold.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _g(rows, kind="ordered_list"):
    return {"result_kind": kind, "value": rows, "expected_behavior": "answer"}


def _score(produced, gold):
    from evidence.eval_run.comparator import score
    return score(produced, gold)


# ── FIX 1a: id-less ordered fallback ─────────────────────────────────────────

_ORDERED_RID = [
    {"employee_id": "R1", "Title": "alpha", "Cost": 50.0},
    {"employee_id": "R2", "Title": "beta", "Cost": 40.0},
    {"employee_id": "R3", "Title": "gamma", "Cost": 30.0},
]


def _drop_rid(rows):
    return [{k: v for k, v in r.items() if k != "employee_id"} for r in rows]


def test_1a_idless_ordered_correct_entities_and_order_is_correct():
    produced = _drop_rid(_ORDERED_RID)
    cls, reason, _ = _score(produced, _g(_ORDERED_RID))
    assert cls == "correct", f"id-less ordered correct projection must match — {cls}: {reason}"


def test_1a_idless_ordered_wrong_order_is_silent_wrong():
    produced = _drop_rid(_ORDERED_RID)
    produced[0], produced[1] = produced[1], produced[0]      # 50/40 not tied
    assert _score(produced, _g(_ORDERED_RID))[0] == "silent_wrong"


def test_1a_idless_ordered_wrong_entities_is_silent_wrong():
    produced = _drop_rid(_ORDERED_RID)
    produced[2] = {"Title": "INTRUDER", "Cost": 30.0}
    assert _score(produced, _g(_ORDERED_RID))[0] == "silent_wrong"


def test_1a_rid_bearing_ordered_path_unchanged():
    """Regression: when produced carries employee_id, the existing keyed path applies."""
    good = [dict(r) for r in _ORDERED_RID]
    assert _score(good, _g(_ORDERED_RID))[0] == "correct"
    swapped = [dict(r) for r in _ORDERED_RID]
    swapped[0], swapped[1] = swapped[1], swapped[0]
    assert _score(swapped, _g(_ORDERED_RID))[0] == "silent_wrong"
    wrong_id = [dict(r) for r in _ORDERED_RID]
    wrong_id[2]["employee_id"] = "R99"
    assert _score(wrong_id, _g(_ORDERED_RID))[0] == "silent_wrong"


# ── FIX 1b: renamed-metric guard ─────────────────────────────────────────────

_ORDERED_GROUPED = [
    {"Owner": "A", "gap": 5.5},
    {"Owner": "B", "gap": 4.5},
    {"Owner": "C", "gap": 3.5},
]


def test_1b_renamed_metric_correct_values_verified_by_value():
    """Correct keys + correct values under a RENAMED metric column: the values ARE
    verifiable (matched by value in a produced numeric column) -> correct. The old
    comparator also said correct, but by silently skipping every numeric check."""
    produced = [{"Owner": r["Owner"], "mean_gap": r["gap"]} for r in _ORDERED_GROUPED]
    cls, reason, _ = _score(produced, _g(_ORDERED_GROUPED))
    assert cls == "correct"
    assert "matched by value" in reason                  # verified, not skipped


def test_1b_renamed_metric_wrong_values_is_wrong():
    """Correct keys + ABSURD values under a renamed metric column: the audit case —
    was correct (numeric checks silently skipped), must be wrong."""
    produced = [{"Owner": r["Owner"], "mean_gap": r["gap"] * 1000 + 7} for r in _ORDERED_GROUPED]
    assert _score(produced, _g(_ORDERED_GROUPED))[0] == "silent_wrong"


def test_1b_metric_never_computed_is_wrong():
    """Correct keys, requested metric NOT produced under any name (no numeric column
    carries the gold values): was correct on key order alone, must be wrong."""
    produced = [{"Owner": r["Owner"], "rank": float(i)} for i, r in enumerate(_ORDERED_GROUPED)]
    assert _score(produced, _g(_ORDERED_GROUPED))[0] == "silent_wrong"


def test_1b_string_column_can_never_satisfy_the_value_match():
    """Regression: a STRING column must never be picked to verify a numeric
    metric, even when the metric is absent by name."""
    produced = [{"Owner": r["Owner"], "label": str(r["gap"])} for r in _ORDERED_GROUPED]
    assert _score(produced, _g(_ORDERED_GROUPED))[0] == "silent_wrong"


def test_1b_gold_named_metric_correct_stays_correct():
    produced = [dict(r) for r in _ORDERED_GROUPED]
    assert _score(produced, _g(_ORDERED_GROUPED))[0] == "correct"


def test_1b_gold_named_metric_wrong_value_stays_wrong():
    produced = [dict(r) for r in _ORDERED_GROUPED]
    produced[1]["gap"] = 999.0
    assert _score(produced, _g(_ORDERED_GROUPED))[0] == "silent_wrong"


def test_1b_applies_to_rid_path_too():
    """The value-verified guard holds on the id-keyed path as well: renamed metric
    with correct values -> correct; with wrong values -> wrong."""
    ok = [{"employee_id": r["employee_id"], "price": r["Cost"]} for r in _ORDERED_RID]
    assert _score(ok, _g(_ORDERED_RID))[0] == "correct"
    bad = [{"employee_id": r["employee_id"], "price": r["Cost"] + 123456.0} for r in _ORDERED_RID]
    assert _score(bad, _g(_ORDERED_RID))[0] == "silent_wrong"


# ── FIX 1c: fallback superset projection (row-list AND ordered) ──────────────

_ROWLIST_RID = [
    {"employee_id": "R1", "Title": "alpha", "Owner": "X"},
    {"employee_id": "R2", "Title": "beta", "Owner": "Y"},
]


def test_1c_full_projection_still_correct():
    produced = _drop_rid(_ROWLIST_RID)
    assert _score(produced, _g(_ROWLIST_RID, kind="table"))[0] == "correct"


def test_1c_under_projection_is_wrong():
    """Produced has FEWER columns than gold ({Title} vs {Title, Owner}): missing a
    requested column is not a match (was: correct via intersection)."""
    produced = [{"Title": r["Title"]} for r in _ROWLIST_RID]
    assert _score(produced, _g(_ROWLIST_RID, kind="table"))[0] == "silent_wrong"


def test_1c_extra_columns_still_tolerated():
    produced = [dict(r, extra_col="anything") for r in _drop_rid(_ROWLIST_RID)]
    assert _score(produced, _g(_ROWLIST_RID, kind="table"))[0] == "correct"


def test_1c_under_projection_is_wrong_for_idless_ordered_too():
    produced = [{"Title": r["Title"]} for r in _ORDERED_RID]
    assert _score(produced, _g(_ORDERED_RID))[0] == "silent_wrong"


def test_1c_idless_rowlist_renamed_column_correct_via_value():
    """Symmetric with 1b on the id-less rowlist path: a renamed gold column whose
    values match positionally (multiset for rowlist) is correct."""
    produced = [{"Title": r["Title"], "owner_name": r["Owner"]} for r in _ROWLIST_RID]
    cls, reason, _ = _score(produced, _g(_ROWLIST_RID, kind="table"))
    assert cls == "correct"
    assert "values matched by rename" in reason


def test_1c_idless_rowlist_under_projection_still_wrong():
    """No produced column carries the missing gold column's values -> under-projection."""
    produced = [{"Title": r["Title"], "extra": "junk"} for r in _ROWLIST_RID]
    assert _score(produced, _g(_ROWLIST_RID, kind="table"))[0] == "silent_wrong"


def test_1c_idless_ordered_renamed_column_correct_via_value():
    """Symmetric with 1b on the id-less ordered path: a renamed gold column whose
    values match positionally is correct."""
    produced = [{"name": r["Title"], "price": r["Cost"]} for r in _ORDERED_RID]
    cls, reason, _ = _score(produced, _g(_ORDERED_RID))
    assert cls == "correct"
    assert "values matched by rename" in reason


def test_1c_idless_ordered_string_substitute_must_match_string():
    """Type guard on the id-less path: a numeric column can never be substituted
    by a string column, even if the strings happen to be the stringified numbers."""
    produced = [{"Title": r["Title"], "label": str(r["Cost"])} for r in _ORDERED_RID]
    assert _score(produced, _g(_ORDERED_RID))[0] == "silent_wrong"


# ── FIX 1d: internal-tie set rule ────────────────────────────────────────────

_ORDERED_TIED = [
    {"employee_id": "R1", "Cost": 50.0},
    {"employee_id": "R2", "Cost": 40.0},
    {"employee_id": "R3", "Cost": 30.0},   # tied pair: positions 2 and 3
    {"employee_id": "R4", "Cost": 30.0},
    {"employee_id": "R5", "Cost": 20.0},
]


def test_1d_swap_within_tie_is_correct():
    produced = [dict(r) for r in _ORDERED_TIED]
    produced[2], produced[3] = produced[3], produced[2]
    cls, reason, _ = _score(produced, _g(_ORDERED_TIED))
    assert cls == "correct", f"a permutation within a tied run is a valid order — {cls}: {reason}"


def test_1d_foreign_entity_in_tie_is_wrong():
    produced = [dict(r) for r in _ORDERED_TIED]
    produced[2] = {"employee_id": "R99", "Cost": 30.0}          # not a member of the tie
    assert _score(produced, _g(_ORDERED_TIED))[0] == "silent_wrong"


def test_1d_no_ties_keeps_positional_behaviour():
    produced = [dict(r) for r in _ORDERED_RID]
    produced[1], produced[2] = produced[2], produced[1]      # 40/30 not tied
    assert _score(produced, _g(_ORDERED_RID))[0] == "silent_wrong"
    assert _score([dict(r) for r in _ORDERED_RID], _g(_ORDERED_RID))[0] == "correct"


def test_1d_boundary_substitution_still_wrong():
    """build_gold flags k-vs-(k+1) boundary ties; the excluded (k+1)-th row is NOT in
    gold, so swapping it in cannot pass the set rule (its id is not in the tied
    group), whether the boundary row is internally tied or not."""
    # untied boundary: top-3 of [50, 40, 30, (30 excluded)]
    gold = _ORDERED_RID
    produced = [dict(r) for r in gold]
    produced[2] = {"employee_id": "R9", "Title": "gamma", "Cost": 30.0}
    assert _score(produced, _g(gold))[0] == "silent_wrong"
    # internally-tied boundary: positions 2,3 tied AND the excluded row tied too
    produced = [dict(r) for r in _ORDERED_TIED]
    produced[3] = {"employee_id": "R9", "Cost": 30.0}
    assert _score(produced, _g(_ORDERED_TIED))[0] == "silent_wrong"


def test_1d_swap_within_tie_idless_is_correct():
    """The tie rule is path-independent: an id-less projection of a tied run may
    also permute the tied rows."""
    produced = _drop_rid(_ORDERED_TIED)
    produced[2], produced[3] = produced[3], produced[2]      # identical tuples here
    assert _score(produced, _g(_ORDERED_TIED))[0] == "correct"


def test_1d_grouped_topk_tie():
    """Grouped top-k (no employee_id anywhere): tied metric rows may permute; a wrong
    group label inside the tie stays wrong."""
    gold = [{"Owner": "A", "count": 9.0}, {"Owner": "B", "count": 7.0},
            {"Owner": "C", "count": 7.0}, {"Owner": "D", "count": 5.0}]
    produced = [dict(r) for r in gold]
    produced[1], produced[2] = produced[2], produced[1]
    assert _score(produced, _g(gold))[0] == "correct"
    produced = [dict(r) for r in gold]
    produced[1] = {"Owner": "Z", "count": 7.0}
    assert _score(produced, _g(gold))[0] == "silent_wrong"


# ── FIX s37: non-numeric (text) values in scalar / grouped-metric comparisons ──

def test_s37_text_scalar_matches_by_equality():
    gold = {"result_kind": "scalar", "value": "Serena Williams", "expected_behavior": "answer"}
    assert _score("Serena Williams", gold)[0] == "correct"
    assert _score("Venus Williams", gold)[0] == "silent_wrong"


def test_s37_text_grouped_metric_matches_by_equality():
    """Grouped-aggregate whose metric column is text (e.g. GROUP BY ... a label,
    not a number) — was always "metric mismatch" even when equal, since _num_eq
    only knew how to subtract."""
    gold = [{"sum(Population)": 78000, "GovernmentForm": "Parliamentary Coprincipality"}]
    assert _score(gold, _g(gold, kind="table"))[0] == "correct"
    wrong = [{"sum(Population)": 78000, "GovernmentForm": "Something Else"}]
    assert _score(wrong, _g(gold, kind="table"))[0] == "silent_wrong"


def test_s37_single_column_table_matches_by_equality():
    """A plain SELECT of one text column (no id, no metric — e.g. `SELECT Name FROM
    country WHERE ...`) collapses group-key and metric onto the same column; a
    byte-identical answer must score correct, not "metric mismatch at X: got X"."""
    gold = [{"Name": "Algeria"}, {"Name": "Angola"}]
    assert _score(gold, _g(gold, kind="table"))[0] == "correct"
    wrong = [{"Name": "Algeria"}, {"Name": "WRONG"}]
    assert _score(wrong, _g(gold, kind="table"))[0] == "silent_wrong"


# ── integrity guard + version ────────────────────────────────────────────────

def test_integrity_comparator_has_no_eval_content():
    """Every s36 rule is a general matching rule: no query ids, no gold values."""
    src = (ROOT / "evidence" / "eval_run" / "comparator.py").read_text()
    assert not re.search(r"\b(t[1-5]_\d{2}|oor_\d{2}|hv_\d{2})\b", src), "query id in comparator.py"
    for val in ("4836800", "211846", "45491000"):           # gold values (max/mean/sum Cost)
        assert val not in src, f"gold value {val} baked into comparator.py"


def test_comparator_version_bumped_to_s36():
    """Pins the CURRENT comparator version so any scoring-rule edit that forgets
    the bump fails here. s36: value-verified renames etc.; s37: _num_eq string
    fallback; s46: scalar unwrap + grouped key-orientation (the comparator-
    correctness pass, adversarially gated in
    test_comparator_scalar_unwrap_adversarial.py); s47: orientation replaced
    by name-based guarded normalization + case-fold
    (test_comparator_namekey_casefold_adversarial.py)."""
    from evidence.eval_run.comparator import COMPARATOR_VERSION
    assert COMPARATOR_VERSION == "s48"
