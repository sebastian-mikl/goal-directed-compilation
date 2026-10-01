"""S13 Part C — wire the goal→plan path through the LIVE executor (join-free).

Path: NL → solve → plan (planner ops) → execute via the executor's primitives →
apply the OrderSpec as the FINAL step via the executor's OWN sort/limit (reuse;
these are NOT planner ops and never enter A*).

Two adapters are needed and contained HERE (the planner core and the executor are
both unchanged):
  - vocabulary: planner `filter`→executor `select`(predicates); planner `select`
    (projection)→executor `project`; planner `aggregate`→executor `aggregate` with
    `avg`→`mean` and a rename of the output (the executor names it after value_col,
    the planner declares a separate out_col).
  - schema view: real columns like "Unit Price (Net)" are aliased to simple identifiers
    so `expr_refs`/`safe_eval_expr` work; the scorer is label-agnostic, so the
    aliasing is invisible to correctness.
"""
from __future__ import annotations

import re

import pandas as pd

from gdc.primitives import run_primitive, AGG_FN_ALIASES
from .operators import apply
from .order import OrderSpec
from .state import State

# P22: the planner->executor fn spelling table now lives beside the executor's
# declared fn set (primitives.SUPPORTED_AGG_FNS) so aggregate_pre can consult
# the same table this dispatch uses. Name kept; one call site below.
_AGG_FN_MAP = AGG_FN_ALIASES


# ── simple-identifier schema view ────────────────────────────────────────────

def alias_name(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip().lower()).strip("_")
    return s or "col"


def categorical_hints(aliased_df: pd.DataFrame, max_card: int = 15) -> dict:
    """{alias: [distinct values]} for low-cardinality non-numeric columns — the
    categorical vocabulary LLM1 needs so its `==`/`in` filters actually match
    (e.g. status is "Open High/Medium/...", never a bare "Open")."""
    hints: dict = {}
    for c in aliased_df.columns:
        s = aliased_df[c]
        if pd.api.types.is_numeric_dtype(s):
            continue
        vals = s.dropna().astype(str).unique()
        if 1 <= len(vals) <= max_card:
            hints[str(c)] = sorted(vals)
    return hints


def aliased_view(register: pd.DataFrame):
    """Return (alias_map real->simple, aliased_df, schema {simple: type})."""
    amap: dict = {}
    seen: dict = {}
    for c in register.columns:
        a = alias_name(c)
        if a in seen:
            seen[a] += 1
            a = f"{a}_{seen[a]}"
        else:
            seen[a] = 0
        amap[c] = a
    df = register.rename(columns=amap)
    schema = {amap[c]: _declared_type(register[c]) for c in register.columns}
    return amap, df, schema


def _declared_type(col: pd.Series) -> str:
    """The State type of a register column. A bool column is "bool", not
    "float": pandas counts bool as numeric, but prim_select compares bool
    columns by string form (P54), so declaring them "float" would make
    filter_pre refuse `flag == True` at plan time while the executor runs it."""
    if pd.api.types.is_bool_dtype(col):
        return "bool"
    return "float" if pd.api.types.is_numeric_dtype(col) else "str"


# ── planner op -> executor primitive call ────────────────────────────────────

def op_to_primitive(op):
    """Map one planner Op to (executor_kind, params, post) where `post` is an
    optional frame->frame adapter applied after run_primitive."""
    p = op.params
    if op.kind == "filter":
        return "select", {"predicates": [{"col": p["col"], "op": p["op"], "value": p["val"]}]}, None
    if op.kind == "select":                       # planner select = column projection
        return "project", {"columns": list(p["cols"])}, None
    if op.kind == "compute":
        return "compute", {"new_col": p["new_col"], "expr": p["expr"]}, None
    if op.kind == "aggregate":
        by = list(p["by"])
        gb = by[0] if len(by) == 1 else by
        vc, out_col = p["col"], p["out_col"]
        fn = _AGG_FN_MAP.get(p["fn"], p["fn"])

        def post(frame, vc=vc, out_col=out_col, keys=tuple(map(str, by))):
            frame = _rename_value_col(frame, vc, out_col)
            # P57. When the value column
            # is itself a group key, prim_aggregate keeps the key under
            # `{key}__group` so the aggregate can sit under value_col without
            # a duplicate label. Once the aggregate is under out_col — which is
            # never a key (P23) — the stand-in has done its job: give the key
            # its own name back, the one the State, the Goal and every
            # downstream consumer (OrderSpec, a later select) uses.
            # Was: the frame kept `{key}__group` while the State said `key`;
            # an OrderSpec on the key was refused and a projection naming it
            # would have dropped it. The primitive is unchanged: the baseline
            # adapters call it directly and the comparator strips the suffix
            # for them.
            restore = {f"{k}__group": k for k in keys
                       if f"{k}__group" in frame.columns and k not in frame.columns}
            return frame.rename(columns=restore) if restore else frame

        def _rename_value_col(frame, vc, out_col):
            if vc != out_col and vc in frame.columns:
                if out_col in frame.columns:
                    # P23: the only other column here is a group key (or its
                    # `{key}__group` stand-in), so renaming would yield two
                    # columns labelled out_col — a frame pandas then silently
                    # truncates on serialisation. aggregate_pre refuses this at
                    # plan time; this is the executor's own guard.
                    from gdc.primitives import AggregateTypeError
                    raise AggregateTypeError(
                        f"aggregate out_col {out_col!r} collides with group key "
                        f"{out_col!r}: one column cannot hold both the key and "
                        f"the aggregate. Refusing rather than producing duplicate "
                        f"labels.")
                return frame.rename(columns={vc: out_col})
            return frame
        return "aggregate", {"group_by": gb, "value_col": vc, "agg": fn}, post
    raise ValueError(f"no executor mapping for planner op kind {op.kind!r}")


class PlanExecutionDivergence(Exception):
    """An executed step produced a frame whose shape is not the State the planner
    derived for that step: its column set differs from the planned columns, or a
    grouped state's keys are not unique (a global aggregate: more than one row;
    checked while every planned key is in the frame).
    The planner proved the plan against the State; a frame that disagrees means a
    primitive did something other than what the operator's effect declares
    (dropped, leaked or never created a column), so nothing after that step can
    be trusted. Raised AT THAT STEP, instead of relying on a later order key or
    the comparator to notice. It checks shape, not values: a primitive that
    returns the planned columns with wrong values is not caught here. Adapters
    treat it as a typed refusal."""


_GROUP_SUFFIX = "__group"


def _executed_columns(frame: pd.DataFrame, state: State) -> list[str]:
    """The frame's column names, read in the planner's vocabulary. prim_aggregate
    keeps a group key that is also the aggregated column under '{key}__group'
    (it cannot emit two columns with one name); that column IS the planned key."""
    present = {str(c) for c in frame.columns}
    keys = set(map(str, state.grouped_by or ()))
    out = []
    for c in map(str, frame.columns):
        base = c[:-len(_GROUP_SUFFIX)] if c.endswith(_GROUP_SUFFIX) else None
        out.append(base if base in keys and base not in present else c)
    return out


def check_step(op, state: State, frame: pd.DataFrame, step: int) -> None:
    """Planner/executor agreement after one executed op (see PlanExecutionDivergence)."""
    names = _executed_columns(frame, state)
    got, want = set(names), state.column_names
    if got != want:
        raise PlanExecutionDivergence(
            f"step {step} ({op.kind}): executed frame columns {sorted(got)} != planned "
            f"{sorted(want)} (missing {sorted(want - got)}, unexpected {sorted(got - want)})")
    if state.grouped_by is not None:
        keys = list(map(str, state.grouped_by))
        if not keys:
            if len(frame) > 1:
                raise PlanExecutionDivergence(
                    f"step {step} ({op.kind}): global aggregate grain but the executed "
                    f"frame has {len(frame)} rows")
        elif all(k in names for k in keys):
            # Only checkable while every planned key is in the frame. A select that
            # keeps a strict subset of them (select_pre admits it) leaves rows that
            # are unique on all the keys but may repeat on the kept ones; the
            # executor did what the State says. Was: the check ran on the kept
            # keys and raised a false PlanExecutionDivergence.
            real = [frame.columns[names.index(k)] for k in keys]
            if frame.duplicated(subset=real).any():
                raise PlanExecutionDivergence(
                    f"step {step} ({op.kind}): grouped by {keys} but the executed frame's "
                    f"group keys are not unique")


def _run_checked(plan_ops, frame: pd.DataFrame):
    """Execute plan_ops from `frame`, checking every step against the State the
    planner's own operator effects derive from the frame's columns. Returns
    (frame, state after the last op)."""
    state = State.from_base([(str(c), "unknown") for c in frame.columns])
    for i, op in enumerate(plan_ops):
        kind, params, post = op_to_primitive(op)
        frame = run_primitive(kind, frame, params)
        if post is not None:
            frame = post(frame)
        state = apply(op, state)
        check_step(op, state, frame, i)
    return frame, state


def execute_plan(plan_ops, frame: pd.DataFrame) -> pd.DataFrame:
    """Run the planner's operator sequence through the executor primitives,
    refusing (PlanExecutionDivergence) at the first step whose frame does not
    match the planned State."""
    return _run_checked(plan_ops, frame)[0]


def apply_order_via_executor(frame: pd.DataFrame, order: OrderSpec) -> pd.DataFrame:
    """Realise the OrderSpec as the FINAL step using the executor's own sort/limit
    primitives (reuse; never planner ops, never in A*)."""
    if order.order_by:
        cols = [c for c, _ in order.order_by]
        asc = [d == "asc" for _, d in order.order_by]
        frame = run_primitive("sort", frame, {"by": cols, "ascending": asc})
    if order.limit is not None:
        frame = run_primitive("limit", frame, {"n": order.limit})
    return frame


class OrderApplicationError(Exception):
    """The OrderSpec references a column the executed plan's output no longer
    carries, and the plan's shape offers no sound point to apply the ordering.
    Raised instead of sorting on a missing key (KeyError -> crash) or silently
    skipping the requested ordering (wrong answer). Adapters treat it as a
    typed refusal."""


def execute_plan_ordered(plan_ops, frame: pd.DataFrame, order: OrderSpec) -> pd.DataFrame:
    """Execute a plan and apply the OrderSpec — BEFORE the terminal projection
    when the projection drops a sort key.

    validate_order accepts order_by columns that live only in goal.derive
    (a derived gap, an aggregate's out_col): legitimate sort keys the executor
    produces mid-plan. Under exact-column satisfaction (S42) the terminal
    select projects those keys OUT of the declared output, so sorting after
    the full plan would fail. Ordering is presentation over the result ROWS,
    and projection never changes which rows exist or their order — so sorting
    the pre-projection frame and then projecting is semantics-identical to
    the old sort-last wiring whenever both are possible.

    Three cases:
      1. terminal frame still carries every order_by column -> sort/limit last
         (byte-identical to the historical path);
      2. the plan ends with the terminal select and the pre-select frame
         carries the order_by columns -> sort/limit the wide frame, then
         project to the select's columns;
      3. anything else (no satisfying plan produces this shape today) ->
         OrderApplicationError, an honest typed refusal — never a KeyError
         crash, never an unordered answer passed off as ordered.

    Every executed step, the terminal select of case 2 included, is checked
    against the planned State (PlanExecutionDivergence).
    """
    order_cols = {str(c) for c, _ in order.order_by}
    if order_cols and plan_ops and plan_ops[-1].kind == "select":
        sel_cols = list(plan_ops[-1].params["cols"])
        if not order_cols <= set(map(str, sel_cols)):        # projection drops a key
            wide, wide_state = _run_checked(plan_ops[:-1], frame)
            if not order_cols <= set(map(str, wide.columns)):
                raise OrderApplicationError(
                    f"order_by column(s) "
                    f"{sorted(order_cols - set(map(str, wide.columns)))} not "
                    f"produced by the plan — no sound application point")
            wide = apply_order_via_executor(wide, order)
            out = run_primitive("project", wide, {"columns": sel_cols})
            check_step(plan_ops[-1], apply(plan_ops[-1], wide_state), out,
                       len(plan_ops) - 1)
            return out
    terminal = execute_plan(plan_ops, frame)
    if not order_cols <= set(map(str, terminal.columns)):
        raise OrderApplicationError(
            f"order_by column(s) {sorted(order_cols - set(map(str, terminal.columns)))} "
            f"not present in the plan's output and no sound pre-projection "
            f"application point exists")
    return apply_order_via_executor(terminal, order)


def run_goal_plan(goal, order: OrderSpec, aliased_register: pd.DataFrame, *, base_schema):
    """Execute a (Goal, OrderSpec) end-to-end over the aliased register. Returns the
    produced frame. Deterministic: zero LLM calls (the LLM was only the prior
    goal-emission step)."""
    from .search import plan, Unreachable
    from .state import State
    p = plan(State.from_base(base_schema), goal)
    if isinstance(p, Unreachable):
        return None, p
    frame = execute_plan_ordered(p, aliased_register, order)
    return frame, p
