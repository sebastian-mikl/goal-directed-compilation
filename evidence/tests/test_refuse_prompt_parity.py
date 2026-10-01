"""Refusal-prompt parity across all 5 systems (deterministic; no API).

Two related properties, both about keeping refusal behavior fair and
architecturally clean across systems rather than accidentally coached by
eval-specific knowledge:

  1. ELIGIBILITY PARITY (planner/react vs network): planner/react used to carry
     an eval-adjacent ENUMERATION of ill-posed operations (median/correlation/
     window/running-total/second-highest/row-vs-aggregate/ratios/…) that the
     network system never had. Removed; only the decline channel and the
     generic refusal remain by default. With the REFUSE_UNSUPPORTED toggle ON,
     all three primitive systems carry the identical general block.

  2. BASELINE PARITY (code_agent/text_to_sql): the same eval-adjacent list sat
     in the pandas and SQL baselines too — which CAN perform those operations,
     so the list was authored from the eval, not from a real capability gap.
     Removed from every prompt; decline channels + generic refusal kept.

  3. GUIDANCE-TOGGLE PARITY (planner/react vs network, OFF state): after (1),
     planner/react still had an always-on generic nudge ("if the query cannot
     be expressed/answered, refuse / cannot answer") that the network system
     lacked when the toggle was OFF. OFF is now a clean architectural
     baseline: no refusal guidance anywhere; the decline CHANNEL (the
     {refuse} envelope / plain-text mechanism — native plumbing, not prompt
     text) is kept regardless. ON: all three carry the identical instruction.

Across all three: removing prompt GUIDANCE never removes the decline CHANNEL,
and in-range execution is unaffected — these are prompt-text changes only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]

# Risk-domain source data is withheld from the public release; the tests that
# need it skip cleanly rather than fail when it is absent. (Baseline-parity
# tests below need no register/briefing and are unaffected.)
_RISK_DATA = [ROOT / "environment" / "register.pkl",
              ROOT / "eval_corpus.json",
              ROOT / "evidence" / "gold" / "gold.json"]
_HAS_RISK = all(p.exists() for p in _RISK_DATA)
_skip_no_risk = pytest.mark.skipif(not _HAS_RISK,
    reason="risk domain data not included in this release")


def _register():
    return pd.read_pickle(ROOT / "environment" / "register.pkl")


def _briefing():
    from evidence.eval_run.briefing import load_briefing
    return load_briefing(ROOT / "environment" / "schema.json", ROOT / "environment" / "register.pkl")


def _text_block(t):
    return type("T", (), {"type": "text", "text": t})()


class _Client:
    def __init__(self, *responses):
        q = list(responses)

        class _Msgs:
            def create(self, **kw):
                return type("R", (), {"content": q.pop(0) if q else [_text_block("I cannot answer this.")],
                                      "usage": None})()
        self.messages = _Msgs()


def _planner(client=None):
    from evidence.eval_run.adapters.planner_upfront import PlannerUpfrontAdapter
    return PlannerUpfrontAdapter(name="planner", client=client or _Client(), register=_register(),
                                 briefing=_briefing(), model="m")


def _react(client=None):
    from evidence.eval_run.adapters.react import ReactAdapter
    return ReactAdapter(name="react", client=client or _Client(), register=_register(),
                        briefing=_briefing(), model="m", max_iters=3)


# ── 1. Eligibility parity — planner/react vs network ─────────────────────────

# eval-adjacent op terms that must NOT appear in a parity-clean default prompt
OOR_OP_TERMS = ["median", "correlation", "window function", "running total",
                "second-highest", "row-vs-aggregate", "nested aggregate",
                "ratios across", "percentile", "cumulative", "join across rows"]


@_skip_no_risk
def test_P2_planner_default_prompt_has_no_oor_op_enumeration(monkeypatch):
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    t = _planner().system_text.lower()
    present = [w for w in OOR_OP_TERMS if w in t]
    assert not present, f"planner default prompt still enumerates oor ops: {present}"


@_skip_no_risk
def test_P2_react_default_prompt_has_no_oor_op_enumeration(monkeypatch):
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    t = _react().system_text.lower()
    present = [w for w in OOR_OP_TERMS if w in t]
    assert not present, f"react default prompt still enumerates oor ops: {present}"


@_skip_no_risk
def test_P2_decline_channels_kept(monkeypatch):
    """Removing the enumeration KEEPS the decline CHANNEL (code plumbing): a {refuse}
    envelope (planner) and a plain-text decline (react) still map to refused. (The
    guidance-toggle change below moves residual prompt guidance behind a toggle;
    this channel test is unaffected because the channel is parsing code, not
    prompt text.)"""
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    pl = _planner(_Client([_text_block(json.dumps({"refuse": "x"}))]))
    assert pl.run("q", seed=0)["status"] == "refused"
    re = _react(_Client([_text_block("I cannot answer this.")]))
    assert re.run("q", seed=0)["status"] == "refused"


@_skip_no_risk
def test_P2_toggle_ON_identical_S28_block_in_all_three_primitives(monkeypatch):
    monkeypatch.setenv("REFUSE_UNSUPPORTED", "1")
    from evidence.eval_run.refuse_config import REFUSE_INSTRUCTION
    from evidence.eval_run.adapters.network import NetworkAdapter
    nw = NetworkAdapter(name="network", client=_Client([_text_block("{}")]), register=_register(),
                        briefing=_briefing(), model="m")
    assert REFUSE_INSTRUCTION in _planner().system_text
    assert REFUSE_INSTRUCTION in _react().system_text
    assert REFUSE_INSTRUCTION in nw.cache_prefix_text()       # identical general block, all three


@_skip_no_risk
def test_P2_planner_in_range_execution_unchanged(monkeypatch):
    """No in-range regression: a normal filter plan still executes, and the 5-op
    capability description is intact (we removed only the enumeration)."""
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    plan = {"plan": [{"op": "filter", "input": "register",
                      "predicates": [{"col": "Cost", "op": ">", "value": 1000000}]}],
            "final_frame": "f0"}
    r = _planner(_Client([_text_block(json.dumps(plan))])).run("Cost over 1,000,000", seed=0)
    assert r["status"] == "completed", f"{r['status']}: {r.get('error')}"
    for op in ("filter", "select", "compute", "aggregate", "order"):
        assert op in _planner().system_text.lower()


# ── 2. Baseline parity — code_agent / text_to_sql ────────────────────────────

# Same list as above; the pandas/SQL baselines can actually perform these ops,
# so enumerating them was an eval-authored coaching artifact, not a capability
# boundary.
_BASELINE_OOR_OP_TERMS = ["window function", "nested aggregate", "row-vs-aggregate",
                          "median", "correlation", "second-highest", "running total",
                          "ratios across", "percentile", "cumulative"]


def test_PA_code_agent_prompt_has_no_oor_op_enumeration():
    from evidence.eval_run.adapters.code_agent import CODE_SYSTEM
    t = CODE_SYSTEM.lower()
    present = [w for w in _BASELINE_OOR_OP_TERMS if w in t]
    assert not present, f"code_agent prompt still enumerates oor ops: {present}"


def test_PA_text_to_sql_prompt_has_no_oor_op_enumeration():
    from evidence.eval_run.adapters.text_to_sql import SQL_SYSTEM
    t = SQL_SYSTEM.lower()
    present = [w for w in _BASELINE_OOR_OP_TERMS if w in t]
    assert not present, f"text_to_sql prompt still enumerates oor ops: {present}"


def test_PA_generic_refusal_mechanism_kept():
    """Keep the decline mechanism: both still tell the model it may reply in plain text
    that it cannot, instead of fabricating an answer."""
    from evidence.eval_run.adapters.code_agent import CODE_SYSTEM
    from evidence.eval_run.adapters.text_to_sql import SQL_SYSTEM
    assert "cannot" in CODE_SYSTEM.lower()
    assert "cannot" in SQL_SYSTEM.lower()


# ── 3. Guidance-toggle parity — planner/react OFF state vs network ───────────

# any when-to-refuse GUIDANCE phrasing that must be absent from the OFF prompts
GUIDANCE_PHRASES = ["ill-posed", "refuse", "cannot answer", "cannot be expressed",
                    "cannot be answered", "decline"]


@_skip_no_risk
def test_PB_planner_OFF_has_no_when_to_refuse_guidance(monkeypatch):
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    t = _planner().system_text.lower()
    present = [w for w in GUIDANCE_PHRASES if w in t]
    assert not present, f"planner OFF still carries refusal guidance: {present}"


@_skip_no_risk
def test_PB_react_OFF_has_no_when_to_refuse_guidance(monkeypatch):
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    t = _react().system_text.lower()
    present = [w for w in GUIDANCE_PHRASES if w in t]
    assert not present, f"react OFF still carries refusal guidance: {present}"


@_skip_no_risk
def test_PB_ON_parity_identical_instruction_with_network(monkeypatch):
    monkeypatch.setenv("REFUSE_UNSUPPORTED", "1")
    from evidence.eval_run.refuse_config import REFUSE_INSTRUCTION
    from evidence.eval_run.adapters.network import NetworkAdapter
    nw = NetworkAdapter(name="network", client=_Client([_text_block("{}")]), register=_register(),
                        briefing=_briefing(), model="m")
    assert REFUSE_INSTRUCTION in _planner().system_text
    assert REFUSE_INSTRUCTION in _react().system_text
    assert REFUSE_INSTRUCTION in nw.cache_prefix_text()        # identical block, all three


@_skip_no_risk
def test_PB_decline_channels_untouched(monkeypatch):
    """The decline CHANNEL (plumbing) is unchanged: a {refuse} envelope (planner) and a
    plain-text decline (react) still map to refused, regardless of prompt guidance."""
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    pl = _planner(_Client([_text_block(json.dumps({"refuse": "x"}))]))
    assert pl.run("q", seed=0)["status"] == "refused"
    re = _react(_Client([_text_block("I cannot answer this.")]))
    assert re.run("q", seed=0)["status"] == "refused"


@_skip_no_risk
def test_PB_planner_in_range_execution_unchanged(monkeypatch):
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    plan = {"plan": [{"op": "filter", "input": "register",
                      "predicates": [{"col": "Cost", "op": ">", "value": 1000000}]}],
            "final_frame": "f0"}
    r = _planner(_Client([_text_block(json.dumps(plan))])).run("Cost over 1,000,000", seed=0)
    assert r["status"] == "completed", f"{r['status']}: {r.get('error')}"
    for op in ("filter", "select", "compute", "aggregate", "order"):
        assert op in _planner().system_text.lower()           # capability description intact
