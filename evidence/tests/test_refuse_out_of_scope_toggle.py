"""S40 — REFUSE_OUT_OF_SCOPE_DATA toggle + decline channel (deterministic; no API).

Companion to test_s28_refuse_toggle.py's S28 suite, for the table/data-coverage
counterpart: the network system answered ALL 3 Spider out_of_scope_register smoke-test
refuse-cases (wta_01, world_07, bike_11) instead of declining, because no instruction
anywhere told it that declining was an option for a MISSING-DATA reason (S28's
REFUSE_UNSUPPORTED only covers missing OPERATIONS, and was off by default anyway).
This suite exercises the new, independent toggle in isolation, mirroring test_s28's
structure exactly.
"""
from __future__ import annotations

import json
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


def _network(payload_obj):
    from evidence.eval_run.adapters.network import NetworkAdapter
    return NetworkAdapter(name="network", client=_Client([_text_block(json.dumps(payload_obj))]),
                          register=_register(), briefing=_briefing(), model="m")


# ════════════ Decline channel — reuses S28's EXISTING network envelope ═══════

def test_network_decline_envelope_maps_to_clean_refusal():
    """LLM emits {"refuse": "..."} for a data-coverage reason -> the SAME clean-decline
    path S28 already built (artifact.declined=True), not a new outcome class."""
    ad = _network({"refuse": "needs a players table this register doesn't have"})
    r = ad.run("Find the total number of players.", seed=0)
    assert r["status"] == "refused", f"{r['status']}: {r.get('error')}"
    assert r["artifact"].get("declined") is True


# ════════════ Toggle: OFF byte-identical, ON adds the NEW instruction only ═══

def test_OFF_adds_nothing_to_any_prompt(monkeypatch):
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    monkeypatch.delenv("REFUSE_OUT_OF_SCOPE_DATA", raising=False)
    from evidence.eval_run.refuse_config import refuse_suffix, refuse_out_of_scope_data_enabled
    assert refuse_out_of_scope_data_enabled() is False
    for sys in ("network", "planner", "react"):
        assert refuse_suffix(sys) == "", f"{sys}: OFF must append nothing (byte-identical prompt)"


def test_ON_adds_new_instruction_to_all_three(monkeypatch):
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    monkeypatch.setenv("REFUSE_OUT_OF_SCOPE_DATA", "1")
    from evidence.eval_run.refuse_config import REFUSE_OUT_OF_SCOPE_INSTRUCTION, NETWORK_DECLINE_DOC, REFUSE_INSTRUCTION
    from evidence.eval_run.adapters.planner_upfront import PlannerUpfrontAdapter
    from evidence.eval_run.adapters.react import ReactAdapter
    pl = PlannerUpfrontAdapter(name="planner", client=_Client(), register=_register(),
                               briefing=_briefing(), model="m")
    re = ReactAdapter(name="react", client=_Client(), register=_register(),
                      briefing=_briefing(), model="m")
    nw = _network({"goal": {}, "order": {}})
    assert REFUSE_OUT_OF_SCOPE_INSTRUCTION in pl.system_text
    assert REFUSE_OUT_OF_SCOPE_INSTRUCTION in re.system_text
    assert REFUSE_OUT_OF_SCOPE_INSTRUCTION in nw.cache_prefix_text()
    assert NETWORK_DECLINE_DOC in nw.cache_prefix_text()          # network told HOW to decline
    assert REFUSE_INSTRUCTION not in nw.cache_prefix_text()       # S28's toggle stayed OFF


def test_toggles_are_independent_not_coupled(monkeypatch):
    """Turning ONE toggle on must not activate the other's text, in either direction —
    this is the whole point of keeping them separate (Step 1's design decision)."""
    from evidence.eval_run.refuse_config import REFUSE_INSTRUCTION, REFUSE_OUT_OF_SCOPE_INSTRUCTION, refuse_suffix

    monkeypatch.setenv("REFUSE_UNSUPPORTED", "1")
    monkeypatch.delenv("REFUSE_OUT_OF_SCOPE_DATA", raising=False)
    only_s28 = refuse_suffix("network")
    assert REFUSE_INSTRUCTION in only_s28
    assert REFUSE_OUT_OF_SCOPE_INSTRUCTION not in only_s28

    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    monkeypatch.setenv("REFUSE_OUT_OF_SCOPE_DATA", "1")
    only_s40 = refuse_suffix("network")
    assert REFUSE_OUT_OF_SCOPE_INSTRUCTION in only_s40
    assert REFUSE_INSTRUCTION not in only_s40


def test_both_ON_composes_both_instructions_once_each(monkeypatch):
    monkeypatch.setenv("REFUSE_UNSUPPORTED", "1")
    monkeypatch.setenv("REFUSE_OUT_OF_SCOPE_DATA", "1")
    from evidence.eval_run.refuse_config import (REFUSE_INSTRUCTION, REFUSE_OUT_OF_SCOPE_INSTRUCTION,
                                                 NETWORK_DECLINE_DOC, refuse_suffix)
    text = refuse_suffix("network")
    assert text.count(REFUSE_INSTRUCTION) == 1
    assert text.count(REFUSE_OUT_OF_SCOPE_INSTRUCTION) == 1
    assert text.count(NETWORK_DECLINE_DOC) == 1                   # not duplicated


def test_ON_byte_identical_to_pre_s40_when_only_s28_enabled(monkeypatch):
    """Regression guard for Step 1's stated compatibility claim: with the NEW toggle
    untouched (unset/off), refuse_suffix("network") is byte-identical to what S28
    alone produced before this change."""
    monkeypatch.setenv("REFUSE_UNSUPPORTED", "1")
    monkeypatch.delenv("REFUSE_OUT_OF_SCOPE_DATA", raising=False)
    from evidence.eval_run.refuse_config import REFUSE_INSTRUCTION, NETWORK_DECLINE_DOC, refuse_suffix
    expected = "\n\n" + REFUSE_INSTRUCTION + "\n" + NETWORK_DECLINE_DOC
    assert refuse_suffix("network") == expected


def test_instruction_is_positive_no_table_enumeration():
    """DISCIPLINE (mirrors S28's own rule): describe the boundary positively; never
    name a specific excluded table/column from the eval corpus (wta_01/world_07/
    bike_11's players/city/countrylanguage/weather etc.) — that would be authoring
    from the eval."""
    from evidence.eval_run.refuse_config import REFUSE_OUT_OF_SCOPE_INSTRUCTION
    t = REFUSE_OUT_OF_SCOPE_INSTRUCTION.lower()
    assert "base columns" in t
    assert "decline" in t
    for banned in ("player", "ranking", "city", "countrylanguage", "weather", "station",
                   "matches", "country", "trip", "gelderland", "temperature"):
        assert banned not in t, f"instruction must not name eval-specific concept {banned!r}"


def test_OFF_sample_emission_unchanged(monkeypatch):
    """With OFF, a normal goal still emits + executes (toggle code dormant)."""
    monkeypatch.delenv("REFUSE_UNSUPPORTED", raising=False)
    monkeypatch.delenv("REFUSE_OUT_OF_SCOPE_DATA", raising=False)
    ad = _network({"goal": {"columns": ["title", "cost"], "grain": "row",
                            "where": [{"col": "cost", "op": ">", "val": 1000000}], "derive": []},
                   "order": {"order_by": [], "limit": None}})
    assert ad.run("entries with Cost over 1,000,000", seed=0)["status"] == "completed"


def test_provenance_tag_constant_distinct_from_s28():
    from evidence.eval_run.refuse_config import REFUSE_PROMPT_TAG, REFUSE_OOS_PROMPT_TAG
    assert REFUSE_OOS_PROMPT_TAG != REFUSE_PROMPT_TAG
    assert REFUSE_OOS_PROMPT_TAG == "refuse_out_of_scope_data"
