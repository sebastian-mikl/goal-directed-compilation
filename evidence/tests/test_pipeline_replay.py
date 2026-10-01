"""The paper pipeline scores GDC and planner on one engine (scripts/paper_inputs.py).

A planner reply is replayed through planner_upfront.execute_reply, the code
PlannerUpfrontAdapter.run runs after its API call, so the replay and the live run
cannot drift. A GDC Goal is replayed through replay_goals.run_deterministic. Both
caches are keyed on the emission, never on the query. react/code_agent/text_to_sql
are scored on their recorded raw_result. Synthetic register; no API call.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from evidence.eval_run.adapters import planner_upfront as pu
from evidence.eval_run.types import REFUSAL, empty_run_result
from scripts import paper_inputs as pi

REG = pd.DataFrame({"region": ["n", "s", "n", "e", "s"], "amount": [5.0, 3.0, 2.0, 7.0, 3.0],
                    "label": ["a", "b", "c", "d", "e"]})


def _plan(*steps, final=None):
    return json.dumps({"plan": list(steps), **({"final_frame": final} if final else {})})


REPLIES = {
    "grouped_sum": _plan({"op": "filter", "input": "register", "predicates": [{"col": "amount", "op": ">", "value": 2}]},
                         {"op": "aggregate", "input": "f0", "group_by": ["region"], "value_col": "amount", "agg": "sum"},
                         final="f1"),
    "order_limit": _plan({"op": "order", "input": "register", "order_by": [{"col": "amount", "dir": "desc"}], "limit": 2}),
    "compute_select": _plan({"op": "compute", "input": "register", "new_col": "twice", "expr": "amount + amount"},
                            {"op": "select", "input": "f0", "columns": ["label", "twice"]}),
    "refuse_envelope": json.dumps({"refuse": "needs a window function"}),
    "unparseable": "here is a plan without any JSON",
    "decline_text": "I cannot answer this with the available operations.",
    "unknown_op": _plan({"op": "pivot", "input": "register"}),
    "typed_refusal": _plan({"op": "aggregate", "input": "register", "group_by": [], "value_col": "label", "agg": "sum"}),
    "empty_plan": _plan(),
    "fenced": "```json\n" + _plan({"op": "select", "input": "register", "columns": ["label"]}) + "\n```",
    "malformed_step": _plan("not a step"),
    "malformed_plan": json.dumps({"plan": {"op": "select"}}),
}


class _Client:
    def __init__(self, text):
        self.text = text
        self.messages = self

    def create(self, **kw):
        block = type("T", (), {"type": "text", "text": self.text})()
        return type("R", (), {"content": [block], "usage": None})()


def _adapter(text):
    return pu.PlannerUpfrontAdapter(name="planner", client=_Client(text), register=REG,
                                    briefing={"columns": list(REG.columns)}, model="m", temperature=0)


@pytest.mark.parametrize("name", sorted(REPLIES))
def test_replay_is_the_adapters_own_post_call_path(name):
    text = REPLIES[name]
    live = _adapter(text).run("q", seed=0)
    rep = pu.execute_reply(text, REG, empty_run_result())
    for k in ("status", "result", "error", "artifact"):
        assert rep[k] == live[k], k
    assert live["llm_calls"] == 1 and rep["llm_calls"] == 0     # only the live run made a call


def test_replies_cover_every_outcome():
    st = {n: pu.execute_reply(t, REG, empty_run_result())["status"] for n, t in REPLIES.items()}
    assert {"completed", "refused", "crashed"} <= set(st.values())
    assert st["typed_refusal"] == "refused" and st["unknown_op"] == "crashed"


@pytest.fixture
def domain(monkeypatch):
    monkeypatch.setitem(pi._registers, "synthetic", REG)
    monkeypatch.setattr(pi, "_plan_cache", {})
    monkeypatch.setattr(pi, "_net_cache", {})
    return "synthetic"


def _planner_row(text, recorded="recorded-value", status="completed"):
    return {"system": "planner", "qid": "q1", "seed": 0, "raw_result": recorded, "status": status,
            "artifact": {"kind": "planner_upfront", "raw_text": text} if text is not None else None}


def test_planner_row_is_scored_on_the_replayed_result(domain):
    text = REPLIES["grouped_sum"]
    res, status = pi.scored_input(domain, _planner_row(text))
    assert status == "completed"
    assert res == pu.execute_reply(text, REG, empty_run_result())["result"] != "recorded-value"


def test_planner_row_without_reply_keeps_its_record(domain):
    assert pi.replay_planner(domain, _planner_row(None, "x", "crashed")) == ("x", "crashed")


def test_planner_replay_is_cached_on_the_reply_text(domain, monkeypatch):
    calls = []
    real = pu.execute_reply
    monkeypatch.setattr(pi, "execute_reply", lambda t, r, rec: calls.append(t) or real(t, r, rec))
    a, b = REPLIES["grouped_sum"], REPLIES["order_limit"]
    for row in (_planner_row(a), dict(_planner_row(a), seed=1), _planner_row(b)):
        pi.replay_planner(domain, row)
    assert calls == [a, b]


def test_a_malformed_plan_is_the_same_typed_refusal_live_and_in_replay(domain):
    """Was: a step that is not an object raised AttributeError out of run() (a crash
    in the harness) and the replay mapped it to a crash; both are now the typed
    refusal execute_reply gives."""
    text = _plan("not a step")
    live = _adapter(text).run("q", seed=0)
    assert live["status"] == "refused" and live["result"] == REFUSAL
    assert live["error"] == "malformed plan: step 0: plan step is str, not an object"
    assert pi.replay_planner(domain, _planner_row(text)) == (REFUSAL, "refused")


def test_planner_exception_is_a_crash_as_run_cell_records_it(domain, monkeypatch):
    """Anything that still escapes execute_reply is scored as harness.run_cell
    records an exception out of run(): a crash with no result."""
    def boom(text, register, rec):
        raise RuntimeError("escaped")
    monkeypatch.setattr(pi, "execute_reply", boom)
    assert pi.replay_planner(domain, _planner_row(REPLIES["grouped_sum"])) == (REFUSAL, "crashed")


def test_network_replay_is_keyed_on_the_goal_not_the_query(domain, monkeypatch):
    seen = []
    monkeypatch.setattr(pi.DomainContext, "get", classmethod(lambda cls, d: d))
    monkeypatch.setattr(pi, "rebuild_goal", lambda g: json.dumps(g))
    monkeypatch.setattr(pi, "rebuild_order", lambda o: json.dumps(o))
    monkeypatch.setattr(pi, "run_deterministic",
                        lambda ctx, g, o: seen.append(g) or ("completed", f"value of {g}", None))
    rows = [{"system": "network", "qid": "q1", "seed": s, "raw_result": "rec", "status": "completed",
             "artifact": {"goal": {"columns": [c]}, "order": {}}} for s, c in ((0, "a"), (1, "b"), (2, "a"))]
    out = [pi.scored_input(domain, r) for r in rows]
    assert [o[0] for o in out] == ['value of {"columns": ["a"]}', 'value of {"columns": ["b"]}',
                                   'value of {"columns": ["a"]}']
    assert len(seen) == 2
    declined = {"system": "network", "qid": "q2", "seed": 0, "raw_result": REFUSAL, "status": "refused",
                "artifact": {"kind": "network", "declined": True}}
    assert pi.scored_input(domain, declined) == (REFUSAL, "refused")


@pytest.mark.parametrize("system", ["react", "code_agent", "text_to_sql"])
def test_other_systems_are_scored_on_the_record(domain, system):
    row = {"system": system, "qid": "q1", "seed": 0, "raw_result": [{"x": 1}], "status": "timeout",
           "artifact": {"raw_text": REPLIES["grouped_sum"], "goal": {"columns": ["x"]}}}
    assert pi.scored_input(domain, row) == ([{"x": 1}], "timeout")


def test_spider_gdc_cells_are_the_phase_r_runs():
    assert pi.spider_file("wta_1", "network") == "results_spider_wta_1_postfix_s40off.jsonl"
    assert pi.spider_file("wta_1", "network", "on") == "results_spider_wta_1_postfix_s40on.jsonl"
    assert pi.spider_file("wta_1", "planner") == "results_spider_wta_1.jsonl"
    cells = pi.network_cells("wta_1")
    files = {fn for fn, _ in cells}
    if not files:
        pytest.skip("Spider results files not present")
    assert files == {"results_spider_wta_1_postfix_s40off.jsonl", "results_spider_wta_1_postfix_s40on.jsonl"}
    assert all(r["system"] == "network" for _, r in cells)
    on = [r for fn, r in cells if fn.endswith("s40on.jsonl")]
    assert on and {r["tier"] for r in on} == {"out_of_scope_register"}
