"""D6 (2026-10-01): the synthetic domain stays strict in Tables 1 and 2 (D9 adds tolerant rows
beside them), and the limit ties are disclosed per system. scripts/synthetic_ties.py finds every ranked question whose
limit cuts through a tie from the gold builders (never from an answer) and lists
every other valid filling of the tied slots; the builder counts, per system, the
cells strict scores wrong that equal one (fig_table Ties). Deterministic; no API.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from evidence.eval_run.comparator import score

ROOT = Path(__file__).resolve().parents[2]
TIES = json.loads((ROOT / "reports" / "tie_alternatives_synthetic.json").read_text())


def test_the_committed_tie_file_is_what_the_builders_give():
    from scripts import synthetic_ties as st
    for dom, name in st.BUILDERS.items():
        m = st._builder(name)
        ranked, ties = st.scan(m, pd.read_pickle(m.REGISTER))
        m.FLAGS.clear()
        assert ranked == TIES["scanned"][dom]
        assert sorted(ties) == sorted(TIES["ties"][dom])
    assert sorted(TIES["ties"]["employee"]) == ["t3_04", "t3_13"]   # the builder's two FLAGS
    assert "risk" not in TIES["ties"] and "risk" not in TIES["scanned"]   # the risk domain is withheld


def test_alternatives_are_valid_answers_and_only_differ_in_the_tied_slots():
    gold = json.loads((ROOT / "evidence" / "gold" / "gold_employee.json").read_text())
    for qid, t in TIES["ties"]["employee"].items():
        g = gold[qid]
        assert score([dict(r) for r in g["value"]], g)[0] == "correct"
        by, b = t["by"], t["boundary"]
        fixed = [r for r in g["value"] if r[by] != b]
        assert len(t["alt_values"]) > 0
        for alt in t["alt_values"]:
            assert score([dict(r) for r in alt], g)[0] != "correct"          # strict rejects it
            assert score([dict(r) for r in alt], {**g, "value": alt})[0] == "correct"
            assert [r for r in alt if r[by] != b] == fixed                       # rows above the tie unchanged
            assert all(r[by] == b for r in alt if r not in fixed) and len(alt) == len(g["value"])


def test_a_non_tied_wrong_answer_matches_no_alternative():
    gold = json.loads((ROOT / "evidence" / "gold" / "gold_employee.json").read_text())
    t = TIES["ties"]["employee"]["t3_04"]
    wrong = [dict(r) for r in gold["t3_04"]["value"]]
    wrong[0] = {**wrong[0], "Manager ID": "E9999"}                               # the row above the tie
    assert not any(score(wrong, {**gold["t3_04"], "value": v})[0] == "correct" for v in t["alt_values"])
