"""Text-to-SQL adapter — the LLM emits ONE SELECT against a sqlite mirror of the
register; we execute it. ~1 LLM call per query.

SQL execution errors are treated as REFUSAL (status=refused) — silent_wrong
cannot arise from a syntax error. The model is told to emit nothing but the SQL.
"""
from __future__ import annotations

import re
import time

from ..briefing import render_briefing
from ..tools_sql import SQL_TOOLS, run_sql
from ..types import REFUSAL
from .base import Adapter, df_to_result, is_decline_text, is_transport_error, new_result


SQL_SYSTEM = """\
You answer an analytical query over a single sqlite table `register` ({n_rows} rows).
Emit EXACTLY ONE SELECT statement via the run_sql tool. Quote column names with
spaces / parens like "Unit Price (Net)". No commentary outside the tool call.

If the query genuinely cannot be answered from this table, reply with plain text
explaining you cannot, instead of emitting SQL.

REGISTER SCHEMA + DOMAIN CONTEXT:
{briefing}

TABLE COLUMNS (verbatim — sqlite identifiers, quote when they contain spaces/parens):
{columns}
"""


class TextToSQLAdapter(Adapter):
    name = "text_to_sql"

    def _build_system(self) -> str:
        return SQL_SYSTEM.format(
            n_rows=len(self.register),
            briefing=render_briefing(self.briefing),
            columns=", ".join(repr(c) for c in self.briefing["columns"]),
        )

    def cache_tools(self):
        return SQL_TOOLS

    def run(self, query: str, seed: int) -> dict:
        rec = new_result()
        t0 = time.perf_counter()
        messages = [{"role": "user", "content": f"QUERY: {query}"}]
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=self.system_text,
                tools=SQL_TOOLS, messages=messages,
                **self._temp_kwargs(),
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

        blocks = list(resp.content)
        tool_uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
        text = "\n".join(getattr(b, "text", "") for b in blocks
                        if getattr(b, "type", None) == "text")
        rec["artifact"] = {"kind": "text_to_sql", "raw_text": text}

        if not tool_uses:
            # text-only reply -> either a decline or no parseable result
            rec["status"] = "refused"
            rec["result"] = REFUSAL
            rec["error"] = "no SQL emitted" + (" (declined)" if is_decline_text(text) else "")
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        tu = tool_uses[0]
        sql = dict(tu.input).get("sql", "")
        rec["artifact"]["sql"] = sql
        summary, frame, err = run_sql(sql, self.register, timeout_s=self.timeout_s)
        rec["artifact"]["exec_summary"] = summary[:400]
        if err is not None:
            rec["status"] = "refused"        # SQL failure -> refusal, not silent_wrong
            rec["result"] = REFUSAL
            rec["error"] = f"sql error: {err}"
            rec["wall_time_s"] = time.perf_counter() - t0
            return rec
        rec["result"] = df_to_result(frame)
        rec["wall_time_s"] = time.perf_counter() - t0
        return rec
