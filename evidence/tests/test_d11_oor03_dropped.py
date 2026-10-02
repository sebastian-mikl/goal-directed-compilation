"""D11 (2026-10-01): employee oor_03 ("Compare our salaries to industry
benchmarks.") is dropped from scoring. It has a defensible answering reading:
schema.json describes Market Rate as the "External market benchmark salary for
the employee's role", so a refusal is not the only correct behaviour. It leaves
the corpus and gold, as S26's hv_01/hv_04 did; its recorded cells stay in the
results files and no reported set reads them. Deterministic; no API.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "evaluation" / "domains" / "employee" / "eval_corpus_employee.json"


def test_oor03_is_out_of_the_employee_corpus_and_gold():
    c = {q["id"]: q for q in json.loads(CORPUS.read_text())["queries"]}
    g = json.loads((ROOT / "evidence" / "gold" / "gold_employee.json").read_text())
    assert "oor_03" not in c and "oor_03" not in g and set(c) == set(g)
    oor = sorted(q for q, r in c.items() if r["tier"] == "out_of_range")
    assert oor == [f"oor_{i:02d}" for i in (1, 2, 4, 5, 6, 7, 8, 9, 10)]
    assert all(g[q]["expected_behavior"] == "refuse" for q in oor)


def test_scored_employee_rows_exclude_oor03():
    from scripts import paper_inputs as pi
    rows = [r for r in pi.employee() if r["qid"] == "oor_03"]
    assert rows, "the recorded oor_03 cells stay in results_employee.jsonl"
    assert pi.scored(rows, pi.GOLD["employee"]) == []
