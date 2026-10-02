"""D9 (2 Oct 2026): tie-tolerant scoring is reported beside strict on the employee domain too, as on
Spider. employee t3_04 (11 rows tied for 4 places) and t3_13 (3 for 2) have limits that cut
through a tie (reports/tie_alternatives_synthetic.json); under s48t a ranked answer that equals
a data-verified tied filling scores correct. Risk has no tie, so it has no tolerant rows.
The builder emits scoring=tolerant rows beside the strict ones in Table1 (employee in-contract
tiers) and Table2 (employee in_contract_answer), comparator s48t, same key convention as the
Spider rows. Deterministic; no API.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from evidence.eval_run.comparator import score
from scripts.score_tie_tolerant import SCORER_VERSION, score_tt, score_tt_syn, tie_alt_values_syn

ROOT = Path(__file__).resolve().parents[2]
GOLD = json.loads((ROOT / "evidence" / "gold" / "gold_employee.json").read_text())
SYSTEMS = ("network", "planner", "react", "code_agent", "text_to_sql")
IN_CONTRACT = ("T1_single_op", "T2_two_ops", "T3_three_ops", "T4_business_rule_multi_op", "T5_high_complexity", "house_vocab")
FIVE = ("accuracy", "silent_wrong", "refused_should_answer", "crash", "timeout")


def test_the_tie_questions_have_alternatives_and_the_others_have_none():
    assert tie_alt_values_syn("employee", "t3_04") and tie_alt_values_syn("employee", "t3_13")
    assert tie_alt_values_syn("employee", "t3_01") == []
    assert tie_alt_values_syn("risk", "t3_04") == []                # the qid spaces overlap; risk has no tie
    assert tie_alt_values_syn("employee", "no_such_qid") == []


@pytest.mark.parametrize("qid", ["t3_04", "t3_13"])
def test_a_tied_filling_is_wrong_strict_and_correct_tolerant(qid):
    g = GOLD[qid]
    alt = [dict(r) for r in tie_alt_values_syn("employee", qid)[0]]
    assert score(alt, g)[0] != "correct"
    v, _, used = score_tt_syn("employee", alt, g, qid)
    assert v == "correct" and used is True
    v, _, used = score_tt_syn("employee", [dict(r) for r in g["value"]], g, qid)
    assert v == "correct" and used is False                         # gold itself needs no tie


def test_a_wrong_answer_stays_wrong_under_tolerant_scoring():
    g = GOLD["t3_04"]
    wrong = [dict(r) for r in g["value"]]
    wrong[0] = {**wrong[0], "Manager ID": "E9999"}                  # the row above the tie
    assert score_tt_syn("employee", wrong, g, "t3_04")[0] == score(wrong, g)[0] != "correct"
    assert score_tt_syn("employee", None, g, "t3_04", status="refused")[0] == score(None, g, status="refused")[0]


def test_a_non_tie_question_is_identical_strict_and_tolerant():
    g = GOLD["t3_01"]
    for ans in ([dict(r) for r in g["value"]] if isinstance(g["value"], list) else g["value"], "something else", None):
        assert score_tt_syn("employee", ans, g, "t3_01")[0] == score(ans, g)[0]


def test_the_spider_scorer_is_untouched_by_the_synthetic_one():
    """score_tt is keyed by bare qid and reads reports/tie_alternatives.json (Spider only)."""
    g = GOLD["t3_04"]
    alt = [dict(r) for r in tie_alt_values_syn("employee", "t3_04")[0]]
    assert score_tt(alt, g, "t3_04")[0] == score(alt, g)[0] != "correct"
    assert SCORER_VERSION == "s48t"


@pytest.fixture(scope="module")
def csv_rows():
    p = ROOT / "reports" / "paper_numbers.csv"
    if not p.exists():
        pytest.skip("reports/paper_numbers.csv not present")
    rows = {}
    for r in csv.DictReader(open(p, encoding="utf-8")):
        k = (r["fig_table"], r["domain"], r["system"], r["tier_group"], r["scoring"], r["metric"])
        assert k not in rows, k
        rows[k] = r
    return rows


def test_the_csv_has_tolerant_rows_beside_the_strict_ones(csv_rows):
    for s in SYSTEMS:
        for m in FIVE:
            assert ("Table2", "employee", s, "in_contract_answer", "tolerant", m) in csv_rows, (s, m)
            for t in IN_CONTRACT:
                assert ("Table1", "employee", s, t, "tolerant", m) in csv_rows, (s, t, m)
    assert not [k for k in csv_rows if k[4] == "tolerant" and k[1] in ("risk",)]          # risk: no tie, no row
    assert not [k for k in csv_rows if k[4] == "tolerant" and k[0] == "Table1" and k[3] == "out_of_range"]


def test_tolerant_rows_use_the_tolerant_comparator_and_the_same_cells(csv_rows):
    for k, r in csv_rows.items():
        if k[1] == "employee" and k[4] == "tolerant":
            assert r["comparator"] == SCORER_VERSION, k
            strict = csv_rows[k[:4] + ("strict", k[5])]
            assert r["denominator"] == strict["denominator"], k
    for s in SYSTEMS:                                                # a partition of the same n
        for t in IN_CONTRACT:
            n = {int(csv_rows[("Table1", "employee", s, t, "tolerant", m)]["denominator"]) for m in FIVE}
            assert len(n) == 1
            assert sum(int(csv_rows[("Table1", "employee", s, t, "tolerant", m)]["numerator"]) for m in FIVE) == n.pop()


def test_tolerant_differs_from_strict_only_where_a_limit_ties(csv_rows):
    for s in SYSTEMS:
        for t in IN_CONTRACT:
            a = csv_rows[("Table1", "employee", s, t, "strict", "accuracy")]
            b = csv_rows[("Table1", "employee", s, t, "tolerant", "accuracy")]
            if t != "T3_three_ops":
                assert (a["numerator"], a["denominator"]) == (b["numerator"], b["denominator"]), (s, t)
            else:
                assert int(b["numerator"]) >= int(a["numerator"])
        sa = csv_rows[("Table2", "employee", s, "in_contract_answer", "strict", "silent_wrong")]
        sb = csv_rows[("Table2", "employee", s, "in_contract_answer", "tolerant", "silent_wrong")]
        assert int(sb["numerator"]) <= int(sa["numerator"])
    g = csv_rows[("Table2", "employee", "network", "in_contract_answer", "tolerant", "silent_wrong")]
    assert (g["numerator"], g["denominator"]) == ("0", "243")      # GDC has no tied-alternative cell


def test_the_tolerant_gain_equals_the_tie_alternative_cells(csv_rows):
    """The cells that move are exactly the Ties rows' tie_alternative cells (strict-wrong, equal to a tied filling)."""
    for s in SYSTEMS:
        gain = (int(csv_rows[("Table2", "employee", s, "in_contract_answer", "tolerant", "accuracy")]["numerator"])
                - int(csv_rows[("Table2", "employee", s, "in_contract_answer", "strict", "accuracy")]["numerator"]))
        alt = int(csv_rows[("Ties", "employee", s, "all_limit_ties", "strict", "tie_alternative")]["numerator"])
        assert gain == alt, (s, gain, alt)
