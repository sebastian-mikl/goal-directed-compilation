"""Planner-upfront adapter — the LLM emits a FULL ordered step plan in ONE call
over the same 4 declarative primitives + `order` the react adapter uses. The
plan is then executed mechanically (no further LLM calls).

This is the "ablation" twin of the Network adapter: same number of LLM calls,
same tool surface, but the LLM hands over the OPS directly (no goal abstraction,
no deterministic A* search).
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from ..briefing import render_briefing
from ..tools_decl import _apply_one
from ..types import REFUSAL
from ..refuse_config import refuse_suffix, decline_affordance
from gdc.primitives import FilterTypeError
from .base import Adapter, df_to_result, is_decline_text, is_transport_error, new_result

PLAN_INSTRUCTION = """\
You answer an analytical query over a tabular register. Emit a JSON object with one key, "plan",
whose value is a LIST of step dicts. Each step is one of these operations applied to a NAMED
frame (start name: "register"). Each step produces a new named frame "f0","f1",… for the next.

The 5 step ops (mirrors the Network's primitives + one ordering step):
  - {{"op":"filter","input":"<frame>","predicates":[{{"col":"...","op":"==/!=/</<=/>/>=/in/contains","value":...}}]}}
  - {{"op":"select","input":"<frame>","columns":["..."]}}                         # project columns
  - {{"op":"compute","input":"<frame>","new_col":"...","expr":"ColA OP ColB"}}    # +,-,*,/
  - {{"op":"aggregate","input":"<frame>","group_by":[]|["col"],"value_col":"...","agg":"sum|mean|count|min|max"}}
  - {{"op":"order","input":"<frame>","order_by":[{{"col":"...","dir":"asc|desc"}}],"limit":<int>?}}

At the end, your last step's output frame IS the final answer. ALSO include a "final_frame" key
naming that frame (e.g. "f3"). NO commentary outside the JSON.

REGISTER SCHEMA + DOMAIN CONTEXT:
{briefing}

REGISTER COLUMNS (verbatim):
{columns}
"""


def _extract_json(text: str) -> str:
    """Pull the first balanced JSON object out of text."""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*", "", s).strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    a = s.find("{"); b = s.rfind("}")
    if a == -1 or b == -1 or b < a:
        raise ValueError("no JSON object found")
    return s[a:b + 1]


class PlannerUpfrontAdapter(Adapter):
    """1 call: LLM emits a plan JSON; we execute it mechanically over the 5 tool ops."""

    name = "planner"

    def _build_system(self) -> str:
        return PLAN_INSTRUCTION.format(
            briefing=render_briefing(self.briefing),
            columns=", ".join(repr(c) for c in self.briefing["columns"]),
        ) + refuse_suffix("planner") + decline_affordance("planner")

    def run(self, query: str, seed: int) -> dict:
        rec = new_result()
        t0 = time.perf_counter()
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=self.system_text,
                temperature=0,
                messages=[{"role": "user", "content": f"QUERY: {query}"}],
            )
        except Exception as exc:                          # noqa: BLE001
            if is_transport_error(exc):     # transient — don't record; let the run abort + resume
                raise
            rec["status"] = "crashed"
            rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        rec["llm_calls"] = 1
        self._account(rec, resp)
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", None) == "text")
        execute_reply(text, self.register, rec)
        rec["wall_time_s"] = time.perf_counter() - t0
        return rec


def execute_reply(text: str, register, rec: dict) -> dict:
    """Everything PlannerUpfrontAdapter.run does after the API call: parse the
    reply, then execute its plan mechanically on `register`. Fills status,
    result, error and artifact into `rec` and returns it. A pure function of
    (text, register), so the paper pipeline replays recorded replies through it
    (scripts/paper_inputs.py) on the same executor the live run used."""
    rec["artifact"] = {"kind": "planner_upfront", "raw_text": text}

    # parse JSON; the model may have replied with a refusal envelope
    try:
        obj = json.loads(_extract_json(text))
    except Exception as exc:                          # noqa: BLE001
        if is_decline_text(text):
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            rec["error"] = f"plan parse-fail with decline text: {exc}"
        else:
            rec["status"] = "refused"        # unparseable -> treated as refusal (no confident answer)
            rec["result"] = REFUSAL
            rec["error"] = f"JSON parse error: {exc}"
        return rec
    # A reply that parses but is not the plan shape (an array, a bare string, a
    # step that is not an object) is a typed refusal like any malformed plan.
    # Was: `obj.get` / `step.get` raised AttributeError out of run(), which the
    # harness recorded as a crashed cell.
    malformed = (f"plan reply is {type(obj).__name__}, not an object"
                 if not isinstance(obj, dict) else None)
    if malformed is None and "refuse" in obj:
        rec["status"] = "refused"
        rec["result"] = REFUSAL
        rec["artifact"]["refusal_reason"] = obj["refuse"]
        return rec
    steps = (obj.get("plan") or []) if malformed is None else []
    final_frame = obj.get("final_frame") if malformed is None else None
    rec["artifact"]["plan"] = steps
    rec["artifact"]["final_frame"] = final_frame
    if malformed is None and not isinstance(steps, list):
        malformed = f"'plan' is {type(steps).__name__}, not a list of steps"
    if malformed is None:
        bad = next((i for i, st in enumerate(steps) if not isinstance(st, dict)), None)
        if bad is not None:
            malformed = f"step {bad}: plan step is {type(steps[bad]).__name__}, not an object"
    if malformed is not None:
        rec["status"] = "refused"
        rec["result"] = REFUSAL
        rec["error"] = f"malformed plan: {malformed}"
        return rec

    # execute mechanically
    frames = {"register": register}
    last = None
    for i, step in enumerate(steps):
        op = step.get("op")
        args = {k: v for k, v in step.items() if k != "op"}
        try:
            summary, out_name = _apply_one(op, args, frames)
        except FilterTypeError as exc:  # typed refusal family -> clean REFUSAL (mirror network)
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            # P15f: the actual class name, not the literal family root.
            rec["error"] = f"step {i} ({op!r}): {type(exc).__name__}: {exc}"
            return rec
        if out_name is None:
            rec["status"] = "crashed"
            rec["error"] = f"step {i} ({op!r}): {summary}"
            return rec
        last = out_name
    # final answer
    fname = final_frame if final_frame in frames else last
    if fname is None:
        rec["status"] = "refused"
        rec["result"] = REFUSAL
        rec["error"] = "empty plan"
        return rec
    rec["result"] = df_to_result(frames[fname])
    return rec
