"""# regression: FilterTypeError was once mapped to a refusal in the NETWORK
# adapter only, leaving the planner adapter crashing on the identical filter
# instead of refusing gracefully. Deterministic (no API).

Module 1: cross-system even-handedness. prim_select is shared by
network/planner/react via the declarative executor (_apply_one), which
catches ALL exceptions and returns an error string + None. React feeds that
back as an observation (graceful), but the PLANNER used to map a None result
to status="crashed" -> uneven (network refuses, planner "crashes" on the
identical filter). Fix: _apply_one re-raises the typed FilterTypeError;
planner catches -> refused; react catches -> graceful observation.

Module 2: a narrower, separate regression — a scalar-max over-declaration on
t1_09 is steered by an LLM1 prompt line; the engine path it targets (clean
global-max scalar) is regression-locked here too.
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

BAD = {"input": "register", "predicates": [{"col": "Cost", "op": ">", "value": "average"}]}


def _register():
    return pd.read_pickle(ROOT / "environment" / "register.pkl")


def _briefing():
    from evidence.eval_run.briefing import load_briefing
    return load_briefing(ROOT / "environment" / "schema.json", ROOT / "environment" / "register.pkl")


# ── stub LLM clients (queue of canned responses) ────────────────────────────

def _text_block(t):
    return type("T", (), {"type": "text", "text": t})()


def _tool_block(name, inp, tid="t1"):
    return type("U", (), {"type": "tool_use", "name": name, "input": inp, "id": tid})()


class _Client:
    """Returns canned responses in order (one per messages.create call)."""
    def __init__(self, *responses):
        self._q = list(responses)

        class _Msgs:
            def __init__(s, q): s.q = q
            def create(s, **kw):
                blocks = s.q.pop(0) if s.q else [_text_block("I cannot answer this.")]
                return type("R", (), {"content": blocks, "usage": None})()
        self.messages = _Msgs(self._q)


# ════════════ Module 1 — cross-system even-handedness ═══════════════════════

def test_M1_shared_executor_surfaces_typed_filter_error():
    """The shared step-executor (_apply_one) must surface the typed FilterTypeError
    so adapters can map it to a refusal — not bury it in a generic error string."""
    from evidence.eval_run.tools_decl import _apply_one
    from gdc.primitives import FilterTypeError
    with pytest.raises(FilterTypeError):
        _apply_one("filter", BAD, {"register": _register()})


def test_M1_planner_refuses_not_crashes_on_type_mismatch():
    from evidence.eval_run.adapters.planner_upfront import PlannerUpfrontAdapter
    plan = {"plan": [{"op": "filter", **BAD}], "final_frame": "f0"}
    ad = PlannerUpfrontAdapter(name="planner", client=_Client([_text_block(json.dumps(plan))]),
                               register=_register(), briefing=_briefing(), model="m")
    r = ad.run("List entries whose Cost is above the average Cost.", seed=0)
    assert r["status"] == "refused", f"expected refused, got {r['status']}: {r.get('error')}"


def test_M1_planner_valid_filter_still_completes():
    from evidence.eval_run.adapters.planner_upfront import PlannerUpfrontAdapter
    ok = {"input": "register", "predicates": [{"col": "Cost", "op": ">", "value": 1000000}]}
    plan = {"plan": [{"op": "filter", **ok}], "final_frame": "f0"}
    ad = PlannerUpfrontAdapter(name="planner", client=_Client([_text_block(json.dumps(plan))]),
                               register=_register(), briefing=_briefing(), model="m")
    r = ad.run("entries with Cost over 1,000,000", seed=0)
    assert r["status"] == "completed", f"{r['status']}: {r.get('error')}"


def test_M1_react_graceful_refusal_on_type_mismatch():
    """React feeds the error back as an observation (its design), then declines ->
    a clean refusal. Must NEVER end 'crashed' / raise uncaught."""
    from evidence.eval_run.adapters.react import ReactAdapter
    client = _Client(
        [_tool_block("filter", BAD)],                 # iter 1: bad filter -> error observation
        [_text_block("I cannot answer this.")],       # iter 2: decline
    )
    ad = ReactAdapter(name="react", client=client, register=_register(),
                      briefing=_briefing(), model="m", max_iters=4)
    r = ad.run("List entries whose Cost is above the average Cost.", seed=0)
    assert r["status"] == "refused", f"expected refused, got {r['status']}: {r.get('error')}"


# ════════════ Module 2 — t1_09 over-declaration (network translator) ═════════
# Narrow LLM1 prompt line: aggregate-VALUE queries emit ONLY the aggregate
# column(s), never an identifying/per-row column (which a collapsing aggregate
# can't carry -> unreachable). The engine path it steers toward is locked here;
# the emission effect (t1_09 refuse->correct) is verified in the re-run.

def test_M2_clean_global_max_scalar_returns_exact_gold():
    """The goal the new prompt line steers toward: max Cost, global grain, no
    extra columns -> plans and returns the gold scalar 4,836,800."""
    from gdc.state import State, Goal
    from gdc.search import plan, Unreachable
    from gdc.integrate import aliased_view, execute_plan
    _, adf, base = aliased_view(_register())
    g = Goal.make(columns=[("max_cost", "unknown")], grain={"grouped_by": []}, where=[],
                  derive=[{"name": "max_cost", "expr": "max(cost)"}])
    p = plan(State.from_base(base), g)
    assert not isinstance(p, Unreachable), getattr(p, "reason", "")
    assert int(execute_plan(p, adf).iat[0, 0]) == 4836800


def test_M2_prompt_line_present_and_scoped_to_aggregate_value():
    """S31 generalized the S27 M2 line: a scalar aggregate emits exactly one derived
    field (never the underlying raw column), kept DISTINCT from row-selection (top-N).
    (a) the aggregate guidance is present; (b) row selection still lists columns + orders."""
    from gdc.llm import GOAL_ORDER_INSTRUCTION
    t = GOAL_ORDER_INSTRUCTION.lower()
    assert "scalar aggregate" in t                             # (a) the targeted case
    assert ("do not" in t or "don't" in t) and ("underlying" in t or "raw" in t)
    assert "row selection" in t                                # (b) the top-N distinction


def test_M2_provenance_records_network_prompt_version():
    from evidence.eval_run.logger import write_provenance
    from gdc.llm import GOAL_PROMPT_VERSION
    import tempfile, os
    d = tempfile.mkdtemp()
    p = Path(d) / "prov.json"
    write_provenance(p, model="m", temperature=None, seeds=[0], bounds={}, pricing={},
                     corpus_path=ROOT / "eval_corpus.json",
                     gold_path=ROOT / "evidence" / "gold" / "gold.json",
                     prompts={}, comparator_version="s26",
                     network_prompt_version=GOAL_PROMPT_VERSION)
    prov = json.loads(p.read_text())
    assert prov["network_prompt_version"] == GOAL_PROMPT_VERSION
    assert prov["comparator_version"] == "s26"
