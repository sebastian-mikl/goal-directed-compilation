"""A GDC decline or unparseable emission is a billed call: NetworkAdapter.run
accounts its tokens like any other call.

The decline path (EmissionDecline) and the parse-error path recorded llm_calls 1
at 0 tokens, so every decline cost $0 in the record (the Spider s40on declines).
llm1 swallows the response, so the tokens come from the CachingClient's
last_ledger(), as on the answer path. Employee domain register; no API call.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from evidence.eval_run.adapters.network import NetworkAdapter
from evidence.eval_run.briefing import load_briefing
from evidence.eval_run.logger import CachingClient
from evidence.eval_run.pricing import cost_usd_cached

ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "evaluation" / "domains" / "employee" / "employees.pkl"
SCHEMA = ROOT / "evaluation" / "domains" / "employee" / "schema.json"
MODEL = "claude-sonnet-4-5"
USAGE = {"input_tokens": 180, "output_tokens": 35, "cache_creation_input_tokens": 0,
         "cache_read_input_tokens": 2400}


class _Inner:
    def __init__(self, text):
        self.text = text
        self.messages = self

    def create(self, **kw):
        block = type("T", (), {"type": "text", "text": self.text})()
        return type("R", (), {"content": [block], "usage": type("U", (), dict(USAGE))()})()


def _run(text):
    import pandas as pd
    ad = NetworkAdapter(name="network", client=CachingClient(_Inner(text)),
                        register=pd.read_pickle(REGISTER), briefing=load_briefing(SCHEMA, REGISTER),
                        model=MODEL, schema_path=SCHEMA)
    return ad.run("a question", seed=0)


@pytest.mark.parametrize("text,status,marker", [
    (json.dumps({"refuse": "the register has no such table"}), "refused", "declined"),
    ("no JSON in this reply", "refused", "ParseError"),
])
def test_decline_and_parse_error_are_costed(text, status, marker):
    rec = _run(text)
    assert rec["status"] == status and marker in rec["error"]
    assert rec["llm_calls"] == 1
    assert rec["input_tokens"] == USAGE["input_tokens"] + USAGE["cache_read_input_tokens"]
    assert rec["uncached_input_tokens"] == USAGE["input_tokens"]
    assert rec["cache_read_tokens"] == USAGE["cache_read_input_tokens"]
    assert rec["output_tokens"] == USAGE["output_tokens"]
    assert len(rec["per_call_tokens"]) == 1
    cost = cost_usd_cached(MODEL, rec["uncached_input_tokens"], rec["cache_write_tokens"],
                           rec["cache_read_tokens"], rec["output_tokens"])
    assert cost > 0


def test_decline_costs_the_same_tokens_as_an_answer():
    answer = _run(json.dumps({"goal": {"columns": ["nonexistent_column"], "grain": "row",
                                       "where": [], "derive": []},
                              "order": {"order_by": [], "limit": None}}))
    decline = _run(json.dumps({"refuse": "out of scope"}))
    keys = ("llm_calls", "input_tokens", "output_tokens", "uncached_input_tokens",
            "cache_write_tokens", "cache_read_tokens", "per_call_tokens")
    assert {k: decline[k] for k in keys} == {k: answer[k] for k in keys}
