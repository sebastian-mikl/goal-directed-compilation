"""S13 Part B — minimal LLM1 (Goal, OrderSpec) + solve().

LLM-free here: a MOCK client returns canned Call-1 JSON, so the parse → validate →
plan pipeline is exercised without an API key. The live model is handed off (--nl).

B-rank : a ranking request → Goal (grouped_by[owner], sum(cost), NO ordering inside)
         + OrderSpec {total_cost desc, limit 5}; validates and is reachable.
B-typed: malformed / out-of-contract output → ParseError / ContractError, never raises.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gdc.probe import DEMO_SCHEMA
from gdc import Op, Unreachable, ContractError, ParseError, OrderSpec
from gdc.llm import llm1, solve


# ── mock Call-1 client ───────────────────────────────────────────────────────

class _Txt:
    type = "text"
    def __init__(self, t): self.text = t

class _Resp:
    def __init__(self, t): self.content = [_Txt(t)]

class _Msgs:
    def __init__(self, t): self._t = t
    def create(self, **kw): return _Resp(self._t)

class MockClient:
    def __init__(self, text): self.messages = _Msgs(text)


RANK_JSON = """```json
{"goal": {"columns": ["owner", "total_cost"],
          "grain": {"grouped_by": ["owner"]},
          "where": [{"col": "status", "op": "==", "val": "active"}],
          "derive": [{"name": "total_cost", "expr": "sum(cost)"}]},
 "order": {"order_by": [{"col": "total_cost", "dir": "desc"}], "limit": 5}}
```"""


# ── B-rank ───────────────────────────────────────────────────────────────────

def test_B_rank_goal_holds_no_ordering_order_holds_ranking():
    goal, order = llm1("show the top 5 owners by total cost for active risks",
                       client=MockClient(RANK_JSON), base_schema=DEMO_SCHEMA)
    # the ranking is NOT inside the goal
    assert goal.grouped_by == ("owner",)
    assert ("total_cost", "sum(cost)") in goal.derive
    assert goal.where == (("status", "==", "active"),)
    assert "order" not in {f for f in goal.__dataclass_fields__}     # structurally impossible
    # the ranking lives in the OrderSpec
    assert order == OrderSpec.make(order_by=[{"col": "total_cost", "dir": "desc"}], limit=5)

    # solve → (plan, order): validates + reachable
    result = solve("show the top 5 owners by total cost for active risks",
                   client=MockClient(RANK_JSON), base_schema=DEMO_SCHEMA)
    assert isinstance(result, tuple), f"expected (plan, order), got {result!r}"
    plan_ops, order2 = result
    assert all(isinstance(o, Op) for o in plan_ops) and plan_ops
    assert order2.limit == 5
    # filter(status) precedes aggregate — derived order, no ordering op in the plan
    kinds = [o.kind for o in plan_ops]
    assert kinds.index("filter") < kinds.index("aggregate")
    assert "sort" not in kinds and "limit" not in kinds            # ordering is NOT a plan op


# ── B-typed: never raises, always a typed result ─────────────────────────────

def test_B_typed_malformed_json_returns_parse_error():
    r = solve("anything", client=MockClient("sorry, I can't do that — no JSON here"), base_schema=DEMO_SCHEMA)
    assert isinstance(r, ParseError)


def test_B_typed_missing_goal_returns_parse_error():
    r = solve("x", client=MockClient('{"order": {"order_by": [], "limit": null}}'), base_schema=DEMO_SCHEMA)
    assert isinstance(r, ParseError)


def test_B_typed_out_of_contract_returns_contract_error():
    two_agg = """{"goal": {"columns": ["owner", "total_cost", "avg_sev"],
        "grain": {"grouped_by": ["owner"]}, "where": [],
        "derive": [{"name": "total_cost", "expr": "sum(cost)"},
                   {"name": "avg_sev", "expr": "avg(severity)"}]},
        "order": {"order_by": [], "limit": null}}"""
    r = solve("two aggregates please", client=MockClient(two_agg), base_schema=DEMO_SCHEMA)
    assert isinstance(r, ContractError)
    assert r.reason == "more than one aggregate"


def test_B_typed_bad_order_column_returns_contract_error():
    bad_order = """{"goal": {"columns": ["owner", "total_cost"],
        "grain": {"grouped_by": ["owner"]}, "where": [],
        "derive": [{"name": "total_cost", "expr": "sum(cost)"}]},
        "order": {"order_by": [{"col": "severity", "dir": "desc"}], "limit": 5}}"""
    r = solve("rank by a column not in the result", client=MockClient(bad_order), base_schema=DEMO_SCHEMA)
    assert isinstance(r, ContractError) and "not in goal output" in r.reason
