"""Declarative tools for the `planner_upfront` and `react` adapters.

EXACTLY the four planner primitives + ONE combined `order` step that mirrors the
Network's OrderSpec 1:1 (architect-decided fairness pick). All tool semantics
delegate to `gdc.primitives.run_primitive` so they are byte-identical to what
the Network would execute — the comparison is about ARCHITECTURE, not about
divergent primitive implementations.

Both adapters operate on a per-call "frame store" indexed by name; the register
starts at name 'register'; each tool reads a frame and produces a new frame.
A `final_answer` tool terminates and declares the answer.
"""
from __future__ import annotations

import pandas as pd

from gdc import primitives as P
from gdc.primitives import FilterTypeError


# ── tool schemas (Anthropic tool-use format) ────────────────────────────────

DECL_TOOLS = [
    {"name": "filter",
     "description": ("Keep rows matching ALL predicates (conjunction). Each "
                     "predicate is {col, op (==/!=/</<=/>/>=/in/contains), value}."),
     "input_schema": {"type": "object", "properties": {
         "input": {"type": "string", "description": "frame to read (default 'register')"},
         "predicates": {"type": "array", "items": {"type": "object", "properties": {
             "col":   {"type": "string"},
             "op":    {"type": "string",
                       "enum": ["==", "!=", "<", "<=", ">", ">=", "in", "contains"]},
             "value": {}},
             "required": ["col", "op", "value"]}}},
         "required": ["input", "predicates"]}},
    {"name": "select",
     "description": "Project (keep) the named columns, dropping the rest.",
     "input_schema": {"type": "object", "properties": {
         "input":   {"type": "string"},
         "columns": {"type": "array", "items": {"type": "string"}}},
         "required": ["input", "columns"]}},
    {"name": "aggregate",
     "description": ("Group by `group_by` (string or list), aggregate `value_col` "
                     "with `agg` (sum/mean/count/min/max). The output column "
                     "carries the value_col name. Set `group_by=[]` to aggregate "
                     "globally over all rows (produces a 1-row frame)."),
     "input_schema": {"type": "object", "properties": {
         "input":     {"type": "string"},
         "group_by":  {},
         "agg":       {"type": "string",
                       "enum": ["sum", "mean", "count", "min", "max"]},
         "value_col": {"type": "string"}},
         "required": ["input", "group_by", "agg", "value_col"]}},
    {"name": "compute",
     "description": ("Add new_col = eval(expr). expr is 'ColA OP ColB' over "
                     "existing columns (+ - * /) — for derived metrics like gap "
                     "or exposure."),
     "input_schema": {"type": "object", "properties": {
         "input":   {"type": "string"},
         "new_col": {"type": "string"},
         "expr":    {"type": "string"}},
         "required": ["input", "new_col", "expr"]}},
    {"name": "order",
     "description": ("ONE combined ordering step (sort + optional limit). Use "
                     "`order_by` = [{col, dir}] and optional `limit` for top-k."),
     "input_schema": {"type": "object", "properties": {
         "input":    {"type": "string"},
         "order_by": {"type": "array", "items": {"type": "object", "properties": {
             "col": {"type": "string"},
             "dir": {"type": "string", "enum": ["asc", "desc"]}},
             "required": ["col", "dir"]}},
         "limit":    {"type": "integer"}},
         "required": ["input", "order_by"]}},
    {"name": "final_answer",
     "description": ("Declare the named frame as the final answer and stop. "
                     "For a scalar, that frame is 1x1; for a list, it is the "
                     "frame produced by your last step."),
     "input_schema": {"type": "object", "properties": {"frame": {"type": "string"}},
                      "required": ["frame"]}},
]


# ── application: one tool call -> one frame update ───────────────────────────

def _apply_one(name: str, args: dict, frames: dict) -> tuple[str, str | None]:
    """Apply one declarative tool. Returns (summary_text, produced_frame_name|None)."""
    if name not in {"filter", "select", "aggregate", "compute", "order"}:
        return f"error: unknown tool {name!r}", None
    src_name = args.get("input")
    src = frames.get(src_name)
    if src is None:
        return f"error: unknown input frame {src_name!r}", None
    try:
        if name == "filter":
            out = P.run_primitive("select", src,
                                  {"predicates": args.get("predicates", [])})
        elif name == "select":
            out = P.run_primitive("project", src, {"columns": args.get("columns", [])})
        elif name == "compute":
            out = P.run_primitive("compute", src,
                                  {"new_col": args["new_col"], "expr": args["expr"]})
        elif name == "aggregate":
            gb = args.get("group_by")
            vc = args["value_col"]; agg = args["agg"]
            empty_gb = (gb is None) or (isinstance(gb, (list, tuple)) and len(gb) == 0)
            # S36 FIX 2a — delegate global aggregate to prim_aggregate (was: inline
            # pd.to_numeric path that silently produced NaN on non-numeric columns,
            # so react/code_agent's global avg(text_col) bypassed the typed-refusal
            # guarantee that the grouped path already had). Uniform now: every
            # non-count aggregate over a non-numeric column raises AggregateTypeError
            # through one code path, which each adapter maps to a clean refusal.
            if empty_gb:
                out = P.run_primitive("aggregate", src,
                                      {"group_by": [], "value_col": vc, "agg": agg})
            else:
                key = gb[0] if isinstance(gb, (list, tuple)) and len(gb) == 1 else gb
                out = P.run_primitive("aggregate", src,
                                      {"group_by": key, "value_col": vc, "agg": agg})
        elif name == "order":
            out = src
            ob = args.get("order_by") or []
            if ob:
                cols = [d["col"] for d in ob]
                asc  = [d["dir"] == "asc" for d in ob]
                out = P.run_primitive("sort", out, {"by": cols, "ascending": asc})
            limit = args.get("limit")
            if isinstance(limit, int) and limit >= 0:
                out = P.run_primitive("limit", out, {"n": limit})
    except FilterTypeError:
        raise            # typed signal — let the adapter map it to a clean refusal (cross-system even-handedness)
    except Exception as exc:                              # noqa: BLE001 — feed back
        return f"error applying {name}: {type(exc).__name__}: {exc}", None
    fname = f"f{len([k for k in frames if k.startswith('f')])}"
    frames[fname] = out
    return _summary(out, fname), fname


def _summary(frame: pd.DataFrame, name: str, n: int = 5) -> str:
    cols = [str(c) for c in frame.columns]
    try:
        head = frame.head(n).to_dict("records")
    except Exception:
        head = "<unprintable>"
    return (f"frame '{name}': {len(frame)} rows x {len(cols)} cols {cols}\n"
            f"head: {head}")


# ── tool-use loop helpers (shared with react adapter) ───────────────────────

def apply_decl_tool(name: str, args: dict, frames: dict) -> str:
    summary, _ = _apply_one(name, args, frames)
    return summary
