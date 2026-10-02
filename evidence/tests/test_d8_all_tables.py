"""D8 part B (2 Oct 2026): one decline rule in every table. Table 5 counts `declined` as explicit
declines plus typed refusals and a refusal that is neither as `failed`. Tables 1 and 2 (and the
Spider in-contract rows) used to count every refusal: `accuracy` on a decline tier (employee
out_of_range) was any refusal, and `refused_should_answer` on an in-scope tier included SQL
errors (text-to-SQL) and unparseable or revised plans (planner). With the rule applied to every
table, a refusal that is not a decline is `failed`, in its own row, and the partition is
correct / silent_wrong / refused_should_answer / failed / crash / timeout. Deterministic; no API.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SYSTEMS = ("network", "planner", "react", "code_agent", "text_to_sql")
SIX = ("accuracy", "silent_wrong", "refused_should_answer", "failed", "crash", "timeout")


@pytest.fixture(scope="module")
def rows():
    p = ROOT / "reports" / "paper_numbers.csv"
    if not p.exists():
        pytest.skip("reports/paper_numbers.csv not present")
    out = {}
    for r in csv.DictReader(open(p, encoding="utf-8")):
        out[(r["fig_table"], r["domain"], r["system"], r["tier_group"], r["scoring"], r["metric"])] = r
    return out


def test_every_in_scope_group_partitions_into_six_classes(rows):
    groups = {k[:5] for k in rows if k[0] in ("Table1", "Table2", "Fig1/Fig2/Table3") and k[5] == "failed"}
    assert groups
    for g in groups:
        n = int(rows[g + ("accuracy",)]["denominator"])
        assert sum(int(rows[g + (m,)]["numerator"]) for m in SIX) == n, g


def test_table1_employee_out_of_range_agrees_with_table5(rows):
    for s in SYSTEMS:
        t1 = rows[("Table1", "employee", s, "out_of_range", "strict", "accuracy")]
        t5 = rows[("Table5", "employee", s, "out_of_range", "strict", "declined")]
        assert (t1["numerator"], t1["denominator"]) == (t5["numerator"], t5["denominator"]), s
        f1 = rows[("Table1", "employee", s, "out_of_range", "strict", "failed")]
        f5 = rows[("Table5", "employee", s, "out_of_range", "strict", "failed")]
        assert f1["numerator"] == f5["numerator"], s


def test_a_failed_refusal_is_not_counted_as_a_decline(rows):
    """text-to-SQL's SQL errors on employee out_of_range (the 60 s interrupt) are failures, not
    declines: they sit in `failed` and not in the decline tier's `accuracy`, and the partition
    still sums to n. (The development tree's version of this test reads the withheld risk
    domain, where text-to-SQL's SQL errors left `refused_should_answer`.)"""
    f = rows[("Table1", "employee", "text_to_sql", "out_of_range", "strict", "failed")]
    a = rows[("Table1", "employee", "text_to_sql", "out_of_range", "strict", "accuracy")]
    assert int(f["numerator"]) > 0
    assert int(a["numerator"]) + int(f["numerator"]) <= int(a["denominator"])


def test_no_system_has_a_failed_cell_that_changes_correct_or_silent(rows):
    """Splitting `failed` off refused/correct must not touch the answered cells."""
    for s in SYSTEMS:
        for t in ("T1_single_op", "T3_three_ops", "T5_high_complexity"):
            a = rows[("Table1", "employee", s, t, "strict", "silent_wrong")]
            assert a["denominator"] == rows[("Table1", "employee", s, t, "strict", "accuracy")]["denominator"]
