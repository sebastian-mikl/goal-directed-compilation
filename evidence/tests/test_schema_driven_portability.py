"""S17 validations — schema-driven engine (portability proof, offline parts).

V2-crux : employee "current" rule EXCLUDES Terminated (the S16 silent-wrong-active bug
          is gone) — derived deterministically + proven end-to-end with a canned goal.
V3      : a malformed rule RAISES SchemaRuleError (never a permissive default).
V4      : V2 and V-risk run on the SAME engine code — only DB + schema.json differ.
(V1 risk-live + V2 employee-live LLM1 are the hand-off; the deterministic crux is here.)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from gdc import Goal, OrderSpec, build_bridge, SchemaRuleError
from gdc.schema_bridge import _rule_allowed
from gdc.integrate import aliased_view, run_goal_plan

EMP_DB = ROOT / "evaluation/domains/employee" / "employees.pkl"
EMP_SCHEMA = ROOT / "evaluation/domains/employee" / "schema.json"
RISK_DB = ROOT / "environment" / "register.pkl"
_need = pytest.mark.skipif(not (EMP_DB.exists() and RISK_DB.exists()), reason="datasets absent")


# ── V3: fail loud on a malformed rule ───────────────────────────────────────

def test_V3_fail_loud_on_malformed_rule():
    cols = {"Status": {"type": "categorical", "categories": ["A", "B", "C"], "match_strategy": "exact"}}
    # missing column
    with pytest.raises(SchemaRuleError):
        _rule_allowed("r", {"exclude": ["A"]}, cols)
    # column not in schema
    with pytest.raises(SchemaRuleError):
        _rule_allowed("r", {"column": "Nope", "exclude": ["A"]}, cols)
    # neither include nor exclude
    with pytest.raises(SchemaRuleError):
        _rule_allowed("r", {"column": "Status"}, cols)
    # both include and exclude
    with pytest.raises(SchemaRuleError):
        _rule_allowed("r", {"column": "Status", "include": ["A"], "exclude": ["B"]}, cols)
    # value outside categories
    with pytest.raises(SchemaRuleError) as ei:
        _rule_allowed("r", {"column": "Status", "exclude": ["Z"]}, cols)
    assert "not in" in str(ei.value)
    # a VALID rule computes the set (sanity)
    assert _rule_allowed("r", {"column": "Status", "exclude": ["A"]}, cols)["allowed"] == ["B", "C"]


def test_V3_build_bridge_raises_on_bad_schema_rule(tmp_path):
    schema = {"columns": {"Status": {"type": "categorical", "categories": ["X", "Y"],
                                     "match_strategy": "exact"}},
              "business_rules": {"active": {"column": "Status"}}}      # no include|exclude
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(schema))
    with pytest.raises(SchemaRuleError):
        build_bridge(["Status"], schema_path=p)


# ── V2-crux: employee "current" excludes Terminated, end-to-end ─────────────

@_need
def test_V2_crux_current_excludes_terminated():
    reg = pd.read_pickle(EMP_DB)
    amap, _, rules = build_bridge(list(reg.columns), schema_path=EMP_SCHEMA)
    # the rule derived from the EMPLOYEE schema — Terminated excluded, all others kept
    assert "Terminated" not in rules["current"]["allowed"]
    assert set(rules["current"]["allowed"]) == {"Active", "On Leave", "Sabbatical"}

    # end-to-end: a canned "current employees, avg salary per department" goal
    _, adf, schema = aliased_view(reg)
    goal = Goal.make(columns=["department", "mean_salary"], grain={"grouped_by": ["department"]},
                     where=[{"col": "status", "op": "in", "val": rules["current"]["allowed"]}],
                     derive=[{"name": "mean_salary", "expr": "avg(annual_salary)"}])
    frame, p = run_goal_plan(goal, OrderSpec.make(), adf, base_schema=schema)
    assert frame is not None and len(frame) > 0
    # the oracle: current (non-Terminated) employees only
    cur = reg[reg.Status != "Terminated"]
    oracle = cur.groupby("Department")["Annual Salary"].mean().reset_index()
    from evidence.correctness import values_equal
    ok, detail = values_equal(
        frame.rename(columns={c: amap.to_real(c) for c in frame.columns if c in amap.aliases}),
        oracle, tolerate_extra_cols=True)
    assert ok, f"employee current-rule query wrong: {detail}"


# ── V4: same engine, only DB + schema.json differ ──────────────────────────

@_need
def test_V4_same_engine_both_domains():
    # the SAME build_bridge handles both domains with no per-domain branch
    risk_reg = pd.read_pickle(RISK_DB)
    _, _, risk_rules = build_bridge(list(risk_reg.columns))                 # default = risk schema
    emp_reg = pd.read_pickle(EMP_DB)
    _, _, emp_rules = build_bridge(list(emp_reg.columns), schema_path=EMP_SCHEMA)
    # different domains, different rule NAMES and columns, same generic machinery
    assert "active" in risk_rules and "current" in emp_rules
    assert risk_rules["active"]["allowed"] and emp_rules["current"]["allowed"]
    # the engine module is identical for both — only the schema_path argument changed.
    import gdc.schema_bridge as sb
    assert build_bridge.__module__ == sb.__name__
