"""React adapter — observe→act→observe loop with the LLM picking ONE primitive
per step over the same 5-op tool surface as planner_upfront. Bounded by
max_iters (treated as a recorded failure, status=timeout, when hit).
"""
from __future__ import annotations

import time

from ..briefing import render_briefing
from ..tools_decl import DECL_TOOLS, apply_decl_tool, _apply_one
from ..types import REFUSAL
from ..refuse_config import refuse_suffix, decline_affordance
from gdc.primitives import FilterTypeError
from .base import Adapter, df_to_result, is_decline_text, is_transport_error, new_result


REACT_SYSTEM = """\
You answer an analytical query over a tabular register using exactly five tools (declarative
primitives, the same the Network uses): filter, select (project), aggregate, compute, order
(sort + optional limit). Start from a frame named "register"; each tool reads a frame and produces
a new named frame "f0","f1",… Call final_answer with the name of the frame that IS the answer
(a 1x1 frame for a scalar; a multi-row frame for a list).

REGISTER SCHEMA + DOMAIN CONTEXT:
{briefing}

REGISTER COLUMNS (verbatim):
{columns}
"""


def typed_refusal_observation(tool: str, exc: FilterTypeError) -> str:
    """The observation react receives for a typed refusal from the shared executor.
    The FilterTypeError root is the filter's type guard, a comparison the register
    cannot make, and keeps the sentence react always saw for it. Every other member
    of the family (an absent column, a compute or aggregate the executor refuses, an
    undeclared function, an absent sort key) is named with its class and message: it
    is usually a call the agent can fix, so it is not told the request is
    inexpressible."""
    if type(exc) is FilterTypeError:
        return f"cannot apply {tool}: {exc}. This comparison is not expressible over the register."
    return (f"cannot apply {tool}: {type(exc).__name__}: {str(exc).rstrip('.')}. "
            f"Fix the call, or answer that it cannot be computed.")


class ReactAdapter(Adapter):
    name = "react"

    def _build_system(self) -> str:
        return REACT_SYSTEM.format(
            briefing=render_briefing(self.briefing),
            columns=", ".join(repr(c) for c in self.briefing["columns"]),
        ) + refuse_suffix("react") + decline_affordance("react")

    def cache_tools(self):
        return DECL_TOOLS

    def run(self, query: str, seed: int) -> dict:
        rec = new_result()
        t0 = time.perf_counter()
        system = self.system_text                  # built once; cached prefix stays warm
        frames = {"register": self.register}
        messages = [{"role": "user", "content": f"QUERY: {query}"}]
        trace = []
        final_frame_name = None
        last_text = ""
        cap_hit = True
        for it in range(self.max_iters):
            try:
                resp = self.client.messages.create(
                    model=self.model, max_tokens=self.max_tokens, system=system,
                    tools=DECL_TOOLS, messages=messages,
                    **self._temp_kwargs(),
                )
            except Exception as exc:                          # noqa: BLE001
                if is_transport_error(exc):  # transient — don't record; let the run abort + resume
                    raise
                rec["status"] = "crashed"
                rec["error"] = f"iter {it}: {type(exc).__name__}: {exc}"
                rec["artifact"] = {"kind": "react", "trace": trace, "iters": it}
                rec["wall_time_s"] = time.perf_counter() - t0
                return rec
            rec["llm_calls"] += 1
            self._account(rec, resp)

            blocks = list(resp.content)
            tool_uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
            text_blocks = [getattr(b, "text", "") for b in blocks
                           if getattr(b, "type", None) == "text"]
            last_text = "\n".join(text_blocks)
            messages.append({"role": "assistant", "content": blocks})

            if not tool_uses:
                cap_hit = False
                break                       # the model stopped — text-only reply

            results = []
            terminal = False
            for tu in tool_uses:
                if tu.name == "final_answer":
                    fname = dict(tu.input).get("frame")
                    trace.append({"tool": "final_answer", "args": dict(tu.input)})
                    final_frame_name = fname
                    terminal = True
                    content = "final answer recorded."
                else:
                    args = dict(tu.input)
                    try:
                        summary, _ = _apply_one(tu.name, args, frames)
                    except FilterTypeError as exc:  # typed refusal — fed back as an observation, not a crash
                        summary = typed_refusal_observation(tu.name, exc)
                    trace.append({"tool": tu.name, "args": args,   # summary as the model saw it
                                  "summary": str(summary)[:4000]})
                    content = summary
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": str(content)[:4000]})
            messages.append({"role": "user", "content": results})
            if terminal:
                cap_hit = False
                break

        rec["artifact"] = {"kind": "react", "trace": trace,
                           "iters": len(rec["per_call_tokens"]),
                           "final_frame": final_frame_name,
                           "last_text": last_text}
        if cap_hit:
            rec["status"] = "timeout"
            rec["result"] = REFUSAL          # no parseable result
            rec["error"] = f"max_iters {self.max_iters} hit"
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        if final_frame_name is not None and final_frame_name in frames:
            rec["result"] = df_to_result(frames[final_frame_name])
        elif is_decline_text(last_text):
            rec["status"] = "refused"
            rec["result"] = REFUSAL
        else:
            rec["status"] = "refused"        # text-only, not a decline -> still no parseable result
            rec["result"] = REFUSAL
        rec["wall_time_s"] = time.perf_counter() - t0
        return rec
