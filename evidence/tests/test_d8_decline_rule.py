"""D8 (2026-10-01): one decline rule for every system. A decline is an explicit
decline statement or a typed refusal (GDC's type guards included); SQL errors, the
60 s wall-clock interrupt and unparseable plans with no decline text are failures.
The comparator scores all of them as refusals; paper_inputs.decline_channel says
which each is, from the replay or record the comparator scored, and Table 5 carries
the split (declined = declined_explicit + declined_typed; failed apart).
Synthetic register; no API.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

from evidence.eval_run.types import REFUSAL
from scripts import paper_inputs as pi

ROOT = Path(__file__).resolve().parents[2]
REG = pd.DataFrame({"region": ["n", "s", "n"], "amount": [5.0, 3.0, 2.0], "label": ["a", "b", "c"]})


@pytest.fixture
def dom(monkeypatch):
    monkeypatch.setitem(pi._registers, "synthetic", REG)
    monkeypatch.setattr(pi, "_plan_cache", {})
    monkeypatch.setattr(pi, "_net_cache", {})
    return "synthetic"


def _planner(text):
    return {"system": "planner", "qid": "q", "seed": 0, "raw_result": "recorded", "status": "completed",
            "artifact": {"kind": "planner_upfront", "raw_text": text}}


PLAN = lambda *steps: json.dumps({"plan": list(steps)})          # noqa: E731


@pytest.mark.parametrize("text,channel", [
    (json.dumps({"refuse": "needs a window function"}), "explicit"),
    ("I cannot answer this with the available operations.", "explicit"),       # unparseable, decline text
    ("here is a plan without any JSON", "failed"),                             # unparseable, no decline text
    (PLAN({"op": "aggregate", "input": "register", "group_by": [], "value_col": "label", "agg": "sum"}), "typed"),
    (PLAN(), "failed"),                                                        # empty plan
    (json.dumps({"plan": {"op": "select"}}), "failed"),                        # malformed plan
])
def test_planner_channels(dom, text, channel):
    assert pi.decline_channel(dom, _planner(text))[0] == channel


def test_planner_answer_has_no_channel(dom):
    assert pi.decline_channel(dom, _planner(PLAN({"op": "select", "input": "register", "columns": ["label"]}))) is None


def test_network_channels(dom, monkeypatch):
    monkeypatch.setattr(pi.DomainContext, "get", classmethod(lambda cls, d: d))
    monkeypatch.setattr(pi, "rebuild_goal", lambda g: json.dumps(g))
    monkeypatch.setattr(pi, "rebuild_order", lambda o: json.dumps(o))
    label = {"a": "FilterTypeError: filter 'x' > 'y': value is not numeric", "b": "Unreachable: exhausted"}
    monkeypatch.setattr(pi, "run_deterministic", lambda ctx, g, o: ("refused", REFUSAL, label[json.loads(g)["c"]]))
    row = lambda c, status="completed": {"system": "network", "qid": "q", "seed": 0, "status": status,  # noqa: E731
                                         "raw_result": [{"v": 1}], "artifact": {"goal": {"c": c}, "order": {}}}
    assert pi.decline_channel(dom, row("a", "refused")) == ("typed", "FilterTypeError")   # a type guard
    assert pi.decline_channel(dom, row("b")) == ("typed", "Unreachable")    # a decline that exists only on replay
    declined = {"system": "network", "qid": "q", "seed": 0, "status": "refused", "raw_result": REFUSAL,
                "error": "declined: out of scope", "artifact": {"kind": "network", "declined": True}}
    parse = {"system": "network", "qid": "q", "seed": 0, "status": "refused", "raw_result": REFUSAL,
             "error": "ParseError: bad json", "artifact": {"kind": "network", "error": "bad json"}}
    assert pi.decline_channel(dom, declined)[0] == "explicit"
    assert pi.decline_channel(dom, parse)[0] == "failed"


@pytest.mark.parametrize("row,channel", [
    ({"system": "text_to_sql", "status": "refused", "raw_result": REFUSAL, "error": "no SQL emitted (declined)",
      "artifact": {"raw_text": "The register cannot answer this."}}, ("explicit", "statement, no SQL")),
    ({"system": "text_to_sql", "status": "refused", "raw_result": REFUSAL,
      "error": "sql error: Execution failed on sql 'WITH RECURSIVE r ...': interrupted",
      "artifact": {"raw_text": "", "sql": "WITH RECURSIVE r ..."}}, ("failed", "SQL error (60 s wall clock)")),
    ({"system": "text_to_sql", "status": "refused", "raw_result": REFUSAL,
      "error": "sql error: near \"INTERSECT\": syntax error", "artifact": {"raw_text": ""}}, ("failed", "SQL error")),
    ({"system": "react", "status": "refused", "raw_result": REFUSAL, "artifact": {"last_text": "No such column."}},
     ("explicit", "statement, no answer")),
    ({"system": "code_agent", "status": "refused", "raw_result": REFUSAL, "artifact": {"last_text": ""}},
     ("failed", "no answer, no text")),
    ({"system": "react", "status": "completed", "raw_result": REFUSAL, "artifact": {"last_text": ""}},
     ("failed", "completed with no result")),
    ({"system": "code_agent", "status": "timeout", "raw_result": REFUSAL, "artifact": {}}, None),
])
def test_baseline_channels(dom, row, channel):
    assert pi.decline_channel(dom, dict(row, qid="q", seed=0)) == channel


def test_csv_table5_split_partitions_every_cell():
    p = ROOT / "reports" / "paper_numbers.csv"
    if not p.exists():
        pytest.skip("reports/paper_numbers.csv not present")
    rows = [r for r in csv.DictReader(open(p, encoding="utf-8")) if r["fig_table"] == "Table5"]
    by = {}
    for r in rows:
        by.setdefault((r["domain"], r["system"]), {})[r["metric"]] = (int(r["numerator"]), int(r["denominator"]))
    assert by
    for key, m in by.items():
        n = {d for _, d in m.values()}
        assert len(n) == 1, key
        assert m["declined"][0] == m["declined_explicit"][0] + m["declined_typed"][0], key
        assert sum(m[c][0] for c in ("declined", "failed", "silent_wrong", "crash", "timeout")) == n.pop(), key
