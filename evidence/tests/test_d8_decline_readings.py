"""D8 part A (2 Oct 2026): the hand readings of the no-answer cells. Seb: statement-only endings
that say the data cannot answer the question are explicit declines, for every system; SQL errors,
timeouts and unparseable output with no data-absent conclusion stay failures. The regex /
text-presence rule of paper_inputs.rule_channel decides on the words of a reply; the reading of
the final text decides on what it concludes. reports/decline_readings.json holds the readings
(domain|system|qid|seed -> class); paper_inputs.decline_channel applies one before the rule.
Synthetic register for the unit tests; the completeness check reads the recorded results files
and skips when they are absent. No API.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from evidence.eval_run.types import REFUSAL
from scripts import paper_inputs as pi

ROOT = Path(__file__).resolve().parents[2]
READINGS = ROOT / "reports" / "decline_readings.json"
REG = pd.DataFrame({"region": ["n", "s", "n"], "amount": [5.0, 3.0, 2.0], "label": ["a", "b", "c"]})


@pytest.fixture
def dom(monkeypatch):
    monkeypatch.setitem(pi._registers, "synthetic", REG)
    monkeypatch.setattr(pi, "_plan_cache", {})
    monkeypatch.setattr(pi, "_net_cache", {})
    return "synthetic"


def _planner(text, qid="q", seed=0):
    return {"system": "planner", "qid": qid, "seed": seed, "raw_result": "recorded", "status": "completed",
            "artifact": {"kind": "planner_upfront", "raw_text": text}}


REVISED = ("Wait, that approach won't work with these operations. Let me reconsider.\n"
           '```json\n{"plan": [{"op": "select", "input": "register", "columns": ["label"]}]}\n```\n'
           "Here is the revised plan:\n"
           '```json\n{"plan": [{"op": "select", "input": "register", "columns": ["amount"]}]}\n```')


def test_the_readings_file_is_well_formed():
    d = json.loads(READINGS.read_text(encoding="utf-8"))
    assert set(d) == {"_meta", "cells"}
    assert d["cells"]
    for k, v in d["cells"].items():
        assert len(k.split("|")) == 4, k
        assert v["class"] in pi.READING_CLASSES, k
        assert v["reading"] in d["_meta"]["readings"], k
        assert v["class"] == d["_meta"]["readings"][v["reading"]]["class"], k
        if k.startswith("risk|"):
            assert v["excerpt"] == "[risk excerpt withheld: see cell]", k
        else:
            assert 0 < len(v["excerpt"]) <= 260, k           # 200 characters of text, plus an ellipsis


def test_the_loaded_readings_are_the_file():
    d = json.loads(READINGS.read_text(encoding="utf-8"))["cells"]
    assert pi.DECLINE_READINGS == d


def test_a_reading_overrides_the_regex_class_in_both_directions(dom, monkeypatch):
    revised = _planner(REVISED, "q_revised")                       # the regex matches "won't": explicit
    assert pi.rule_channel(dom, revised)[0] == "explicit"
    noword = _planner("The register holds no weather column at all.\n```json\n{}\n```\n```json\n{}\n```", "q_noword")
    assert pi.rule_channel(dom, noword)[0] == "failed"             # no decline word: failed
    monkeypatch.setattr(pi, "DECLINE_READINGS", {
        pi.reading_key(dom, revised): {"class": "failed", "reading": "revised_plan"},
        pi.reading_key(dom, noword): {"class": "explicit", "reading": "absence_statement_no_decline_word"}})
    assert pi.decline_channel(dom, revised) == ("failed", "read: revised_plan")
    assert pi.decline_channel(dom, noword) == ("explicit", "read: absence_statement_no_decline_word")


def test_a_cell_absent_from_the_readings_keeps_its_regex_class(dom, monkeypatch):
    monkeypatch.setattr(pi, "DECLINE_READINGS", {})
    assert pi.decline_channel(dom, _planner(REVISED, "q_revised")) == pi.rule_channel(dom, _planner(REVISED, "q_revised"))
    monkeypatch.setattr(pi, "DECLINE_READINGS", {pi.reading_key(dom, _planner(REVISED, "other")): {"class": "failed", "reading": "x"}})
    assert pi.decline_channel(dom, _planner(REVISED, "q_revised"))[0] == "explicit"     # another qid's reading does not apply
    assert pi.decline_channel(dom, _planner(REVISED, "q_revised", seed=1))[0] == "explicit"   # nor another seed's


def test_the_reading_key_is_domain_system_qid_seed(dom):
    assert pi.reading_key("wta_1", _planner("x", "wta_04", 3)) == "wta_1|planner|wta_04|3"


def test_a_typed_refusal_cannot_be_read(dom, monkeypatch):
    typed = _planner('{"plan": [{"op": "aggregate", "input": "register", "group_by": [], "value_col": "label", "agg": "sum"}]}',
                     "q_typed")
    assert pi.rule_channel(dom, typed)[0] == "typed"
    monkeypatch.setattr(pi, "DECLINE_READINGS", {pi.reading_key(dom, typed): {"class": "failed", "reading": "x"}})
    with pytest.raises(ValueError):
        pi.decline_channel(dom, typed)


def test_an_answer_has_no_class_even_with_a_reading(dom, monkeypatch):
    ans = _planner('{"plan": [{"op": "select", "input": "register", "columns": ["label"]}]}', "q_ans")
    monkeypatch.setattr(pi, "DECLINE_READINGS", {pi.reading_key(dom, ans): {"class": "failed", "reading": "x"}})
    assert pi.decline_channel(dom, ans) is None


def test_load_rejects_a_class_that_is_not_a_reading_class(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"cells": {"d|s|q|0": {"class": "typed", "reading": "x"}}}))
    with pytest.raises(ValueError):
        pi.load_decline_readings(p)
    assert pi.load_decline_readings(tmp_path / "absent.json") == {}


def test_every_text_classed_cell_has_a_reading_and_every_reading_has_a_cell():
    """scripts/check_decline_readings.py reads the cells as the builder does (PARITY_TEMP0=1, the
    temperature-0 overlays), which the test process cannot do after import, so it runs in a child."""
    import os
    import subprocess
    import sys
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_decline_readings.py")], cwd=ROOT,
                       env=env, capture_output=True, text=True)
    if p.returncode == 77:
        pytest.skip(p.stdout.strip())
    assert p.returncode == 0, p.stdout + p.stderr


def test_text_to_sql_sql_errors_are_failures_and_statements_are_declines():
    r = pi.DECLINE_READINGS
    sql = [k for k, v in r.items() if k.split("|")[1] == "text_to_sql" and v["reading"] == "sql_error"]
    assert sql and all(r[k]["class"] == "failed" for k in sql)
    stm = [k for k, v in r.items() if k.split("|")[1] == "text_to_sql" and v["reading"] == "decline_statement"]
    assert stm and all(r[k]["class"] == "explicit" for k in stm)


def test_the_two_truncated_react_replies_keep_their_current_class():
    kept = {k: v for k, v in pi.DECLINE_READINGS.items() if v["reading"] == "truncated_kept_current"}
    assert sorted(kept) == ["bike_1|react|bike_24|0", "bike_1|react|bike_24|3"]
    assert all(v["class"] == "explicit" and v.get("keep_current") for v in kept.values())
