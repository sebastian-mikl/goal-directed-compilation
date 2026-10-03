"""D8 (2026-10-01): one decline rule for every system. A decline is an explicit
decline statement or a typed refusal (GDC's type guards included); SQL errors, the
60 s wall-clock interrupt and unparseable plans with no decline text are failures.
The comparator scores all of them as refusals; paper_inputs.decline_channel says
which each is, from the replay or record the comparator scored, and Table 5 carries
the split (declined = declined_explicit + declined_typed; failed apart).
The schema line (decision of 3 Oct 2026): a GDC ParseError on a reply that parsed as JSON
(the Goal or OrderSpec schema check) is a typed refusal; one on a reply that is not
valid JSON is a failure.
Synthetic register; no API.
"""
from __future__ import annotations

import ast
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
    parse = lambda msg: {"system": "network", "qid": "q", "seed": 0, "status": "refused",  # noqa: E731
                         "raw_result": REFUSAL, "error": f"ParseError: {msg}",
                         "artifact": {"kind": "network", "error": msg}}
    assert pi.decline_channel(dom, declined)[0] == "explicit"
    assert pi.decline_channel(dom, parse("LLM1 output was not valid JSON: Expecting ',' delimiter: "
                                         "line 1 column 30 (char 29)")) == ("failed", "ParseError")
    assert pi.decline_channel(dom, parse("missing required field: 'derive'")) == ("typed", "ParseError")


# ── the schema line: a ParseError on a reply that parsed is typed, on one that did not, failed ──
def _engine():
    """The engine's llm and contract modules (gdc/ here; planner/ in a tree from before the rename)."""
    try:
        from gdc import contract, llm
        if hasattr(llm, "llm1"):
            return llm, contract
    except ImportError:
        pass
    from planner import contract, llm    # noqa: F811
    return llm, contract


_GOAL = {"columns": ["a"], "grain": "row", "where": [], "derive": []}
SCHEMA_LINE_REPLIES = [
    "There is no JSON object in this reply.",                               # no JSON object
    '{"goal": {"columns": ["a"], "grain": "row"',                            # unbalanced
    '{"goal": {"columns": ["a"],}}',                                         # json.loads rejects it
    "{'goal': {'columns': ['a']}}",                                          # single quotes
    json.dumps({"order": {}}),                                               # no 'goal'
    json.dumps({"goal": "columns a"}),                                       # goal a string that does not parse
    json.dumps({"goal": ["a"]}),                                             # goal not an object
    json.dumps({"goal": {k: v for k, v in _GOAL.items() if k != "derive"}}),  # missing required field
    json.dumps({"goal": {k: v for k, v in _GOAL.items() if k != "where"}}),
    json.dumps({"goal": dict(_GOAL, columns="a")}),
    json.dumps({"goal": dict(_GOAL, where={})}),
    json.dumps({"goal": dict(_GOAL, derive={})}),
    json.dumps({"goal": dict(_GOAL, where=[{"col": "a"}])}),
    json.dumps({"goal": dict(_GOAL, derive=[{"name": "x"}])}),
    json.dumps({"goal": dict(_GOAL, grain=5)}),                              # could not build Goal
    json.dumps({"goal": _GOAL, "order": []}),                                # the OrderSpec schema
    json.dumps({"goal": _GOAL, "order": {"order_by": "a"}}),
    json.dumps({"goal": _GOAL, "order": {"order_by": [{"dir": "asc"}]}}),
    json.dumps({"goal": _GOAL, "order": {"limit": "3"}}),
]


@pytest.mark.parametrize("reply", SCHEMA_LINE_REPLIES)
def test_schema_line_follows_the_json_parse(dom, reply):
    """The engine's own llm1 on the reply raises a ParseError; recorded as NetworkAdapter.run
    records it, the cell is typed if the reply parses as JSON and failed if it does not."""
    llm, contract = _engine()
    resp = type("R", (), {"content": [type("T", (), {"type": "text", "text": reply})()], "usage": None})()
    client = type("C", (), {})()
    client.messages = type("M", (), {"create": lambda self, **kw: resp})()
    with pytest.raises(contract.ParseError) as ei:
        llm.llm1("q", client=client, base_schema={"a": "str"})
    assert not isinstance(ei.value, llm.EmissionDecline)
    try:
        json.loads(reply)
        parsed = True
    except ValueError:
        parsed = False
    row = {"system": "network", "qid": "q", "seed": 0, "status": "refused", "raw_result": REFUSAL,
           "error": f"ParseError: {ei.value}", "artifact": {"kind": "network", "error": str(ei.value)}}
    assert pi.decline_channel(dom, row)[0] == ("typed" if parsed else "failed")


def test_every_engine_parse_error_message_is_classed():
    """Every `raise ParseError(...)` in the engine's llm.py and contract.py has a message the
    schema line classes; a message it does not know raises instead of falling into a class."""
    llm, contract = _engine()
    heads = []
    for mod in (llm, contract):
        for node in ast.walk(ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                    and getattr(node.exc.func, "id", None) == "ParseError" and node.exc.args):
                a = node.exc.args[0]
                if isinstance(a, ast.Constant):
                    heads.append(a.value)
                else:
                    head = ""
                    for v in a.values:
                        if not isinstance(v, ast.Constant):
                            break
                        head += v.value
                    heads.append(head)
    assert len(heads) == 18, heads
    assert sorted(pi.gdc_parse_channel("ParseError: " + h + "x") for h in heads).count("failed") == 3
    with pytest.raises(ValueError):
        pi.gdc_parse_channel("ParseError: bad json")


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
