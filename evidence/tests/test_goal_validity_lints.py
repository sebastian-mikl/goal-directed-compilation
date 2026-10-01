"""S35 — goal validity lints (deterministic, 0 extra LLM calls).

Verified from a recorded network artifact: the emission LLM sometimes produces a
well-formed, REACHABLE, but under-specified Goal — e.g. it derives a column intended
for row-membership and then drops the where-predicate that consumes it (a "dead"
derive). The engine faithfully executes the malformed goal -> confident wrong answer.

LINT 1 (dead derive): every derive[] entry must be CONSUMED by a where predicate,
another derive's expr (incl. the aggregate), an order_by column, the group key, or
columns[]. LINT 2 (order closure): every order_by column must be a real output
column — already enforced live by validate_order (S13); the lint-rescore re-applies
both to recorded artifacts. Violations map to the existing refusal outcome BEFORE
planning/execution. Authored purely from Goal semantics — no query ids, no gold
values (asserted below). All tests use SYNTHETIC goals, not eval queries.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]

# The risk domain's source data is withheld from the public release, so the tests
# in this module — which exercise the engine against the withheld domain's register/corpus/gold —
# skip cleanly rather than fail when those files are absent.
_RISK_DATA = [ROOT / "environment" / "register.pkl",
              ROOT / "eval_corpus.json",
              ROOT / "evidence" / "gold" / "gold.json"]
pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in _RISK_DATA),
    reason="risk domain data not included in this release")



def _goal(*, columns, grain="row", where=(), derive=()):
    from gdc.state import Goal
    return Goal.make(columns=list(columns), grain=grain,
                     where=list(where), derive=list(derive))


# ── LINT 1: dead derive ──────────────────────────────────────────────────────

def test_L1_dead_derive_rejected():
    """A derived column consumed by NOTHING -> rejected, naming the column."""
    from gdc.contract import lint_goal
    g = _goal(columns=["alpha"],
              where=[{"col": "alpha", "op": ">", "val": 0}],
              derive=[{"name": "dead_col", "expr": "alpha - beta"}])
    v = lint_goal(g)
    assert not v.ok
    assert "dead_col" in v.reason and "dead" in v.reason.lower()


def test_L1_every_consumer_kind_passes():
    """A derive consumed by each consumer kind passes: where / another derive /
    aggregate / order_by / columns / group key."""
    from gdc.contract import lint_goal
    # consumed by a WHERE predicate
    assert lint_goal(_goal(columns=["alpha"],
                           where=[{"col": "d1", "op": ">", "val": 0}],
                           derive=[{"name": "d1", "expr": "alpha - beta"}])).ok
    # consumed by ANOTHER DERIVE's expr (d2 itself consumed by columns)
    assert lint_goal(_goal(columns=["d2"],
                           derive=[{"name": "d1", "expr": "alpha - beta"},
                                   {"name": "d2", "expr": "d1 + gamma"}])).ok
    # consumed by the AGGREGATE (grouped grain; aggregate consumed by columns)
    assert lint_goal(_goal(columns=["g", "m"], grain={"grouped_by": ["g"]},
                           derive=[{"name": "d1", "expr": "alpha - beta"},
                                   {"name": "m", "expr": "avg(d1)"}])).ok
    # consumed by an ORDER_BY column only
    assert lint_goal(_goal(columns=["alpha"],
                           derive=[{"name": "d1", "expr": "alpha - beta"}]),
                     order_cols=("d1",)).ok
    # consumed by COLUMNS[]
    assert lint_goal(_goal(columns=["d1"],
                           derive=[{"name": "d1", "expr": "alpha - beta"}])).ok


def test_L1_dead_aggregate_also_rejected():
    """The aggregate derive is a derive[] entry too: an aggregate consumed by
    neither columns[] nor order_by is dead."""
    from gdc.contract import lint_goal
    g = _goal(columns=["g"], grain={"grouped_by": ["g"]},
              derive=[{"name": "m", "expr": "avg(alpha)"}])
    v = lint_goal(g)
    assert not v.ok and "m" in v.reason


# ── LINT 2: order closure (validate_order — regression guard + rescore reuse) ─

def test_L2_order_on_absent_column_rejected():
    from gdc.order import OrderSpec, validate_order
    g = _goal(columns=["alpha"])
    spec = OrderSpec.make(order_by=[{"col": "nope", "dir": "desc"}], limit=3)
    v = validate_order(spec, g)
    assert not v.ok and "nope" in v.reason


def test_L2_order_on_derived_or_aggregate_passes():
    from gdc.order import OrderSpec, validate_order
    g = _goal(columns=["g", "m"], grain={"grouped_by": ["g"]},
              derive=[{"name": "m", "expr": "avg(alpha)"}])
    assert validate_order(OrderSpec.make(order_by=[{"col": "m", "dir": "desc"}], limit=3), g).ok


# ── integrity guard: lints authored from Goal semantics only ─────────────────

def test_integrity_lint_code_has_no_eval_content():
    src = (ROOT / "gdc" / "contract.py").read_text()
    assert not re.search(r"\b(t[1-5]_\d{2}|oor_\d{2}|hv_\d{2})\b", src), "query id in contract.py"
    for val in ("4836800", "211846", "45491000"):           # gold values (max/mean/sum Cost)
        assert val not in src, f"gold value {val} baked into contract.py"


def test_contract_version_bumped_and_in_provenance(tmp_path):
    from gdc.contract import CONTRACT_VERSION
    assert CONTRACT_VERSION == "s49"     # s49: declared-aggregate and compute-grammar rules
    from evidence.eval_run.logger import write_provenance
    p = tmp_path / "prov.json"
    write_provenance(p, model="claude-sonnet-4-5", temperature=None, seeds=[0],
                     bounds={"react_max_iters": 4, "code_max_retries": 4},
                     pricing={"claude-sonnet-4-5": {"input": 3.0, "output": 15.0}},
                     corpus_path=ROOT / "eval_corpus.json",
                     gold_path=ROOT / "evidence" / "gold" / "gold.json",
                     prompts={}, contract_version=CONTRACT_VERSION)
    assert json.loads(p.read_text())["contract_version"] == "s49"


# ── live wiring: the network adapter refuses a dead-derive goal, 0 extra calls ─

def _text_block(t):
    return type("T", (), {"type": "text", "text": t})()


class _Client:
    def __init__(self, *responses):
        q = list(responses)

        class _Msgs:
            def create(self, **kw):
                return type("R", (), {"content": q.pop(0), "usage": None})()
        self.messages = _Msgs()


def test_network_adapter_refuses_dead_derive_goal_before_execution():
    from evidence.eval_run.adapters.network import NetworkAdapter
    from evidence.eval_run.briefing import load_briefing
    reg = pd.read_pickle(ROOT / "environment" / "register.pkl")
    brief = load_briefing(ROOT / "environment" / "schema.json",
                          ROOT / "environment" / "register.pkl")
    # well-formed, reachable, but 'dead_gap' is consumed by nothing
    emission = {"goal": {"columns": ["strategy", "m"],
                         "grain": {"grouped_by": ["strategy"]},
                         "where": [],
                         "derive": [{"name": "dead_gap", "expr": "cost - forecast_value"},
                                    {"name": "m", "expr": "avg(cost)"}]},
                "order": {"order_by": [{"col": "m", "dir": "desc"}], "limit": 3}}
    ad = NetworkAdapter(name="network", client=_Client([_text_block(json.dumps(emission))]),
                        register=reg, briefing=brief, model="m")
    rec = ad.run("synthetic", seed=0)
    assert rec["status"] == "refused", f"expected refusal, got {rec['status']}: {rec.get('error')}"
    assert "dead_gap" in (rec["error"] or "")
    assert rec["llm_calls"] == 1                              # zero extra calls
    assert rec["artifact"].get("goal_lint_ok") is False


def test_network_adapter_clean_goal_still_executes():
    """No regression: the same goal WITH the consuming predicate executes."""
    from evidence.eval_run.adapters.network import NetworkAdapter
    from evidence.eval_run.briefing import load_briefing
    reg = pd.read_pickle(ROOT / "environment" / "register.pkl")
    brief = load_briefing(ROOT / "environment" / "schema.json",
                          ROOT / "environment" / "register.pkl")
    emission = {"goal": {"columns": ["strategy", "m"],
                         "grain": {"grouped_by": ["strategy"]},
                         "where": [{"col": "gap", "op": ">", "val": 0}],
                         "derive": [{"name": "gap", "expr": "cost - forecast_value"},
                                    {"name": "m", "expr": "avg(cost)"}]},
                "order": {"order_by": [{"col": "m", "dir": "desc"}], "limit": 3}}
    ad = NetworkAdapter(name="network", client=_Client([_text_block(json.dumps(emission))]),
                        register=reg, briefing=brief, model="m")
    rec = ad.run("synthetic", seed=0)
    assert rec["status"] == "completed", f"{rec['status']}: {rec.get('error')}"
    assert isinstance(rec["result"], list) and len(rec["result"]) == 3


# ── free rescore: re-apply lints to recorded artifact.goal ───────────────────

def test_lint_rescore_reclassifies_silent_wrong_and_reports_correct_hits(tmp_path):
    from evidence.eval_run.run import lint_rescore
    dead_goal = {"columns": [["s", "unknown"], ["m", "unknown"]],
                 "grain": {"grouped_by": ["s"]},
                 "where": [],
                 "derive": [{"name": "dead_d", "expr": "a - b"},
                            {"name": "m", "expr": "avg(a)"}]}
    clean_goal = {"columns": [["s", "unknown"], ["m", "unknown"]],
                  "grain": {"grouped_by": ["s"]},
                  "where": [],
                  "derive": [{"name": "m", "expr": "avg(a)"}]}
    order = {"order_by": [{"col": "m", "dir": "desc"}], "limit": 3}

    def cell(qid, system, outcome, goal=None, value=1.0):
        r = {"qid": qid, "seed": 0, "system": system, "tier": "X",
             "expected_behavior": "answer", "result_kind": "scalar",
             "raw_result": value, "status": "completed", "outcome_class": outcome,
             "gold_value": value, "artifact": {"kind": system}}
        if goal is not None:
            r["artifact"].update({"goal": goal, "order": order})
        return r

    rows = [cell("q1", "network", "silent_wrong", dead_goal, value=2.0),   # -> reclassified
            cell("q2", "network", "correct", clean_goal, value=1.0),       # untouched
            cell("q3", "network", "correct", dead_goal, value=1.0),        # lint-on-correct: REPORTED cost
            cell("q4", "planner", "silent_wrong")]                         # no goal artifact: skipped
    p = tmp_path / "results.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))

    summary = lint_rescore(p)
    assert summary["network"]["lint_violations"] == 2
    assert summary["network"]["silent_wrong_to_refused"] == 1
    assert summary["network"]["correct_hit_by_lint"] == 1                  # honest cost line
    assert "planner" not in summary or summary["planner"]["lint_violations"] == 0
    out = tmp_path / "results.lint_rescored.jsonl"
    assert out.exists()
    new = {r["qid"]: r for r in map(json.loads, out.read_text().splitlines())}
    assert new["q1"]["outcome_class"] == "refused_should_answer"           # honest: NOT correct
    assert new["q1"]["status"] == "refused"
    assert new["q2"]["outcome_class"] == "correct"                         # untouched
    assert new["q3"]["outcome_class"] == "refused_should_answer"           # lint applies uniformly
