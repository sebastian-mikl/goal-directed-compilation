"""Code-agent adapter — the LLM writes & runs Python (pandas) over the
dataframe. Bounded by max_retries (each retry is a tool call). Treated as a
recorded failure (status=timeout) when the bound is hit without a result.
One namespace per episode: what a run_python call defines, `result` included, is
visible to every later call and to final_answer.
"""
from __future__ import annotations

import time

from ..briefing import render_briefing
from ..tools_code import code_tools, new_namespace, run_code
from ..types import REFUSAL
from .base import Adapter, df_to_result, is_decline_text, is_transport_error, new_result


CODE_SYSTEM = """\
You answer an analytical query over a pandas DataFrame `df` ({n_rows}-row register).
Use run_python to compute. ALWAYS store your answer in `result = ...` (a DataFrame,
Series, or scalar) — do NOT use print(). A compact summary of `result` is returned to you.
Once you have verified the answer is correct, call final_answer with code that sets `result`.
Be efficient — at most a few attempts.

If the query genuinely cannot be answered from this data, STOP with a plain-text reply
explaining you cannot, instead of fabricating a result.

REGISTER SCHEMA + DOMAIN CONTEXT:
{briefing}

REGISTER COLUMNS (verbatim):
{columns}
"""


class CodeAgentAdapter(Adapter):
    name = "code_agent"

    def _build_system(self) -> str:
        return CODE_SYSTEM.format(
            n_rows=len(self.register),
            briefing=render_briefing(self.briefing),
            columns=", ".join(repr(c) for c in self.briefing["columns"]),
        )

    def cache_tools(self):
        """run_python / final_answer, stating this domain's row count. Deterministic
        per domain, so the cached prefix stays byte-identical across cells."""
        return code_tools(len(self.register))

    def run(self, query: str, seed: int) -> dict:
        rec = new_result()
        t0 = time.perf_counter()
        system = self.system_text                  # built once; cached prefix stays warm
        tools = self.cache_tools()
        ns = new_namespace(self.register)          # shared by every call of this episode
        messages = [{"role": "user", "content": f"QUERY: {query}"}]
        trace: list[dict] = []
        last_text = ""
        final_result = None
        cap_hit = True
        attempts = 0
        for it in range(self.max_retries + 1):       # +1: at least one initial attempt
            attempts = it + 1
            try:
                resp = self.client.messages.create(
                    model=self.model, max_tokens=self.max_tokens, system=system,
                    tools=tools, messages=messages,
                    **self._temp_kwargs(),
                )
            except Exception as exc:                          # noqa: BLE001
                if is_transport_error(exc):  # transient — don't record; let the run abort + resume
                    raise
                rec["status"] = "crashed"
                rec["error"] = f"iter {it}: {type(exc).__name__}: {exc}"
                rec["artifact"] = {"kind": "code_agent", "trace": trace, "iters": it}
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
                break

            results = []
            terminal = False
            for tu in tool_uses:
                code = dict(tu.input).get("code", "")
                summary, value = run_code(code, ns)
                content = str(summary)[:4000]
                # the full code, and the summary exactly as the model saw it
                trace.append({"tool": tu.name, "code": code, "summary": content})
                if tu.name == "final_answer":
                    # normalised now: a later tool call in this response shares the
                    # namespace and must not change the answer in place
                    final_result = None if value is None else df_to_result(value)
                    terminal = True
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": content})
            messages.append({"role": "user", "content": results})
            if terminal:
                cap_hit = False
                break

        rec["artifact"] = {"kind": "code_agent", "trace": trace,
                           "iters": attempts, "last_text": last_text}
        if cap_hit:
            rec["status"] = "timeout"
            rec["result"] = REFUSAL
            rec["error"] = f"max_retries {self.max_retries} hit"
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        if final_result is not None:
            rec["result"] = final_result
        elif is_decline_text(last_text):
            rec["status"] = "refused"
            rec["result"] = REFUSAL
        else:
            rec["status"] = "refused"
            rec["result"] = REFUSAL
        rec["wall_time_s"] = time.perf_counter() - t0
        return rec
