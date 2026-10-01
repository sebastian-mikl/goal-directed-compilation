"""Network adapter — the contribution. Single LLM call emits a declarative GOAL +
OrderSpec; the deterministic planner derives the operator plan via A*; the
executor runs the plan and applies the OrderSpec. ~1 LLM call per query.

This adapter reuses the live engine (gdc.llm.llm1 + gdc.search.plan +
gdc.integrate) verbatim — it never branches per query.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from gdc.integrate import (aliased_view, execute_plan_ordered, OrderApplicationError,
                               PlanExecutionDivergence)
from gdc.primitives import FilterTypeError
from gdc.schema_bridge import build_bridge, build_briefing
from gdc.llm import llm1, EmissionDecline
from ..refuse_config import refuse_suffix
from gdc.search import plan, Unreachable
from gdc.state import State
from gdc.contract import ParseError, lint_goal, validate_goal
from gdc.order import validate_order

from ..types import REFUSAL
from .base import Adapter, df_to_result, is_transport_error, new_result


SCHEMA_PATH = Path("environment/schema.json")


class _SystemCapture:
    """Capture-only client: records the `system` of the first messages.create and
    returns a minimal valid goal JSON so llm1 parses without an API call. Used once
    per NetworkAdapter to grab the static prefix for the cache-size check."""

    def __init__(self):
        self.system = None
        self.messages = self

    def create(self, **kw):
        self.system = kw.get("system")
        text = ('{"goal":{"columns":["owner"],"grain":"row","where":[],"derive":[]},'
                '"order":{"order_by":[],"limit":null}}')
        return type("R", (), {"content": [type("T", (), {"type": "text", "text": text})()],
                              "usage": None})()


class NetworkAdapter(Adapter):
    """1 call: llm1 -> (Goal, OrderSpec). Deterministic plan + execute."""

    name = "network"

    def __init__(self, *args, schema_path: Path = SCHEMA_PATH, **kw):
        super().__init__(*args, **kw)
        self.schema_path = schema_path
        self._amap, self._adf, self._base_schema = aliased_view(self.register)
        amap_obj, self._schema_view, self._rules = build_bridge(
            list(self.register.columns), schema_path=self.schema_path)
        self._domain = build_briefing(self.schema_path, amap_obj)
        self._amap_obj = amap_obj
        # Capture the EXACT static system prefix llm1 emits (for the harness's
        # minimum-cacheable-size check) by invoking llm1 once with a capture-only
        # client. Reuses llm1 verbatim — no engine code is touched. The prefix is
        # byte-identical across queries, so caching engages on every real call.
        self._network_prefix = self._capture_prefix()

    def _capture_prefix(self) -> str:
        cap = _SystemCapture()
        try:
            llm1("__prefix_probe__", client=cap, base_schema=self._base_schema,
                 schema_view=self._schema_view, business_rules=self._rules,
                 domain=self._domain, model=self.model,
                 extra_instruction=refuse_suffix("network"))
        except Exception:                              # noqa: BLE001 — capture only
            pass
        return cap.system or ""

    def cache_prefix_text(self) -> str:
        return self._network_prefix

    def run(self, query: str, seed: int) -> dict:
        rec = new_result()
        t0 = time.perf_counter()
        try:
            goal, order = llm1(query, client=self.client,
                               base_schema=self._base_schema,
                               schema_view=self._schema_view,
                               business_rules=self._rules,
                               domain=self._domain, model=self.model,
                               extra_instruction=refuse_suffix("network"))
        except EmissionDecline as exc:      # explicit LLM decline -> clean refusal (S28 channel)
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            rec["error"] = f"declined: {exc}"
            # The call happened and was billed: account its tokens like any call
            # (was llm_calls 1 at 0 tokens, so a decline cost $0 in the record).
            rec["llm_calls"] = 1
            self._account(rec)
            rec["wall_time_s"] = time.perf_counter() - t0
            rec["artifact"] = {"kind": "network", "declined": True, "decline_reason": str(exc)}
            return rec
        except ParseError as exc:
            rec["status"] = "refused"
            rec["error"] = f"ParseError: {exc}"
            rec["result"] = REFUSAL
            rec["llm_calls"] = 1            # the call happened; it just didn't parse
            self._account(rec)
            rec["wall_time_s"] = time.perf_counter() - t0
            rec["artifact"] = {"kind": "network", "error": str(exc)}
            return rec
        except Exception as exc:            # noqa: BLE001
            if is_transport_error(exc):     # transient — don't record; let the run abort + resume
                raise
            rec["status"] = "crashed"
            rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec

        # llm1 swallows the resp, so the four-category token ledger comes from the
        # CachingClient wrapper's last_ledger() (resp=None path in _account).
        rec["llm_calls"] = 1
        self._account(rec)

        # validate + plan + execute
        gv = validate_goal(goal); ov = validate_order(order, goal)
        artifact = {"kind": "network", "goal": _goal_to_dict(goal), "order": _order_to_dict(order),
                    "goal_valid": gv.ok, "order_valid": ov.ok,
                    "goal_reason": gv.reason, "order_reason": ov.reason}
        rec["artifact"] = artifact
        if not gv.ok:
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            rec["error"] = f"ContractError(goal): {gv.reason}"
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        if not ov.ok:
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            rec["error"] = f"ContractError(order): {ov.reason}"
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        # S35 goal validity lints: a well-formed, reachable, but under-specified
        # goal (dead derive) is refused BEFORE planning — converting a would-be
        # confident wrong answer into an honest refusal. 0 extra LLM calls.
        lv = lint_goal(goal, order_cols=[c for c, _ in order.order_by])
        artifact["goal_lint_ok"] = lv.ok
        artifact["goal_lint_reason"] = lv.reason
        if not lv.ok:
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            rec["error"] = f"GoalLint: {lv.reason}"
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        p = plan(State.from_base(self._base_schema), goal)
        if isinstance(p, Unreachable):
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            rec["error"] = f"Unreachable: {p.reason}"
            artifact["plan"] = None
            artifact["unreachable_reason"] = p.reason
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        artifact["plan"] = [{"kind": o.kind, "params": dict(o.params)} for o in p]
        try:
            # S42: ordering is applied BEFORE the terminal projection when the
            # projection drops a derive-only sort key (exact-column satisfaction
            # means the select really fires now) — see integrate.execute_plan_ordered.
            frame = execute_plan_ordered(p, self._adf, order)
            frame = frame.rename(columns={c: self._amap_obj.to_real(c)
                                          for c in frame.columns
                                          if c in self._amap_obj.aliases})
        except FilterTypeError as exc:      # typed refusal family -> REFUSAL, not a crash
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            # P15f. Was the literal "FilterTypeError: …" whatever the subclass,
            # so an AggregateTypeError, ComputeExprError, FilterOpError or
            # JoinInputError was recorded under the wrong name. The verdict was
            # never affected; the label now says which guard fired.
            rec["error"] = f"{type(exc).__name__}: {exc}"
            artifact["filter_type_error"] = str(exc)
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        except OrderApplicationError as exc:  # unorderable output -> typed REFUSAL, not a crash
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            rec["error"] = f"OrderApplicationError: {exc}"
            artifact["order_application_error"] = str(exc)
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        except PlanExecutionDivergence as exc:  # executed step != planned State -> typed REFUSAL
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            rec["error"] = f"PlanExecutionDivergence: {exc}"
            artifact["plan_execution_divergence"] = str(exc)
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        except Exception as exc:            # noqa: BLE001
            rec["status"] = "crashed"
            rec["error"] = f"executor: {type(exc).__name__}: {exc}"
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        rec["result"] = df_to_result(frame)
        rec["wall_time_s"] = time.perf_counter() - t0
        return rec


def _goal_to_dict(g):
    grain = "row" if g.grouped_by is None else {"grouped_by": list(g.grouped_by)}
    return {"columns": list(g.columns), "grain": grain,
            "where": [list(w) for w in g.where],
            "derive": [{"name": n, "expr": e} for n, e in g.derive]}


def _order_to_dict(o):
    return {"order_by": [{"col": c, "dir": d} for c, d in o.order_by], "limit": o.limit}
