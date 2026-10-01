"""S41 — headline_report() tier-blindness fix (deterministic; no API).

run.py's headline split used to hardcode a single "out_of_range" string
comparison, so any OTHER special tier (out_of_scope_register, added for the
Spider domains' table-coverage refuse cases; out_of_contract_aggregate, added for
the HAVING/subquery/multi-aggregate architectural-gap cases) would silently fall
into the plain in-range accuracy bucket instead of getting its own section —
corrupting the headline numbers for all 3 Spider domains if run against a full
eval as-is. This suite is the companion to test_s26_consolidation.py's
test_G_headline_splits_in_range_and_oor_boundary (which this fix does not
change the behavior of — see that test, still green) for the two NEW tiers.
"""
from __future__ import annotations

import json


def _rec(system, tier, outcome):
    return {"system": system, "tier": tier, "outcome_class": outcome, "qid": "q", "seed": 0}


def _write(tmp_path, recs):
    p = tmp_path / "r.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in recs))
    return p


def test_out_of_scope_register_gets_its_own_section_not_folded_into_in_range(tmp_path):
    from evidence.eval_run.run import headline_report
    recs = [
        _rec("network", "filter", "correct"),                    # ordinary Spider tier
        _rec("network", "out_of_scope_register", "silent_wrong"),
        _rec("planner", "out_of_scope_register", "correct"),
        _rec("code_agent", "out_of_scope_register", "correct"),   # must be EXCLUDED
        _rec("text_to_sql", "out_of_scope_register", "correct"),  # must be EXCLUDED
    ]
    out = headline_report(_write(tmp_path, recs))
    # the out_of_scope_register cells never contribute to in_range's count
    assert out["in_range"]["network"]["n"] == 1
    assert "out_of_scope_register" in out
    assert set(out["out_of_scope_register"]) == {"network", "planner"}
    assert "code_agent" not in out["out_of_scope_register"]
    assert "text_to_sql" not in out["out_of_scope_register"]


def test_out_of_range_and_out_of_scope_register_are_separate_sections_not_merged(tmp_path):
    """Both are refusal-boundary tiers with identical reporting SHAPE, but they
    measure different mechanisms (unsupported operation vs. unavailable data) and
    must never be combined into one number."""
    from evidence.eval_run.run import headline_report
    recs = [
        _rec("network", "out_of_range", "correct"),
        _rec("network", "out_of_scope_register", "silent_wrong"),
    ]
    out = headline_report(_write(tmp_path, recs))
    assert out["out_of_range"]["network"]["n"] == 1
    assert out["out_of_scope_register"]["network"]["n"] == 1
    assert out["out_of_range"]["network"]["correct"] == 1
    assert out["out_of_scope_register"]["network"]["silent_wrong"] == 1


def test_out_of_contract_aggregate_gets_3way_breakdown_all_systems(tmp_path):
    """The finding for this tier IS the refused/silently_wrong/correct split -- not
    a single accuracy number, and NOT restricted to the primitive systems (that
    restriction is specifically about the out_of_range/out_of_scope_register
    refuse-tier scoring rule, which doesn't apply here: gold stays
    expected_behavior='answer')."""
    from evidence.eval_run.run import headline_report
    recs = [
        _rec("network", "out_of_contract_aggregate", "refused_should_answer"),
        _rec("planner", "out_of_contract_aggregate", "silent_wrong"),
        _rec("react", "out_of_contract_aggregate", "refused_should_answer"),
        _rec("code_agent", "out_of_contract_aggregate", "correct"),
        _rec("text_to_sql", "out_of_contract_aggregate", "correct"),
    ]
    out = headline_report(_write(tmp_path, recs))
    assert "out_of_contract_aggregate" in out
    gb = out["out_of_contract_aggregate"]
    # all 5 systems present -- NOT restricted to network/planner/react
    assert set(gb) == {"network", "planner", "react", "code_agent", "text_to_sql"}
    assert gb["network"]["refused"] == 1
    assert gb["network"]["correct"] == 0
    assert gb["planner"]["silent_wrong"] == 1
    assert gb["code_agent"]["correct"] == 1
    assert gb["text_to_sql"]["correct"] == 1


def test_out_of_contract_aggregate_never_contributes_to_in_range(tmp_path):
    from evidence.eval_run.run import headline_report
    recs = [
        _rec("network", "aggregate", "correct"),                        # ordinary
        _rec("network", "out_of_contract_aggregate", "refused_should_answer"),
    ]
    out = headline_report(_write(tmp_path, recs))
    assert out["in_range"]["network"]["n"] == 1
    assert out["out_of_contract_aggregate"]["network"]["n"] == 1


def test_all_three_special_tier_kinds_coexist_correctly(tmp_path):
    """A results file could in principle carry T5 + out_of_range +
    out_of_scope_register + out_of_contract_aggregate + ordinary in-range cells all
    at once -- every bucket must be mutually exclusive and jointly exhaustive."""
    from evidence.eval_run.run import headline_report
    recs = [
        _rec("network", "T1_single_op", "correct"),
        _rec("network", "T5_high_complexity", "silent_wrong"),
        _rec("network", "out_of_range", "correct"),
        _rec("network", "out_of_scope_register", "correct"),
        _rec("network", "out_of_contract_aggregate", "correct"),
    ]
    out = headline_report(_write(tmp_path, recs))
    assert out["in_range"]["network"]["n"] == 1
    assert out["t5"]["network"]["n"] == 1
    assert out["out_of_range"]["network"]["n"] == 1
    assert out["out_of_scope_register"]["network"]["n"] == 1
    assert out["out_of_contract_aggregate"]["network"]["n"] == 1


def test_absent_tiers_produce_no_section(tmp_path):
    """A results file with none of the special tiers must not print/return empty
    sections for them (matches the existing T5-absent convention)."""
    from evidence.eval_run.run import headline_report
    out = headline_report(_write(tmp_path, [_rec("network", "T1_single_op", "correct")]))
    assert "out_of_range" not in out
    assert "out_of_scope_register" not in out
    assert "out_of_contract_aggregate" not in out
    assert "t5" not in out


def test_special_tier_registries_cover_both_new_tiers():
    from evidence.eval_run.run import REFUSAL_BOUNDARY_TIERS, ARCHITECTURAL_GAP_TIERS, _special_tier
    assert "out_of_range" in REFUSAL_BOUNDARY_TIERS
    assert "out_of_scope_register" in REFUSAL_BOUNDARY_TIERS
    assert "out_of_contract_aggregate" in ARCHITECTURAL_GAP_TIERS
    for t in ("out_of_range", "out_of_scope_register", "out_of_contract_aggregate", "T5_high_complexity"):
        assert _special_tier(t), f"{t!r} should be recognised as a special (non-in-range) tier"
    for t in ("T1_single_op", "house_vocab", "aggregate", "filter", "group_by", "order_by"):
        assert not _special_tier(t), f"{t!r} should NOT be treated as special"
