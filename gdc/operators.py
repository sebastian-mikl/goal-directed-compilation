"""Operator schema for the LLM-free forward planner.

Each operator declares pre(state) -> bool and eff(state) -> State. The four
primitives below are exactly the ones in the spec; order between them is NEVER
hardcoded — it emerges from preconditions during forward search (V1, V2).

  filter(col, op, val)            pre: col in columns AND grain==row
                                  eff: where += {col, op, val}

  select(cols)                    pre: cols ⊆ columns
                                  eff: columns := cols  (column-projection)

  aggregate(fn, col, by, out_col) pre: col in columns AND by ⊆ columns AND grain==row
                                  eff: grain := grouped_by(by);
                                       columns := by + [out_col];
                                       derive += {out_col: "fn(col)"}

  compute(new_col, expr)          pre: expr is in the compute grammar
                                       (gdc.primitives.arith_parse) AND every
                                       column it names ∈ columns
                                  eff: columns += new_col; derive += {new_col: expr}

Op instances are frozen dataclasses exposing .kind (str) and .params (dict) — the
shape `gdc.primitives.run_primitive(kind, frame, params)` already consumes.
Their value-correctness when executed is the executor's concern (out of scope
here per the step prompt).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable

from .state import State, Goal, effective_output_columns, normalise_op


# ── operator instances ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Op:
    """A planner-emitted operator instance. `.kind` + `.params_dict` is the
    shape the existing morphism executor consumes."""
    kind: str
    _params: tuple                       # sorted tuple of (key, json-able-value)

    @property
    def params(self) -> dict:
        return {k: v for k, v in self._params}

    params_dict = params  # alias for executor-style call sites

    def __repr__(self) -> str:
        return f"{self.kind}({dict(self._params)!r})"


def _make_op(kind: str, params: dict) -> Op:
    """Build a hashable Op from a params dict. Sequence params (lists/tuples) are
    frozen into tuples so the Op is hashable and equality-comparable."""
    items = []
    for k in sorted(params.keys()):
        v = params[k]
        if isinstance(v, (list, tuple)):
            v = tuple(v)
        items.append((str(k), v))
    return Op(kind, tuple(items))


# ── expression column-references (for compute.pre) ────────────────────────────

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_RESERVED = frozenset({"and", "or", "not", "in", "is", "True", "False", "None"})


def expr_refs(expr: str) -> set[str]:
    """Identifier-like tokens in `expr`. The planner treats expr as an opaque
    string at the value level; it only needs to know which columns are referenced
    so compute.pre can check existence. This is a syntactic scan, not a parse."""
    return {t for t in _TOKEN.findall(str(expr)) if t not in _RESERVED}


# ── primitives: pre/eff ──────────────────────────────────────────────────────

def filter_pre(state: State, params: dict) -> bool:
    if not (state.is_row_grain and params["col"] in state.column_names):
        return False
    # P21: an op the executor does not implement is not applicable, full stop.
    # Before this check the planner accepted any spelling (normalise_op passes
    # unknown ops through) and prim_select silently returned every row. The
    # executor's own declaration is consulted — one set, not a copy that can
    # drift — imported here, local to the check that needs it (the same
    # per-call pattern the aggregate fn check below uses).
    from gdc.primitives import (SUPPORTED_FILTER_OPS, NUMERIC_STATE_TYPES,
                                value_shape_ok, values_numeric_ok)
    op = normalise_op(params["op"])
    if op not in SUPPORTED_FILTER_OPS:
        return False
    # P54: the VALUE side, mirrored from the executor's own declarations (the
    # same per-call import pattern as the op set). The shape rule is decidable
    # from the Goal alone; the type rule reads the State's declared column
    # type, so a numeric column compared with a non-number — `x == "max(x)"`,
    # the P33 spelling — is refused at plan time instead of executing to a
    # confident empty answer. A derived column is typed "unknown" and is left
    # to the executor, which checks the frame's dtype. Ordered ops with a
    # non-numeric value stay a typed refusal at execution (P60, untouched).
    val = params["val"]
    if not value_shape_ok(op, val):
        return False
    ctype = dict(state.columns).get(str(params["col"]))
    if ctype in NUMERIC_STATE_TYPES and op in ("==", "!=", "in") and not values_numeric_ok(op, val):
        return False
    # Idempotent: re-applying an already-present conjunct is a semantic no-op. Refuse
    # it so an unreachable goal exhausts the finite operator universe instead of
    # stacking duplicate conjuncts into ever-new states (expansion-cap blowout). The
    # conjunct is keyed exactly as filter_eff stores it (col, normalised op, val).
    conjunct = (str(params["col"]), op, val)
    return conjunct not in state.where


def filter_eff(state: State, params: dict) -> State:
    col = str(params["col"])
    op = normalise_op(params["op"])
    val = params["val"]
    return replace(state, where=state.where + ((col, op, val),))


def select_pre(state: State, params: dict) -> bool:
    return set(map(str, params["cols"])) <= state.column_names


def select_eff(state: State, params: dict) -> State:
    type_by_name = {n: t for n, t in state.columns}
    new_cols = tuple((str(c), type_by_name.get(str(c), "unknown")) for c in params["cols"])
    return replace(state, columns=new_cols)


def aggregate_pre(state: State, params: dict) -> bool:
    # P23: an out_col that IS one of the group keys asks one column to hold
    # both the key and the aggregate. No output satisfies that, and executing
    # it produced a frame with DUPLICATE labels (the post-rename in
    # op_to_primitive collided with the key) rather than an error.
    if str(params["out_col"]) in set(map(str, params["by"])):
        return False
    if not state.is_row_grain:
        return False
    if str(params["col"]) not in state.column_names:
        return False
    # P22: an fn the executor does not implement is not applicable. The same
    # alias table op_to_primitive uses at dispatch, so plan-time and run-time
    # agree on which spellings are runnable. Imported local to the check, as
    # filter_pre does for its op set.
    from gdc.primitives import SUPPORTED_AGG_FNS, AGG_FN_ALIASES
    fn = str(params["fn"])
    if AGG_FN_ALIASES.get(fn, fn) not in SUPPORTED_AGG_FNS:
        return False
    return set(map(str, params["by"])) <= state.column_names


def aggregate_eff(state: State, params: dict) -> State:
    fn = str(params["fn"])
    col = str(params["col"])
    by = tuple(map(str, params["by"]))
    out_col = str(params["out_col"])
    new_cols = tuple((c, "unknown") for c in by) + ((out_col, "unknown"),)
    new_derive = state.derive + ((out_col, f"{fn}({col})"),)
    return replace(state, grouped_by=by, columns=new_cols, derive=new_derive)


def compute_pre(state: State, params: dict) -> bool:
    """The planner admits exactly what the executor evaluates: the expression must
    parse under the ONE compute grammar (arith_parse, shared with safe_eval_expr)
    and every column it names must exist in the state. Was: `expr_refs(expr) <=
    columns`, a token scan that admitted chained and single-column expressions the
    one-operator executor could not evaluate, so the compute failed open (P15b).
    Imported local to the check, as filter_pre and aggregate_pre do."""
    from gdc.primitives import arith_parse
    parsed = arith_parse(str(params["expr"]), state.column_names)
    return parsed is not None and parsed.refs <= state.column_names


def compute_eff(state: State, params: dict) -> State:
    new_col = str(params["new_col"])
    expr = str(params["expr"])
    new_cols = state.columns + ((new_col, "unknown"),)
    new_derive = state.derive + ((new_col, expr),)
    return replace(state, columns=new_cols, derive=new_derive)


@dataclass(frozen=True)
class Operator:
    kind: str
    pre: Callable[[State, dict], bool]
    eff: Callable[[State, dict], State]


OPERATORS: dict[str, Operator] = {
    "filter":    Operator("filter",    filter_pre,    filter_eff),
    "select":    Operator("select",    select_pre,    select_eff),
    "aggregate": Operator("aggregate", aggregate_pre, aggregate_eff),
    "compute":   Operator("compute",   compute_pre,   compute_eff),
}


def apply(op: Op, state: State) -> State:
    """Apply an operator instance via its registered eff. The plan-application
    helper used by the search and by tests verifying terminal-state satisfies."""
    return OPERATORS[op.kind].eff(state, op.params)


def pre_holds(op: Op, state: State) -> bool:
    return OPERATORS[op.kind].pre(state, op.params)


# ── per-goal operator universe (canonical enumeration) ───────────────────────

_AGG_FN_OF_COL = re.compile(r"^\s*([A-Za-z_]\w*)\s*\(\s*([A-Za-z_]\w*)\s*\)\s*$")


def _classify_derive(expr: str) -> tuple[str, str] | None:
    """If `expr` looks like a single 'fn(col)' aggregate, return (fn, col).
    Otherwise None (treat as a compute expression)."""
    m = _AGG_FN_OF_COL.match(str(expr))
    return (m.group(1), m.group(2)) if m else None


def enumerate_ops(goal: Goal) -> list[Op]:
    """The canonical operator universe for a goal. Each Op carries enough
    parameters to fire its `eff`; only ops whose params are *justified by the
    goal* are emitted, so the search space is bounded.

    Canonical order (V4/V5 determinism):
      1. all filter ops, sorted by (col, op, repr(val));
      2. all aggregate ops (from `fn(col)` derive entries), sorted by out_col;
      3. all compute ops (from non-`fn(col)` derive entries), sorted by new_col;
      4. one select op for the goal columns (covers the final projection case).
    """
    ops: list[Op] = []
    # filters
    for col, op, val in sorted(((str(c), normalise_op(o), v) for c, o, v in goal.where),
                               key=lambda x: (x[0], x[1], repr(x[2]))):
        ops.append(_make_op("filter", {"col": col, "op": op, "val": val}))
    # aggregates + computes (from derive entries)
    agg, comp = [], []
    # A PRESENT grouped_by — even an empty [] (global aggregate over all rows) —
    # routes a fn(col) derive to the aggregate path; only an ABSENT grouped_by
    # (None == row grain) routes it to compute. `by` is () for the global case,
    # so the emitted aggregate op carries by=[] (collapse all rows into one group).
    grouped = goal.grouped_by is not None
    by = tuple(goal.grouped_by) if goal.grouped_by else ()
    for out_col, expr in goal.derive:
        cls = _classify_derive(expr)
        if cls is not None and grouped:
            fn, col = cls
            agg.append(_make_op("aggregate",
                                {"fn": fn, "col": col, "by": list(by), "out_col": str(out_col)}))
        else:
            comp.append(_make_op("compute", {"new_col": str(out_col), "expr": str(expr)}))
    ops.extend(sorted(agg, key=lambda o: o.params["out_col"]))
    ops.extend(sorted(comp, key=lambda o: o.params["new_col"]))
    # Final-projection select on the goal's EFFECTIVE output columns (goal.columns,
    # plus the grain keys for grouped goals — see state.effective_output_columns;
    # sorted so order-of-input doesn't bite). Since satisfaction is exact column
    # equality (S42), this op is what lets a plan drop unrequested / intermediate
    # columns; a goal with an empty effective set gets no select op and is
    # honestly Unreachable (no declared output to produce).
    eff_cols = effective_output_columns(goal)
    if eff_cols:
        ops.append(_make_op("select", {"cols": sorted(eff_cols)}))
    return ops
