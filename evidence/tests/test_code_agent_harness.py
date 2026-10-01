"""Code-agent harness: three flaws fixed, each pinned here.

(a) A DataFrame's labelled index (value_counts(), groupby().agg(), set_index())
    was dropped by df_to_result and hidden from the run_python summary, so the
    group key never reached the comparator. It is now lifted into a column; a
    positional index is still dropped.
(b) Every run_python call got a fresh namespace, so a variable from one call was
    a NameError in the next. One namespace now serves the whole episode,
    final_answer included; `result` persists, and the summary says when the
    current call did not assign it.
(c) The run_python description said "the 300-row register" in every domain. It
    now states the domain's own row count, and the same bytes for every cell of
    a domain (the tools sit in the cached prefix).
Also: the trace keeps the full code (it was cut at 600 characters) and each
summary as the model saw it, for code_agent and react.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pytest

from evidence.eval_run.adapters.base import df_to_result
from evidence.eval_run.adapters.code_agent import CodeAgentAdapter
from evidence.eval_run.adapters.react import ReactAdapter
from evidence.eval_run.briefing import load_briefing
from evidence.eval_run.tools_code import (
    _binds_result, code_tools, index_to_columns, new_namespace, run_code)
from evidence.eval_run.types import REFUSAL

ROOT = Path(__file__).resolve().parents[2]
DOMAINS_DIR = ROOT / "evaluation" / "domains"
EMPLOYEE = (DOMAINS_DIR / "employee" / "schema.json", DOMAINS_DIR / "employee" / "employees.pkl")
DOMAINS = {
    "employee": EMPLOYEE,
    "wta_1": (DOMAINS_DIR / "spider_wta_1" / "schema.json", DOMAINS_DIR / "spider_wta_1" / "matches.pkl"),
    "world_1": (DOMAINS_DIR / "spider_world_1" / "schema.json", DOMAINS_DIR / "spider_world_1" / "country.pkl"),
    "bike_1": (DOMAINS_DIR / "spider_bike_1" / "schema.json", DOMAINS_DIR / "spider_bike_1" / "trip.pkl"),
}


# ── fake Anthropic-shaped client: canned responses, every request recorded ──

@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class _Tool:
    name: str
    input: dict
    id: str = "tu"
    type: str = "tool_use"


@dataclass
class _Resp:
    content: list
    usage: _Usage = field(default_factory=_Usage)


class _Client:
    def __init__(self, plan):
        self.plan, self.calls = list(plan), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return _Resp(content=self.plan[len(self.calls) - 1])


def _adapter(cls, plan, domain=EMPLOYEE, max_retries=3):
    schema, reg = domain
    client = _Client(plan)
    ad = cls(name=cls.__name__, client=client, register=pd.read_pickle(reg),
             briefing=load_briefing(schema, reg), model="claude-sonnet-4-5",
             max_iters=5, max_retries=max_retries)
    return ad, client


def _tool_results(client) -> list[str]:
    """Every tool_result content the adapter sent back, in order."""
    msgs = client.calls[-1]["messages"]
    return [b["content"] for m in msgs if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"]]


# ── (a) labelled index ──────────────────────────────────────────────────────

def test_value_counts_frame_keeps_its_key():
    s = pd.Series(["a", "b", "a", "c", "a", "b"], name="team")
    vc = s.value_counts()
    f = pd.DataFrame({"n": vc, "share": vc / vc.sum()})
    assert df_to_result(f)[0] == {"team": "a", "n": 3, "share": 0.5}


def test_groupby_agg_frame_keeps_its_key():
    df = pd.DataFrame({"team": ["x", "x", "y"], "hours": [1.0, 3.0, 5.0]})
    g = df.groupby("team").agg(avg=("hours", "mean"), n=("hours", "size"))
    assert df_to_result(g) == [{"team": "x", "avg": 2.0, "n": 2},
                               {"team": "y", "avg": 5.0, "n": 1}]


def test_multiindex_frame_keeps_every_level():
    df = pd.DataFrame({"a": ["p", "p", "q"], "b": [1, 2, 1], "v": [1.0, 2.0, 3.0]})
    assert df_to_result(df.groupby(["a", "b"])[["v"]].sum())[0] == {"a": "p", "b": 1, "v": 1.0}


def test_index_named_like_a_column_is_not_duplicated():
    f = pd.DataFrame({"team": ["x", "y"], "c": [1, 2]},
                     index=pd.Index(["x", "y"], name="team"))
    assert df_to_result(f) == [{"team": "x", "c": 1}, {"team": "y", "c": 2}]


def test_positional_index_is_still_dropped():
    df = pd.DataFrame({"name": ["p", "q", "r"], "hours": [5, 1, 9]})
    f = df[df["hours"] > 2].sort_values("hours", ascending=False)
    assert df_to_result(f) == [{"name": "r", "hours": 9}, {"name": "p", "hours": 5}]


def test_one_row_frame_with_named_index_stays_a_table():
    f = pd.DataFrame({"metric": [42.0]}, index=pd.Index(["x"], name="team"))
    assert df_to_result(f) == [{"team": "x", "metric": 42.0}]


def test_index_lift_that_would_clash_leaves_the_frame_as_it_was():
    # an unnamed level lands on 'index', else 'level_0': both taken -> ValueError
    f = pd.DataFrame({"index": [1, 2], "level_0": [3, 4]}, index=["x", "y"])
    assert index_to_columns(f) is f
    assert df_to_result(f) == [{"index": 1, "level_0": 3}, {"index": 2, "level_0": 4}]


def test_run_code_summary_shows_the_index_key():
    ns = new_namespace(pd.DataFrame({"team": ["a", "b", "a"]}))
    summary, val = run_code("vc = df['team'].value_counts()\n"
                            "result = pd.DataFrame({'n': vc})", ns)
    assert "'team'" in summary and "'a'" in summary
    assert isinstance(val, pd.DataFrame)          # the raw object still goes to the adapter


# ── (b) one namespace per episode; what the summary says about `result` ─────

def _ns():
    return new_namespace(pd.DataFrame({"team": ["x", "x", "y"], "hours": [1, 3, 5]}))


def test_variables_and_result_persist_across_calls():
    ns = _ns()
    run_code("g = df.groupby('team')['hours'].sum()\nresult = g", ns)
    summary, val = run_code("result = int(g.max() + result.min())", ns)
    assert summary == "result = 9" and val == 9


def test_call_that_does_not_assign_result_is_flagged():
    ns = _ns()
    run_code("result = int(df['hours'].sum())", ns)
    summary, val = run_code("tmp = df.shape", ns)
    assert summary.startswith("ran OK but this call did not assign `result`")
    assert summary.endswith("result = 9") and val == 9


def test_in_place_change_is_flagged_and_shows_the_current_value():
    ns = _ns()
    run_code("result = df[['team']].copy()", ns)
    summary, val = run_code("result.drop(index=[0], inplace=True)", ns)
    assert "did not assign `result`" in summary
    assert "2 rows x 1 cols" in summary and len(val) == 2


@pytest.mark.parametrize("code", ["result = result", "result = len(df)"])
def test_rebinding_result_to_the_same_object_is_not_flagged(code):
    ns = _ns()
    run_code("result = len(df)", ns)                 # 3: a cached small int
    summary, _ = run_code(code, ns)
    assert summary == "result = 3"


@pytest.mark.parametrize("code, binds", [
    ("result = 1", True),
    ("result += 1", True),
    ("x = (result := 2)", True),
    ("for result in range(2): pass", True),
    ("with open('f') as result: pass", True),
    ("del result", True),
    ("def f():\n    global result\n    result = 1\nf()", True),
    ("x = result", False),
    ("result.append(1)", False),
    ("result['k'] = 1", False),
    ("def f():\n    result = 1\n    return result\nx = f()", False),
    ("class C:\n    result = 1", False),
])
def test_binds_result(code, binds):
    import ast
    assert _binds_result(ast.parse(code)) is binds


def test_failed_call_returns_no_object_even_with_result_held():
    ns = _ns()
    run_code("result = 1.5", ns)
    summary, val = run_code("result = df['missing']", ns)
    assert summary.startswith("error: KeyError") and val is None
    assert ns["result"] == 1.5                       # the earlier value is still there


def test_result_never_set_and_syntax_error_messages_are_unchanged():
    assert run_code("x = 1", _ns()) == ("ran OK but `result` was not set.", None)
    summary, val = run_code("result = (", _ns())
    assert summary.startswith("error: SyntaxError:") and val is None


def test_episode_df_is_a_copy_of_the_register():
    reg = pd.DataFrame({"hours": [1, 2]})
    ns = new_namespace(reg)
    run_code("df['hours'] = 0\ndf.drop(index=[0], inplace=True)", ns)
    assert reg["hours"].tolist() == [1, 2] and len(ns["df"]) == 1


def test_adapter_final_answer_reuses_earlier_variables():
    ad, client = _adapter(CodeAgentAdapter, [
        [_Tool("run_python", {"code": "eng = df[df['Department'] == 'Engineering']\n"
                                       "result = len(eng)"}, id="t1")],
        [_Tool("final_answer", {"code": "result = float(eng['Annual Salary'].max())"}, id="t2")],
    ])
    r = ad.run("q", seed=0)
    reg = ad.register
    assert r["status"] == "completed", r["error"]
    assert r["result"] == float(reg.loc[reg["Department"] == "Engineering", "Annual Salary"].max())
    assert not any(s.startswith("error") for s in _tool_results(client))


def test_adapter_final_answer_without_assignment_returns_the_held_result():
    ad, _ = _adapter(CodeAgentAdapter, [
        [_Tool("run_python", {"code": "result = df['Department'].nunique()"}, id="t1")],
        [_Tool("final_answer", {"code": "# verified above"}, id="t2")],
    ])
    r = ad.run("q", seed=0)
    assert r["status"] == "completed"
    assert r["result"] == float(ad.register["Department"].nunique())
    assert "did not assign `result`" in r["artifact"]["trace"][-1]["summary"]


def test_adapter_failed_final_answer_is_still_a_refusal():
    ad, _ = _adapter(CodeAgentAdapter, [
        [_Tool("run_python", {"code": "result = 1"}, id="t1")],
        [_Tool("final_answer", {"code": "result = df['missing']"}, id="t2")],
    ])
    r = ad.run("q", seed=0)
    assert r["status"] == "refused" and r["result"] == REFUSAL


def test_adapter_episodes_do_not_share_a_namespace():
    ad, client = _adapter(CodeAgentAdapter, [
        [_Tool("run_python", {"code": "x = 1\ndf.drop(df.index, inplace=True)\n"
                                       "result = len(df)"}, id="t1")],
        [_Tool("final_answer", {"code": "result = x"}, id="t2")],
        [_Tool("final_answer", {"code": "result = x"}, id="t3")],
        [_Tool("final_answer", {"code": "result = len(df)"}, id="t4")],
    ])
    n = len(ad.register)
    assert ad.run("q", seed=0)["result"] == 1.0
    assert ad.run("q", seed=1)["status"] == "refused"           # x is not defined
    assert _tool_results(client)[-1].startswith("error: NameError")
    assert ad.run("q", seed=2)["result"] == float(n)            # fresh df, register untouched
    assert len(ad.register) == n


def test_adapter_answer_is_fixed_when_final_answer_runs():
    ad, _ = _adapter(CodeAgentAdapter, [[
        _Tool("final_answer", {"code": "result = df[['Name']].head(2)"}, id="t1"),
        _Tool("run_python", {"code": "result.drop(result.index, inplace=True)"}, id="t2"),
    ]])
    r = ad.run("q", seed=0)
    assert r["result"] == ad.register[["Name"]].head(2).to_dict("records")


# ── (c) tool description per domain ─────────────────────────────────────────

@pytest.mark.parametrize("domain", sorted(DOMAINS))
def test_tool_description_states_the_domains_row_count(domain):
    plan = [[_Tool("final_answer", {"code": "result = len(df)"})]] * 2
    ad, client = _adapter(CodeAgentAdapter, plan, domain=DOMAINS[domain])
    n = len(ad.register)
    desc = ad.cache_tools()[0]["description"]
    assert f"`df` is the {n}-row register" in desc
    assert n == 300 or "300-row" not in desc
    ad.run("q", seed=0)
    ad.run("q", seed=1)
    sent = [json.dumps(c["tools"]) for c in client.calls]
    assert sent == [json.dumps(code_tools(n))] * 2              # same bytes every cell


def test_tool_descriptions_differ_only_in_the_row_count():
    a, b = code_tools(150), code_tools(9959)
    assert json.dumps(a).replace("150-row", "9959-row") == json.dumps(b)


# ── full code and summaries in the trace ────────────────────────────────────

def test_code_agent_trace_keeps_the_full_code_and_the_summary_as_sent():
    long_code = "\n".join(f"v{i} = {i}" for i in range(200)) + "\nresult = df[['Name', 'Role', 'Department']]"
    assert len(long_code) > 600
    ad, client = _adapter(CodeAgentAdapter, [
        [_Tool("run_python", {"code": long_code}, id="t1")],
        [_Tool("final_answer", {"code": "result = v199"}, id="t2")],
    ])
    r = ad.run("q", seed=0)
    trace = r["artifact"]["trace"]
    assert trace[0]["code"] == long_code
    assert [t["summary"] for t in trace] == _tool_results(client)
    assert len(trace[0]["summary"]) > 200 and r["result"] == 199.0


def test_react_trace_keeps_the_summary_as_sent():
    ad, client = _adapter(ReactAdapter, [
        [_Tool("select", {"input": "register",
                          "columns": list(pd.read_pickle(EMPLOYEE[1]).columns)}, id="t1")],
        [_Tool("final_answer", {"frame": "f0"}, id="t2")],
    ])
    r = ad.run("q", seed=0)
    step = r["artifact"]["trace"][0]
    assert len(step["summary"]) > 200
    assert step["summary"] == _tool_results(client)[0]
