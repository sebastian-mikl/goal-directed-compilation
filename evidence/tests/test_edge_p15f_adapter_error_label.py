"""P15f — the network and planner adapters recorded every member of the
FilterTypeError family under the literal label "FilterTypeError:", so an
AggregateTypeError, ComputeExprError, FilterOpError or JoinInputError appeared
in `rec["error"]` under the wrong name. Cosmetic — the verdict (`refused`) was
never affected — but a recorded error field that misnames the guard that
fired is a wrong record.

Deterministic: canned client responses, the public employee domain. Confirmed
to fail with the fix reverted."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOMAIN = ROOT / "evaluation" / "domains" / "employee"
pytestmark = pytest.mark.skipif(
    not (DOMAIN / "employees.pkl").exists(), reason="employee domain data absent")


def _text_block(t):
    return type("T", (), {"type": "text", "text": t})()


class _Client:
    """Canned responses, one per messages.create call."""
    def __init__(self, *responses):
        q = list(responses)

        class _Msgs:
            def create(self, **kw):
                return type("R", (), {"content": q.pop(0), "usage": None})()
        self.messages = _Msgs()


def _register():
    return pd.read_pickle(DOMAIN / "employees.pkl")


def _briefing():
    from evidence.eval_run.briefing import load_briefing
    return load_briefing(DOMAIN / "schema.json", DOMAIN / "employees.pkl")


def _network(emission):
    from evidence.eval_run.adapters.network import NetworkAdapter
    return NetworkAdapter(name="network", client=_Client([_text_block(json.dumps(emission))]),
                          register=_register(), briefing=_briefing(), model="m",
                          schema_path=DOMAIN / "schema.json")


def _planner(plan):
    from evidence.eval_run.adapters.planner_upfront import PlannerUpfrontAdapter
    return PlannerUpfrontAdapter(name="planner", client=_Client([_text_block(json.dumps(plan))]),
                                 register=_register(), briefing=_briefing(), model="m")


# ── network ──────────────────────────────────────────────────────────────────

def test_network_labels_a_compute_refusal_as_ComputeExprError():
    """A derive over a text column plans (it is in the compute grammar and its
    columns exist) and the executor refuses it (P56: no numeric content).
    Before P15f: "FilterTypeError: compute …". (The original test used `annual_salary`,
    a bare numeric column, which the shared grammar now evaluates as a copy.)"""
    rec = _network({"goal": {"columns": ["name", "x"], "grain": "row", "where": [],
                             "derive": [{"name": "x", "expr": "annual_salary + department"}]},
                    "order": {"order_by": [], "limit": None}}).run("synthetic", seed=0)
    assert rec["status"] == "refused"
    assert rec["error"].startswith("ComputeExprError: "), rec["error"]


def test_network_labels_an_aggregate_refusal_as_AggregateTypeError():
    rec = _network({"goal": {"columns": ["department", "m"],
                             "grain": {"grouped_by": ["department"]}, "where": [],
                             "derive": [{"name": "m", "expr": "sum(name)"}]},
                    "order": {"order_by": [], "limit": None}}).run("synthetic", seed=0)
    assert rec["status"] == "refused"
    assert rec["error"].startswith("AggregateTypeError: "), rec["error"]


def test_network_still_labels_the_root_class_by_its_own_name():
    """A genuine FilterTypeError keeps its label — the fix reads the type, it
    does not rename anything."""
    rec = _network({"goal": {"columns": ["name"], "grain": "row",
                             "where": [{"col": "annual_salary", "op": ">", "val": "average"}],
                             "derive": []},
                    "order": {"order_by": [], "limit": None}}).run("synthetic", seed=0)
    assert rec["status"] == "refused"
    assert rec["error"].startswith("FilterTypeError: "), rec["error"]


# ── planner ──────────────────────────────────────────────────────────────────

def test_planner_labels_an_aggregate_refusal_as_AggregateTypeError():
    rec = _planner({"plan": [{"op": "aggregate", "input": "register",
                              "group_by": "Department", "value_col": "Name",
                              "agg": "sum"}],
                    "final_frame": "f0"}).run("synthetic", seed=0)
    assert rec["status"] == "refused"
    assert rec["error"].startswith("step 0 ('aggregate'): AggregateTypeError: "), rec["error"]


def test_planner_labels_a_filter_op_refusal_as_FilterOpError():
    rec = _planner({"plan": [{"op": "filter", "input": "register",
                              "predicates": [{"col": "Name", "op": "like", "value": "a"}]}],
                    "final_frame": "f0"}).run("synthetic", seed=0)
    assert rec["status"] == "refused"
    assert rec["error"].startswith("step 0 ('filter'): FilterOpError: "), rec["error"]


def test_verdicts_unchanged_by_the_label():
    """The cosmetic fix must not move a status: refused stays refused, and a
    clean plan still completes."""
    rec = _planner({"plan": [{"op": "filter", "input": "register",
                              "predicates": [{"col": "Department", "op": "==",
                                              "value": "Engineering"}]}],
                    "final_frame": "f0"}).run("synthetic", seed=0)
    assert rec["status"] == "completed", rec.get("error")
