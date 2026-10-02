"""D7 (2026-10-01): a question is in scope iff it is answerable from the data
provided. Spider wta_26 ("winners who played in both 2013 and 2016") is a
single-table INTERSECT over the register, so it is in scope; INTERSECT is outside
GDC's operator vocabulary, so it joins Spider's beyond-vocabulary class
(out_of_contract_aggregate), as wta_15 did (efeac9b), and leaves
out_of_scope_register. Deterministic; no API.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "evaluation" / "domains" / "spider_wta_1" / "eval_corpus_spider_wta_1.json"


def _q():
    c = json.loads(CORPUS.read_text())
    return {q["id"]: q for q in c["queries"]}, c["meta"]["register_table"]


def test_wta26_reference_sql_reads_only_the_register_table():
    qs, table = _q()
    q = qs["wta_26"]
    assert q["tables"] == [table] == ["matches"]
    assert set(re.findall(r"\bFROM\s+(\w+)", q["gold_sql"], re.I)) == {table}
    assert not re.search(r"\bJOIN\b", q["gold_sql"], re.I) and re.search(r"\bINTERSECT\b", q["gold_sql"], re.I)


def test_wta26_is_answerable_from_the_register_and_tiered_beyond_the_vocabulary():
    qs, table = _q()
    q = qs["wta_26"]
    assert (q["tier"], q["expected_behavior"], q["architectural_gap"]) == \
        ("out_of_contract_aggregate", "answer", ["set_operation"])
    g = json.loads((ROOT / "evidence" / "gold" / "gold_spider_wta_1.json").read_text())["wta_26"]
    assert g["tier"] == "out_of_contract_aggregate" and g["expected_behavior"] == "answer"
    con = sqlite3.connect(":memory:")
    pd.read_pickle(ROOT / "evaluation" / "domains" / "spider_wta_1" / "matches.pkl").to_sql(table, con, index=False)
    from_register = pd.read_sql(q["gold_sql"], con)
    assert sorted(r["winner_name"] for r in g["value"]) == sorted(from_register["winner_name"])
    assert g["row_count"] == len(from_register) > 0


def test_wta26_is_out_of_the_register_scope_tier():
    from scripts import paper_inputs as pi
    oosr = sorted(q for q, g in pi.GOLD["wta_1"].items() if g["tier"] == "out_of_scope_register")
    ooca = sorted(q for q, g in pi.GOLD["wta_1"].items() if g["tier"] == "out_of_contract_aggregate")
    assert "wta_26" not in oosr and len(oosr) == 15
    assert "wta_26" in ooca and len(ooca) == 4
