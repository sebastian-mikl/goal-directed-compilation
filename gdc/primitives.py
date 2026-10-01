"""Deterministic tabular primitives (SPEC.md Section 3, Phase 1).

Each primitive is a plain, hand-written, deterministic Python function of the
form `fn(frame, params) -> frame` — NO LLM in the execution path. The variety
lives entirely in `params`; the kinds are fixed and small (relational algebra).

Each registry entry carries:
  - fn       : the deterministic transform (frame, params) -> frame
  - contract : a short human description of the declared output schema
  - verify   : a VALUE-LEVEL check (params, source_frame, out_frame) -> (ok, reason)
               (SPEC 3.2) — presence is NOT enough. compute's new_col must equal
               eval(expr); aggregate's output must equal a recomputed group_by/agg;
               the others assert the declared output schema / structural post-condition.

`select` is the SPEC's `filter`. It accepts an explicit `predicates=[{col,op,value}]`
list (the domain-agnostic form the goal→plan path emits) AND, for the op-set BASELINE
only, a fixed col_map vocabulary (`_SELECT_COLMAP`) — see the note on that table below.
`write` (persist + pass-through) is included because the S7 corpus needs it; `project`
is included to complete the relational basis.

State identity (SPEC 2.1 / 7) = the verify signature: `state_fingerprint` hashes
(kind, normalised params, a content hash of the output). This is the reproducible,
single-valued key used to fingerprint a produced state.
"""
from __future__ import annotations

import ast
import functools
import hashlib
import json
import keyword
import re
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

_RTOL = 1e-5
_ATOL = 1e-3   # money-scale tolerance

# ── what the executor implements, declared ONCE ─────────────────────────────
# The planner's `_pre` functions (gdc/operators.py) consult these so that what
# A* will plan and what the executor can run are the same set BY CONSTRUCTION,
# not two hand-maintained copies. P15 was the cost of letting them drift:
# `compute_pre` admitted expressions `prim_compute` could not evaluate, and the
# primitive failed open. P21/P22 found the same gap for filter ops and aggregate
# fns.
#
# Keep SUPPORTED_FILTER_OPS in step with the op chain in prim_select;
# evidence/tests/test_edge_p21_filter_op.py locks it.
SUPPORTED_FILTER_OPS: frozenset[str] = frozenset(
    {"==", "!=", "<", "<=", ">", ">=", "in", "contains"})

# P53/P54. The VALUE side of a filter,
# declared once and read by both prim_select (on the frame's dtype) and
# filter_pre (on the State's declared type), the way SUPPORTED_FILTER_OPS is:
#   - `==`, `!=`, `contains` take one scalar; `in` takes a list.
#   - against a numeric column, `==`/`!=`/`in` values must be numeric, and the
#     comparison IS numeric (was: astype(str) == str(val), which made
#     `x == "max(x)"` a confident empty answer — P33 — and `x == 4` on a float
#     column no match at all — P46).
#   - `contains` is a case-insensitive LITERAL substring match, the schema's
#     `match_strategy: contains` (was: a regular expression by pandas' default).
#   - a null value (JSON null, Python None) under `==` / `!=` is a null test:
#     `== None` keeps the null rows (isna), `!= None` the non-null rows (notna),
#     on a column of any type. JSON null is the only spelling: it is the one the
#     recorded plans use (planner and react `!= None`, nothing else), and a
#     string such as "None" or "null" stays an ordinary value, since a
#     categorical can hold it. Was: `!= None` compared string forms,
#     which dropped no NaN row; the P54 back-port then refused every null value.
SCALAR_OPS: frozenset[str] = frozenset({"==", "!=", "contains"})
LIST_OPS: frozenset[str] = frozenset({"in"})
NULL_TEST_OPS: frozenset[str] = frozenset({"==", "!="})
NUMERIC_STATE_TYPES: frozenset[str] = frozenset({"float", "int", "number"})   # the planner's declared types


def is_null_test(op: str, val) -> bool:
    """`col == None` / `col != None`: a null test, not a value comparison."""
    return val is None and op in NULL_TEST_OPS


def value_shape_ok(op: str, val) -> bool:
    """Is `val` the right SHAPE for `op`? Decidable from the Goal alone."""
    if val is None:
        return is_null_test(op, val)
    if op in SCALAR_OPS:
        return not isinstance(val, (list, tuple, set, dict))
    if op in LIST_OPS:
        return isinstance(val, (list, tuple, set))
    return True


def numeric_value(val) -> float | None:
    """`val` as a float if it can be one, else None. Bools are not numbers here."""
    if isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def values_numeric_ok(op: str, val) -> bool:
    """For a NUMERIC column: every value `op` compares must be numeric. A null
    test compares no value."""
    if is_null_test(op, val):
        return True
    if op in ("==", "!=", "<", "<=", ">", ">="):
        return numeric_value(val) is not None
    if op in LIST_OPS:
        return isinstance(val, (list, tuple, set)) and all(numeric_value(v) is not None for v in val)
    return True


class FilterTypeError(ValueError):
    """A filter compares a column to a value that can't be coerced to the needed
    type (e.g. `Cost > "average"`). A TYPED signal so callers can map it to a
    refusal — NOT a crash, and NOT a silent all-rows/empty result."""


class FilterOpError(FilterTypeError):
    """P21 — a comparison op the executor does not implement.

    `prim_select` used to have no `else`: an op outside its if/elif chain
    (`startswith`, `like`, `is`, …) matched nothing, filtered nothing, and the
    frame came back with EVERY row — a confidently wrong answer. The planner
    let it through because `filter_pre` checked the column and never the op,
    and `normalise_op` passes unknown spellings through unchanged.

    Belt and braces: `filter_pre` now refuses these at plan time, so from a
    Goal the outcome is `Unreachable`; this error is the primitive's own guard
    for any caller that reaches it directly (tools_decl._apply_one, tests).

    Subclasses FilterTypeError for the reason ComputeExprError and
    AggregateTypeError do: `_apply_one` re-raises that family and swallows
    everything else into an agent-visible string, so the subclass buys
    even-handed cross-system refusal with no adapter edits."""


class FilterColumnError(FilterTypeError):
    """P26 — a predicate names a column the frame does not have.

    `prim_select` used to `continue` past it: the predicate filtered nothing
    and every row came back, exactly the P21 shape one field over.
    `filter_pre` already refuses an absent column at plan time, so from a Goal
    the outcome is `Unreachable`; this is the primitive's own guard for any
    caller that reaches it directly (tools_decl._apply_one, tests). Same
    family, same propagation argument as FilterOpError."""


class ProjectColumnError(FilterTypeError):
    """P58: a projection names a column the frame does not have, or names no
    columns. Was: the missing column was dropped from the projection silently,
    and an empty or non-list request returned every column — the shape P26
    fixed in `filter` ("the predicate was dropped and every row came back").
    FilterColumnError's sibling: same family, same adapter mappings, a
    different operator in the message."""


class SortColumnError(FilterTypeError):
    """A sort names a key the frame does not have.

    `prim_sort` used to drop such a key and sort by the rest, or return the frame
    unsorted when none was left, so a following limit took a top-k of the wrong
    rows: the shape P26 fixed in `filter` and P58 in `project`. GDC never reaches
    it (validate_order admits only output columns as sort keys); planner and react
    do, through the `order` tool. Same family, same adapter mappings."""


class ComputeExprError(FilterTypeError):
    """P15a — a compute expression the executor cannot evaluate: one outside the
    compute grammar (`arith_parse` below), one that names a column the frame
    does not have, or one with an operand that has no numeric content (P56).

    `prim_compute` used to FAIL OPEN on an expression it could not evaluate: it
    returned the frame unchanged, so a declared column was silently never
    created, or an existing one silently kept its old value. The evaluator then
    recognised exactly one binary operator between two columns while
    `compute_pre` admitted any expression whose identifiers were columns, so A*
    planned steps the executor could not run. Both now read the one grammar
    (P15b), and raising here turns any remaining disagreement into a refusal at
    the point of failure.

    Subclassing FilterTypeError is deliberate, for the same reason
    AggregateTypeError does: `tools_decl._apply_one` re-raises FilterTypeError
    and swallows every other exception into an agent-visible feedback string, so
    subclassing buys even-handed cross-system refusal (network -> refused,
    react -> graceful observation) with NO adapter edits. A bare Exception would
    refuse in some systems and read as an error string in others — exactly the
    unevenness test_adapter_refusal_consistency.py regression-locks."""


class AggregateTypeError(FilterTypeError):
    """S36 FIX 2a — a non-count aggregate is applied to a column that cannot be
    coerced to numeric (e.g. avg(Title)), so the engine would silently produce NaN
    rows. A TYPED signal mirroring FilterTypeError so the existing adapter mappings
    (network/planner refusal, react graceful observation, code_agent/text_to_sql
    via the shared `_apply_one` re-raise) treat it as a clean refusal — refuse,
    don't approximate. Subclassing FilterTypeError is deliberate: no adapter edits
    needed, the rule applies uniformly across all systems that share `_apply_one`.

    Also raised (P22) for an aggregate fn the executor does not implement — the
    same refuse-don't-approximate signal, and previously an untyped
    AttributeError from inside pandas."""


# ── the compute grammar (ONE grammar: planner compute_pre + this executor) ────
#
#   expr ::= expr ('+' | '-' | '*' | '/') expr | '-' expr | '+' expr | '(' expr ')'
#          | <numeric literal> | <column name>
#
# Standard precedence; '/' is true division. A column name that is not a plain
# Python identifier (the baselines' raw names, e.g. 'Base Pay (USD)') is read by
# column-aware tokenisation: known names are matched longest-first at identifier
# boundaries before parsing, so a name containing an operator character is one
# operand, never split. No function calls, attribute access, comparisons,
# strings or other Python.
#
# `arith_parse` is the single definition. compute_pre (gdc/operators.py)
# admits a compute iff arith_parse accepts it and every column it names exists;
# validate_goal (gdc/contract.py) refuses a derive outside it; safe_eval_expr
# evaluates exactly what it accepts. Before this, the evaluator recognised one
# `colA OP colB` while compute_pre admitted any expression whose identifiers were
# columns, and the gap (chained sums, single-column references) failed open.

_ARITH_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)
_COL_PLACEHOLDER = "__gdc_col_{}__"
_IDENT_CHAR = re.compile(r"[0-9A-Za-z_]")


@dataclass(frozen=True)
class ArithExpr:
    """A parsed compute expression. `refs` are the column names it reads (real
    names, placeholders resolved); `tree` is the checked AST."""
    tree: ast.Expression
    refs: frozenset
    _names: tuple                      # ((ast Name id, real column name), ...)

    def column_of(self, name_id: str) -> str:
        return dict(self._names).get(name_id, name_id)


def _plain_identifier(name: str) -> bool:
    return name.isidentifier() and not keyword.iskeyword(name)


def _bounded(s: str, start: int, end: int) -> bool:
    """True when s[start:end] is not glued to an identifier character on either side."""
    before = s[start - 1] if start > 0 else ""
    after = s[end] if end < len(s) else ""
    return not (before and _IDENT_CHAR.match(before)) and not (after and _IDENT_CHAR.match(after))


def _protect_columns(expr: str, special: tuple) -> tuple[str, dict]:
    """Replace every bounded occurrence of a non-identifier column name with a
    placeholder identifier, longest name first. Returns (rewritten expr,
    {placeholder: real name})."""
    names = sorted(special, key=lambda c: (-len(c), c))
    if not names:
        return expr, {}
    out, mapping, by_name, i = [], {}, {}, 0
    while i < len(expr):
        for c in names:
            if expr.startswith(c, i) and _bounded(expr, i, i + len(c)):
                ph = by_name.setdefault(c, _COL_PLACEHOLDER.format(len(by_name)))
                mapping[ph] = c
                out.append(f" {ph} ")
                i += len(c)
                break
        else:
            out.append(expr[i])
            i += 1
    return "".join(out), mapping


def _numeric_literal(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return bool(np.isfinite(float(value)))
    except OverflowError:
        return False


@functools.lru_cache(maxsize=4096)
def _arith_parse_cached(expr: str, special: tuple) -> ArithExpr | None:
    if "__gdc_col_" in expr:
        return None                     # reserved placeholder spelling
    text, mapping = _protect_columns(expr, special)
    text = text.strip()
    if not text:
        return None
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    refs, names = set(), {}
    for node in ast.walk(tree):
        if not isinstance(node, _ARITH_NODES):
            return None
        if isinstance(node, ast.Constant) and not _numeric_literal(node.value):
            return None
        if isinstance(node, ast.Name):
            real = mapping.get(node.id, node.id)
            names[node.id] = real
            refs.add(real)
    return ArithExpr(tree=tree, refs=frozenset(refs), _names=tuple(sorted(names.items())))


def arith_parse(expr, columns=()) -> ArithExpr | None:
    """Parse `expr` under the compute grammar, or None when it is outside it.
    `columns` are the known column names; only those that are not plain
    identifiers matter (each is tokenised as one operand). Pure syntax: whether
    each referenced column exists is the caller's check (`refs`)."""
    if not isinstance(expr, str):
        return None
    special = tuple(sorted({str(c) for c in columns
                            if not _plain_identifier(str(c)) and str(c) in expr}))
    return _arith_parse_cached(expr, special)


def _eval_arith(node, operands: dict, parsed: ArithExpr):
    if isinstance(node, ast.Expression):
        return _eval_arith(node.body, operands, parsed)
    if isinstance(node, ast.BinOp):
        a = _eval_arith(node.left, operands, parsed)
        b = _eval_arith(node.right, operands, parsed)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        return a / b
    if isinstance(node, ast.UnaryOp):
        v = _eval_arith(node.operand, operands, parsed)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.Name):
        return operands[parsed.column_of(node.id)]
    return np.float64(node.value)        # numpy scalar: x / 0 is inf, as for a column


def safe_eval_expr(expr: str, frame: pd.DataFrame):
    """Evaluate a compute expression of the shared grammar (`arith_parse`) over
    frame columns. Returns a numeric Series, or None when expr is outside the
    grammar or names a column the frame does not have. Every operand is coerced
    with pd.to_numeric(errors="coerce"); one with no numeric content raises
    ComputeExprError (P56). A single binary `colA OP colB` evaluates exactly as
    under the old one-operator evaluator; a bare numeric column is a copy; a
    constant-only expression broadcasts."""
    parsed = arith_parse(expr, frame.columns)
    if parsed is None:
        return None
    cols = {str(c) for c in frame.columns}
    if not parsed.refs <= cols:
        return None
    # P56: each operand must have numeric content — the S36 criterion aggregate
    # applies, through the same function. Was: `to_numeric(errors="coerce")`
    # alone, so `cost + region` over a text column became an all-NaN column with
    # nothing raised, while `sum(region)` one operator over refused typed.
    operands = {c: _coerce_numeric_or_refuse(frame[c], c, expr, error=ComputeExprError,
                                             what="compute",
                                             consequence="produce an all-NaN column")
                for c in sorted(parsed.refs)}
    try:
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            out = _eval_arith(parsed.tree, operands, parsed)
    except RecursionError:
        return None
    if not isinstance(out, pd.Series):          # constant-only expression: broadcast
        out = pd.Series(float(out), index=frame.index, dtype="float64")
    return out


def _values_close(a: pd.Series, b: pd.Series) -> bool:
    """Elementwise float compare with tolerance; NaN-aligned positions agree."""
    a = pd.to_numeric(a, errors="coerce").reset_index(drop=True)
    b = pd.to_numeric(b, errors="coerce").reset_index(drop=True)
    if len(a) != len(b):
        return False
    both_nan = a.isna() & b.isna()
    close = (a - b).abs() <= (_ATOL + _RTOL * b.abs())
    return bool((both_nan | close).all())


# ── canonical select (= SPEC `filter`) ───────────────────────────────────────
# BASELINE-ONLY, ILLUSTRATIVE. This col_map is the keyword vocabulary the op-set
# baseline's Call-1 emits when it does NOT emit an explicit predicate list. The
# names below are a GENERIC PLACEHOLDER SET — they are not the schema of any
# evaluated domain, and are deliberately chosen to collide with none of them.
#
# It is NOT on the goal→plan path: prim_select and _select_cols both return early
# on `predicates=[{col,op,value}]`, which is the domain-agnostic form the goal→plan
# engine always emits, so GDC never reads this table. Baselines are comparison
# points and may be domain-specific; this one is isolated from the schema-driven
# goal→plan engine. (S17)
_SELECT_COLMAP: dict[str, tuple[str, str]] = {
    "state":           ("State",     "status_active"),
    "assignee":        ("Assignee",  "eq"),
    "tier":            ("Tier",      "eq"),
    "team":            ("Team",      "eq"),
    "plan":            ("Plan",      "eq"),
    "origin":          ("Origin",    "icontains"),
    "path":            ("Path",      "icontains"),
    "value_threshold": ("Value",     "ge_numeric"),
}


def prim_select(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    """SPEC `filter`: conjunction of predicates over the canonical col_map.

    Also accepts an explicit `predicates` list ([{col, op, value}]) per SPEC 3.1
    when present, so the idealised filter API is available; the col_map keys are
    the form Call-1 actually emits.
    """
    result = frame.copy()
    preds = params.get("predicates")
    if isinstance(preds, list):
        # Deferred import: gdc/__init__ -> schema_bridge -> integrate imports
        # this module, so a module-level import of gdc.state is circular.
        from gdc.state import normalise_op
        for pr in preds:
            col, op, val = pr.get("col"), pr.get("op"), pr.get("value")
            if col is None:
                raise FilterColumnError(
                    f"filter predicate {pr!r} names no column. Refusing rather "
                    f"than silently ignoring the predicate.")
            if col not in result.columns:
                # P26. Was: `continue` — the predicate was dropped and every
                # row came back.
                raise FilterColumnError(
                    f"filter {col!r} {op!r} {val!r}: column {col!r} is not in "
                    f"the frame (columns: {[str(c) for c in result.columns]}). "
                    f"Refusing rather than silently ignoring the predicate and "
                    f"returning every row.")
            # P21: the same canonicalisation the planner applies, so "=" / "eq"
            # / "EQ" reach the chain as "==" — the executor and filter_pre must
            # agree on spelling as well as on membership.
            op = normalise_op(op)
            s = result[col]
            # A null test first (see NULL_TEST_OPS). Then P54: the value's shape,
            # before any branch compares anything. Was: `k == ["a", "b"]`
            # compared str(list) and returned no rows; `x in 4` wrapped the
            # scalar; `== None` compared "None".
            if is_null_test(op, val):
                result = result[s.isna()] if op == "==" else result[s.notna()]
                continue
            if not value_shape_ok(op, val):
                raise FilterTypeError(
                    f"filter {col!r} {op} {val!r}: {op!r} takes "
                    f"{'one scalar value' if op in SCALAR_OPS else 'a list of values'}, "
                    f"not {type(val).__name__}. Refusing rather than comparing the "
                    f"value's string form and returning a wrong row count.")
            # P54: a numeric column compares numerically, and only with numbers.
            # Bool columns keep the string comparison ("True"/"False" is unambiguous).
            numeric_col = (pd.api.types.is_numeric_dtype(s)
                           and not pd.api.types.is_bool_dtype(s))
            if numeric_col and op in ("==", "!=", "in") and not values_numeric_ok(op, val):
                raise FilterTypeError(
                    f"filter {col!r} {op} {val!r}: value is not numeric — cannot "
                    f"compare against a numeric column (likely an inexpressible "
                    f"row-vs-aggregate request, or a value of the wrong type). "
                    f"Refusing rather than comparing string forms.")
            if op == "==":
                if numeric_col:
                    result = result[(pd.to_numeric(s, errors="coerce") == numeric_value(val)).fillna(False)]
                else:
                    result = result[s.astype(str) == str(val)]
            elif op == "!=":
                # NaN rows are kept under != on either path (P59: contract silent;
                # unchanged here). Numeric: NaN != v is True in pandas as it was
                # for "nan" != str(v).
                if numeric_col:
                    result = result[pd.to_numeric(s, errors="coerce") != numeric_value(val)]
                else:
                    result = result[s.astype(str) != str(val)]
            elif op == "in":
                vals = list(val)
                if numeric_col:
                    result = result[pd.to_numeric(s, errors="coerce").isin([numeric_value(v) for v in vals])]
                else:
                    result = result[s.astype(str).isin([str(v) for v in vals])]
            elif op == "contains":
                # P53: a literal substring, case-insensitive. Was: regex=True by
                # default, so "." matched every row, "a+" matched "ann", and "("
                # raised an untyped ArrowInvalid.
                result = result[s.astype(str).str.contains(str(val), case=False,
                                                           regex=False, na=False)]
            elif op in ("<", "<=", ">", ">="):
                try:
                    f = float(val)
                except (TypeError, ValueError):
                    raise FilterTypeError(
                        f"filter {col!r} {op} {val!r}: value is not numeric — cannot "
                        f"compare against a numeric column (likely an inexpressible "
                        f"row-vs-aggregate request)")
                # P55: the COLUMN must have numeric content too — the S36
                # criterion aggregate already applies. Was: `to_numeric(s,
                # errors="coerce")` alone, so an ordered op on a column of
                # words or ISO dates became all-NaN, every comparison False, and
                # a confident empty answer — while the same branch refused a
                # non-numeric VALUE one line earlier.
                n = _coerce_numeric_or_refuse(s, col, op, error=FilterTypeError,
                                              what="filter", consequence="return no rows")
                result = result[{"<": n < f, "<=": n <= f, ">": n > f, ">=": n >= f}[op]]
            else:
                # P21. Was: no else — an unknown op fell through the chain and
                # the predicate filtered NOTHING, returning every row.
                raise FilterOpError(
                    f"filter {col!r} {op!r} {val!r}: comparison op {op!r} is not "
                    f"implemented by the executor (supported: "
                    f"{sorted(SUPPORTED_FILTER_OPS)}). Refusing rather than "
                    f"silently returning every row.")
        return result
    for key, val in params.items():
        if str(key).startswith("_") or val is None:
            continue
        mapping = _SELECT_COLMAP.get(key)
        if not mapping:
            continue
        col, mode = mapping
        if col not in result.columns:
            continue
        if mode == "status_active":
            if str(val).lower() == "active":
                result = result[result[col].astype(str).str.lower().str.startswith("active")]
            else:
                result = result[result[col].astype(str).str.contains(str(val), case=False, na=False)]
        elif mode == "icontains":
            result = result[result[col].astype(str).str.contains(str(val), case=False, na=False)]
        elif mode == "eq":
            result = result[result[col].astype(str) == str(val)]
        elif mode == "ge_numeric":
            try:
                result = result[pd.to_numeric(result[col], errors="coerce") >= float(val)]
            except (TypeError, ValueError):
                pass
    return result


def verify_select(params: dict, source: pd.DataFrame, out: pd.DataFrame,
                  sources=None) -> tuple[bool, str]:
    if list(map(str, out.columns)) != list(map(str, source.columns)):
        return False, "select changed the column schema"
    if len(out) > len(source):
        return False, "select produced more rows than the source"
    return True, "select: schema preserved, subset of rows"


# ── compute ───────────────────────────────────────────────────────────────────

def prim_compute(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    out = frame.copy()
    new_col = params.get("new_col", "computed")
    expr = str(params.get("expr", ""))
    series = safe_eval_expr(expr, out)
    if series is None:
        # P15a. Was: `return out` — fail open, on the reasoning that "verify will
        # refuse". It does not: execute_plan never calls verify_primitive, so on
        # the live path this silently produced a frame missing a declared column,
        # or one whose existing column kept a stale value, with nothing raised.
        raise ComputeExprError(
            f"compute cannot evaluate expr {expr!r} for column {new_col!r}: it "
            f"is outside the compute grammar (+ - * /, parentheses, unary minus, "
            f"numeric literals, column names) or names a column the frame does "
            f"not have (columns: {[str(c) for c in out.columns]}). Refusing "
            f"rather than returning a frame in which {new_col!r} is absent or "
            f"stale.")
    out[new_col] = series
    return out


def verify_compute(params: dict, source: pd.DataFrame, out: pd.DataFrame,
                   sources=None) -> tuple[bool, str]:
    nc = params.get("new_col")
    if nc is None:
        return False, "compute has no new_col"
    if str(nc) not in {str(c) for c in out.columns}:
        return False, f"compute new_col {nc!r} not created"
    expr = params.get("expr")
    # P15c. Was: `safe_eval_expr(str(expr), out)` — the reference was
    # recomputed over the OUTPUT frame. When expr overwrites one of its own
    # inputs (`cost*qty -> cost`) the input has already been replaced by the
    # time the reference is taken, so eval(expr) saw cost*qty*qty and a correct
    # result was refused. SPEC §3.4's "new_col must equal eval(expr)" is only
    # meaningful over the frame the primitive READ, which is `source`: every
    # input column is there at its pre-mutation value, including one that an
    # earlier compute in the chain produced. Row-preserving, so the index
    # lines up with `out` for the comparison.
    if source is None:
        return False, "compute: source unavailable — cannot recompute eval(expr)"
    try:
        expected = safe_eval_expr(str(expr), source) if expr else None
    except ComputeExprError as exc:            # P56: the primitive refuses the same operands
        return False, f"compute: {exc}"
    if expected is None:
        return False, (f"compute expr {expr!r} is outside the compute grammar or "
                       f"names a column the source does not have")
    if len(out) != len(source):
        return False, f"compute changed the row count ({len(source)} -> {len(out)})"
    if not _values_close(expected, out[str(nc)]):
        return False, f"compute new_col {nc!r} values != eval({expr!r}) over source"
    return True, "compute: new_col == eval(expr) over source"


# ── aggregate ──────────────────────────────────────────────────────────────────

# P22. What prim_aggregate implements. Was `_AGGS = {sum, mean, count, min, max}`
# — consulted by NOTHING, so an unknown fn reached `getattr(SeriesGroupBy, fn)`
# and crashed with an untyped AttributeError from inside pandas. `nunique` was
# always handled (the count/nunique special case) but was missing from the old
# set; `median` used to work by accident and is not declared, so it is now
# refused like any other undeclared fn — the LLM is never told it exists
# (gdc.llm.ALLOWED_AGG_FNS).
# aggregate_pre (gdc/operators.py) consults both names below at plan time,
# and validate_goal (gdc/contract.py) refuses a Goal that applies any other
# function before planning, naming it; evidence/tests/test_edge_p22_aggregate_fn.py
# and test_unsupported_aggregate.py lock the set to the paths.
SUPPORTED_AGG_FNS: frozenset[str] = frozenset(
    {"sum", "mean", "count", "min", "max", "nunique"})

# Planner / Call-1 spellings that map onto a supported fn. Consumed by BOTH
# integrate.op_to_primitive (dispatch) and operators.aggregate_pre (plan-time
# refusal), so the two cannot disagree about which spellings are runnable.
AGG_FN_ALIASES: dict[str, str] = {"avg": "mean",              # LLM1 fn -> executor
                                  "count_distinct": "nunique"}  # S45 distinct count


def has_numeric_content(col: pd.Series) -> bool:
    """The S36 criterion, declared once: a column has numeric content unless
    coercion drops EVERY originally non-null value. A clean numeric column with
    stray NaN/None passes; a text column of numeric strings passes; a column of
    words or ISO dates does not. An all-null or empty column passes (nothing to
    refuse, nothing to approximate)."""
    coerced = pd.to_numeric(col, errors="coerce")
    return not (col.notna().any() and not coerced.notna().any())


def _coerce_numeric_or_refuse(col: pd.Series, vc: str, agg: str, *,
                              error: type = AggregateTypeError,
                              what: str = "aggregate",
                              consequence: str = "produce NaN") -> pd.Series:
    """S36 FIX 2a — coerce a column to numeric for a non-count aggregate, or raise
    AggregateTypeError if it has no numeric content (refuse-don't-approximate).
    A clean numeric column with stray NaN/None is unaffected; a text column where
    coercion drops ALL originally non-null values is rejected.

    P55/P56 reuse the same criterion for the filter's ordered branch and for
    compute, each raising its own member of the typed family with its own name
    in the message (`error`, `what`, `consequence`); the rule is one function."""
    if not has_numeric_content(col):
        raise error(
            f"{what} {agg!r} on column {vc!r}: non-numeric column cannot be "
            f"coerced to numeric — refusing to silently {consequence}")
    return pd.to_numeric(col, errors="coerce")


def prim_aggregate(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    gb = params.get("group_by")
    vc = params.get("value_col")
    agg = params.get("agg", "sum")
    # P22: refuse an undeclared fn HERE, typed, before any of the three paths
    # below reaches getattr(...)(). aggregate_pre mirrors this at plan time.
    if str(agg) not in SUPPORTED_AGG_FNS:
        raise AggregateTypeError(
            f"aggregate fn {agg!r} is not implemented by the executor (supported: "
            f"{sorted(SUPPORTED_AGG_FNS)}). Refusing rather than failing inside pandas.")
    # Global aggregate: a PRESENT-but-empty group_by ([]) collapses ALL rows into
    # one group, yielding a single-row frame carrying the aggregated value (no
    # group column). An ABSENT group_by (None) stays a no-op (missing input).
    if isinstance(gb, (list, tuple)) and len(gb) == 0:
        if vc is None or str(vc) not in frame.columns:
            # P58. Was: `return frame.copy()` — "verify refuses", but verify
            # is not on the live path (P15a), so the frame came back unchanged
            # and unaggregated. Three such branches, all typed now.
            raise AggregateTypeError(
                f"aggregate {agg!r}: value column {vc!r} is not in the frame "
                f"(columns: {[str(c) for c in frame.columns]}). Refusing rather "
                f"than returning the frame unaggregated.")
        if str(agg) in ("count", "nunique"):
            # count / distinct-count work on ANY dtype (a distinct count of
            # names is legitimate) — numeric coercion would corrupt them.
            val = int(getattr(frame[str(vc)], str(agg))())
        else:
            val = getattr(_coerce_numeric_or_refuse(frame[str(vc)], str(vc), str(agg)),
                          str(agg))()
        return pd.DataFrame({str(vc): [val]})
    # Multi-column group-by: gb is a NON-empty list/tuple of key columns (the empty
    # list is the global aggregate, handled above). str(gb) stringifies the whole
    # list and never matches a column, so the single-key path below would silently
    # return the raw frame — the multi-key GROUP BY bug. Collapse to true group grain
    # here. (Single-key gb arrives as a bare str from integrate.py and is untouched.)
    if isinstance(gb, (list, tuple)):
        keys = [str(k) for k in gb]
        if vc is None or not set(keys) <= set(map(str, frame.columns)) \
                or str(vc) not in frame.columns:
            missing = [c for c in keys + [str(vc)] if c not in set(map(str, frame.columns))]
            raise AggregateTypeError(                       # P58, was: return frame.copy()
                f"aggregate {agg!r} by {keys!r} of {vc!r}: column(s) {missing!r} are "
                f"not in the frame (columns: {[str(c) for c in frame.columns]}). "
                f"Refusing rather than returning the frame unaggregated.")
        work = frame.copy()
        if str(agg) not in ("count", "nunique"):
            work[str(vc)] = _coerce_numeric_or_refuse(work[str(vc)], str(vc), str(agg))
        ser = work.groupby(keys)[str(vc)].agg(str(agg))   # MultiIndex -> aggregate
        if str(vc) in keys:
            # value_col collides with a group key (e.g. count of a key column): keep
            # the colliding key under a distinct {key}__group name and put the
            # aggregate under value_col, so the executor's value_col->out_col post
            # rename targets the aggregate — mirrors the single-key str(vc)==str(gb)
            # branch below. The shape-tolerant comparator normalises __group away.
            out = ser.rename("__agg__").reset_index()
            return out.rename(columns={str(vc): f"{vc}__group", "__agg__": str(vc)})
        return ser.reset_index()      # [key1, key2, ..., value_col]
    if gb is None or vc is None or str(gb) not in frame.columns or str(vc) not in frame.columns:
        missing = [c for c in (str(gb), str(vc)) if c not in set(map(str, frame.columns))]
        raise AggregateTypeError(                           # P58, was: return frame.copy()
            f"aggregate {agg!r} by {gb!r} of {vc!r}: column(s) {missing!r} are not in "
            f"the frame (columns: {[str(c) for c in frame.columns]}). Refusing rather "
            f"than returning the frame unaggregated.")
    work = frame.copy()
    if str(agg) not in ("count", "nunique"):
        work[str(vc)] = _coerce_numeric_or_refuse(work[str(vc)], str(vc), str(agg))
    ser = work.groupby(str(gb))[str(vc)].agg(str(agg))    # index=group, values=aggregate
    if str(vc) == str(gb):
        # Aggregating the GROUP column itself (e.g. count rows per group): a plain
        # reset_index() would try to create two columns named {gb} -> ValueError. Put
        # the group under a distinct name and keep the AGGREGATE under value_col, so
        # the executor's value_col->out_col post-rename (and any order_by on out_col)
        # targets the aggregate, not the group. The shape-tolerant comparator keys on
        # column position, so the group column's distinct name is immaterial.
        return pd.DataFrame({f"{gb}__group": ser.index.to_numpy(),
                             str(vc): ser.to_numpy()})
    return ser.reset_index()          # [group, value_col] — unchanged normal path


def verify_aggregate(params: dict, source: pd.DataFrame, out: pd.DataFrame,
                     sources=None) -> tuple[bool, str]:
    gb, vc, agg = params.get("group_by"), params.get("value_col"), params.get("agg")
    if gb is None or vc is None or agg is None:
        return False, "aggregate missing group_by/value_col/agg"
    if str(gb) not in {str(c) for c in out.columns}:
        return False, f"aggregate output missing group column {gb!r}"
    # P15d-sibling. Was: `return True, "... source unavailable for value check
    # (schema ok)"` for all three cases below — a pass issued when the reference
    # recompute SPEC §3.4 requires was impossible. Same rule as P15g one line
    # down: a verifier that cannot check does not say ok.
    if source is None:
        return False, "aggregate: source unavailable — cannot recompute groupby/agg"
    src_cols = {str(c) for c in source.columns}
    if str(gb) not in src_cols:
        return False, (f"aggregate: source lacks group column {gb!r} "
                       f"(columns: {sorted(src_cols)}) — cannot recompute")
    if str(vc) not in src_cols:
        return False, (f"aggregate: source lacks value column {vc!r} "
                       f"(columns: {sorted(src_cols)}) — cannot recompute")
    if out.shape == source.shape:
        return False, "aggregate looks like a source pass-through (no-op)"
    try:
        work = source.copy()
        if str(agg) not in ("count", "nunique"):
            work[str(vc)] = pd.to_numeric(work[str(vc)], errors="coerce")
        ref = work.groupby(str(gb))[str(vc)].agg(str(agg)).reset_index()
    except Exception as exc:  # noqa: BLE001
        # P15g. Was: `return True, "... schema ok"` — a verifier that PASSED
        # when its own reference computation crashed. Not on the live path
        # (verify is unwired, deliberately), but the evidence harness, the demo
        # and the tests do call it, and a verifier that says ok when it could
        # not check is worse than no verifier.
        return False, (f"aggregate: reference recompute failed "
                       f"({type(exc).__name__}: {exc}) — cannot verify")
    if len(out) != len(ref):
        return False, f"aggregate row count {len(out)} != groupby ref {len(ref)}"
    ref_map = dict(zip(ref[str(gb)].astype(str),
                       pd.to_numeric(ref[str(vc)], errors="coerce")))
    # label-agnostic: any non-group column whose per-group values match the ref
    for c in [c for c in out.columns if str(c) != str(gb)]:
        prod_map = dict(zip(out[str(gb)].astype(str),
                            pd.to_numeric(out[c], errors="coerce")))
        if set(prod_map) != set(ref_map):
            continue
        if all(_scalar_close(prod_map[k], ref_map[k]) for k in ref_map):
            return True, "aggregate: values == recomputed groupby/agg"
    return False, "aggregate values disagree with recomputed groupby/agg"


def _scalar_close(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= (_ATOL + _RTOL * abs(float(b)))


# ── sort ───────────────────────────────────────────────────────────────────────

def _is_null(v) -> bool:
    return v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and v != v)


def _value_order_key(v):
    """Type-ranked ascending key for one cell of a mixed-type column: numbers by
    value, then strings, then anything else by type name and text. Nulls stay
    null so `na_position` still places them."""
    if _is_null(v):
        return np.nan
    if isinstance(v, (bool, np.bool_)):
        return (0, int(v), "")
    if isinstance(v, (int, float, np.integer, np.floating)):
        return (0, float(v), "")
    if isinstance(v, str):
        return (1, 0.0, v)
    return (2, 0.0, f"{type(v).__name__}\x00{v}")


def stable_sort(frame: pd.DataFrame, cols: list, ascending) -> pd.DataFrame:
    """THE sort, for the executor (prim_sort) and for gdc.order.apply_order:
    a stable multi-key sort, each key in its own direction, nulls last. Rows
    tied on every key keep their input order, so the result is a function of the
    input rows alone. Was: pandas' default kind (quicksort) on a single key,
    which is not stable, and whose tie order differs across numpy builds and
    platforms; gdc/order.py documented the stable sort, but the live path
    did not run it. A column whose cells cannot be compared with each other
    (numbers mixed with strings) is ordered by type, then value, instead of
    raising TypeError. Raises KeyError for a key the frame does not have; the
    caller decides what an absent key means."""
    try:
        out = frame.sort_values(cols, ascending=ascending, kind="mergesort",
                                na_position="last")
    except TypeError:
        out = frame.sort_values(cols, ascending=ascending, kind="mergesort",
                                na_position="last",
                                key=lambda s: s.map(_value_order_key)
                                if s.dtype == object else s)
    return out.reset_index(drop=True)


def prim_sort(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Sort by the named keys (stable_sort). No key requested (`by` absent or
    empty) is no sort, as the `order` tool's limit-only call relies on. A key the
    frame does not have is a SortColumnError. Was: the key was dropped, and the
    frame came back sorted by the other keys or not at all."""
    by = params.get("by", params.get("sort_by"))
    if by is None:
        return frame.copy()
    cols = by if isinstance(by, list) else [by]
    if not cols:
        return frame.copy()
    missing = [c for c in cols if str(c) not in frame.columns]
    if missing:
        raise SortColumnError(
            f"sort by {cols!r}: key(s) {missing!r} are not in the frame (columns: "
            f"{[str(c) for c in frame.columns]}). Refusing rather than sorting by the "
            f"other keys and returning a wrong order or top-k.")
    ascending = params.get("ascending", False)
    return stable_sort(frame, cols, ascending)


def verify_sort(params: dict, source: pd.DataFrame, out: pd.DataFrame,
                sources=None) -> tuple[bool, str]:
    if set(map(str, out.columns)) != set(map(str, source.columns)):
        return False, "sort changed the column schema"
    if len(out) != len(source):
        return False, "sort changed the row count"
    by = params.get("by", params.get("sort_by"))
    cols = (by if isinstance(by, list) else [by]) if by is not None else []
    cols = [str(c) for c in cols if str(c) in out.columns]
    asc = params.get("ascending", False)
    if cols:
        a = asc[0] if isinstance(asc, list) and asc else (asc if isinstance(asc, bool) else False)
        s = pd.to_numeric(out[cols[0]], errors="coerce")
        if s.notna().all():
            ordered = s.is_monotonic_increasing if a else s.is_monotonic_decreasing
            if not ordered:
                return False, f"sort output not ordered by {cols[0]!r} (ascending={a})"
    return True, "sort: schema/rows preserved, ordered by key"


# ── limit ──────────────────────────────────────────────────────────────────────

def prim_limit(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    try:
        n = int(params.get("n", 10))
    except (TypeError, ValueError):
        n = 10
    return frame.head(n)


def verify_limit(params: dict, source: pd.DataFrame, out: pd.DataFrame,
                 sources=None) -> tuple[bool, str]:
    try:
        n = int(params.get("n", 10))
    except (TypeError, ValueError):
        n = 10
    if set(map(str, out.columns)) != set(map(str, source.columns)):
        return False, "limit changed the column schema"
    if len(out) != min(n, len(source)):
        return False, f"limit produced {len(out)} rows, expected {min(n, len(source))}"
    return True, "limit: first n rows, schema preserved"


# ── join ───────────────────────────────────────────────────────────────────────

class JoinInputError(FilterTypeError):
    """P24 — a join whose inputs it cannot honour.

    `prim_join` used to fail open three ways: no right frame -> return the left
    unchanged; right frame unreadable -> swallow the exception and return the
    left unchanged; and, worst, any requested key absent from either side was
    silently DROPPED and the merge ran on the remaining subset of keys — a
    join on (a, b) quietly became a join on (a). None of these is a join, and
    all three returned a well-formed frame with no signal.

    Not plannable (join is not in operators.OPERATORS) and not dispatched by
    tools_decl._apply_one, so this is reached only by a direct
    run_primitive("join", ...) caller; it subclasses FilterTypeError for family
    consistency so any such caller gets the same typed channel as the others."""


def prim_join(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Merge `frame` (left) with the right frame on `on`. The right frame is
    supplied by the orchestrator's compose mechanism via params['_compose_source_0']
    (a pickle path) — mirroring how the executor threads a second parent.

    P24: raises JoinInputError on anything it cannot honour, instead of
    returning the left frame unchanged (see the class docstring)."""
    on = params.get("on")
    how = params.get("how", "outer")
    left_label = params.get("left_label", "")
    right_label = params.get("right_label", "")
    rp = params.get("_compose_source_0")
    if not rp:
        raise JoinInputError(
            "join needs a right frame via params['_compose_source_0']; none was "
            "supplied. Refusing rather than returning the left frame unchanged.")
    try:
        right = pd.read_pickle(rp)
    except Exception as exc:  # noqa: BLE001 — re-raised typed, not swallowed
        raise JoinInputError(
            f"join right frame {str(rp)!r} could not be read: "
            f"{type(exc).__name__}: {exc}") from exc
    requested = [on] if (on and not isinstance(on, list)) else list(on or [])
    if not requested:
        raise JoinInputError("join needs at least one key in params['on']; none given.")
    missing = [c for c in requested
               if c not in frame.columns or c not in right.columns]
    if missing:
        raise JoinInputError(
            f"join key(s) {missing} absent from the left or right frame "
            f"(left: {list(map(str, frame.columns))}, right: "
            f"{list(map(str, right.columns))}). Refusing rather than joining on "
            f"the remaining subset of keys.")
    suffixes = (f"_{left_label}" if left_label else "_x",
                f"_{right_label}" if right_label else "_y")
    return frame.merge(right, on=requested, how=how, suffixes=suffixes)


def verify_join(params: dict, source: pd.DataFrame, out: pd.DataFrame,
                sources=None) -> tuple[bool, str]:
    on = params.get("on")
    on_cols = [on] if (on and not isinstance(on, list)) else (on or [])
    for c in on_cols:
        if c and str(c) not in {str(x) for x in out.columns}:
            return False, f"join output missing key column {c!r}"
    if out.shape[1] < source.shape[1]:
        return False, "join output narrower than the left input"
    return True, "join: key present, columns combined"


# ── write (persist + pass-through) ────────────────────────────────────────────

def prim_write(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    path = params.get("path")
    if path:
        try:
            p = str(path)
            from pathlib import Path as _P
            _P(p).parent.mkdir(parents=True, exist_ok=True)
            if p.endswith(".csv"):
                frame.to_csv(p, index=False)
            else:
                frame.to_pickle(p)
        except Exception:
            pass
    return frame


def verify_write(params: dict, source: pd.DataFrame, out: pd.DataFrame,
                 sources=None) -> tuple[bool, str]:
    if out.shape != source.shape:
        return False, "write must pass the frame through unchanged"
    return True, "write: pass-through"


# ── project ────────────────────────────────────────────────────────────────────

def _project_columns_or_refuse(cols, frame: pd.DataFrame) -> list:
    """P58: the requested columns, all present, or a typed refusal."""
    if not isinstance(cols, (list, tuple)) or len(cols) == 0:
        raise ProjectColumnError(
            f"project: `columns` must be a non-empty list of column names, got "
            f"{cols!r}. Refusing rather than returning every column.")
    missing = [c for c in cols if c not in frame.columns]
    if missing:
        raise ProjectColumnError(
            f"project: column(s) {missing!r} are not in the frame (columns: "
            f"{[str(c) for c in frame.columns]}). Refusing rather than silently "
            f"narrowing the projection to the columns that exist.")
    return list(cols)


def prim_project(frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    # P58. Was: a non-list request returned the frame unchanged; a missing
    # column was dropped from the projection; no column present returned the
    # frame unchanged — three silent paths, none of them what the State said.
    cols = _project_columns_or_refuse(params.get("columns"), frame)
    return frame[cols].copy()


def verify_project(params: dict, source: pd.DataFrame, out: pd.DataFrame,
                   sources=None) -> tuple[bool, str]:
    try:
        want = [str(c) for c in _project_columns_or_refuse(params.get("columns"), source)]
    except ProjectColumnError as exc:
        return False, f"project: {exc}"
    if list(map(str, out.columns)) != want:
        return False, "project output columns != requested projection"
    return True, "project: columns selected/reordered"


# ── contracts: needs / produces (SPEC §3.2) ──────────────────────────────────
# needs(params) / produces(params) return the set of column names (or the ORDER
# sentinel) a primitive requires / yields. These drive contract-based ordering
# (§3.6) and routing (§3.7): a primitive that needs what another produces must
# follow it. Params-derived and best-effort; join's label-suffixed output columns
# are resolved with branch context by the orderer, so join here declares only its
# key. The value-level verify (above) remains the postcondition check.

ORDER = "__order__"   # the ordering predicate: sort produces it, limit needs it


def _select_cols(params: dict) -> set[str]:
    preds = params.get("predicates")
    if isinstance(preds, list):
        return {str(p.get("col")) for p in preds if p.get("col")}
    cols: set[str] = set()
    for k, v in params.items():
        if str(k).startswith("_") or v is None:
            continue
        m = _SELECT_COLMAP.get(k)
        if m:
            cols.add(m[0])
    return cols


def _expr_cols(expr) -> set[str]:
    parsed = arith_parse(str(expr))
    if parsed is not None:
        return set(parsed.refs)
    for op in ("/", "*", "-", "+"):
        if op in str(expr):
            left, right = str(expr).split(op, 1)
            return {left.strip(), right.strip()}
    return set()


def _on_cols(params: dict) -> set[str]:
    on = params.get("on")
    return set(map(str, on if isinstance(on, list) else ([on] if on else [])))


def _sort_cols(params: dict) -> set[str]:
    by = params.get("by", params.get("sort_by"))
    if by is None:
        return set()
    return set(map(str, by if isinstance(by, list) else [by]))


_NEEDS: dict[str, Callable[[dict], set[str]]] = {
    "select":    _select_cols,
    "filter":    _select_cols,
    "compute":   lambda p: _expr_cols(p.get("expr", "")),
    "aggregate": lambda p: {str(p.get("group_by")), str(p.get("value_col"))} - {"None"},
    "sort":      _sort_cols,
    "limit":     lambda p: {ORDER},
    "join":      _on_cols,
    "write":     lambda p: set(),
    "project":   lambda p: set(map(str, p.get("columns", []) or [])),
}
_PRODUCES: dict[str, Callable[[dict], set[str]]] = {
    "select":    lambda p: set(),                      # row-subset; same columns
    "filter":    lambda p: set(),
    "compute":   lambda p: {str(p["new_col"])} if p.get("new_col") else set(),
    "aggregate": lambda p: {str(p.get("group_by")), str(p.get("value_col"))} - {"None"},
    "sort":      lambda p: {ORDER},                    # an ordering over rows
    "limit":     lambda p: set(),                      # fewer rows; nothing new
    "join":      _on_cols,                             # key kept; suffixed cols via orderer
    "write":     lambda p: set(),
    "project":   lambda p: set(map(str, p.get("columns", []) or [])),
}


def needs(kind: str, params: dict) -> set[str]:
    fn = _NEEDS.get(kind)
    return fn(params) if fn else set()


def produces(kind: str, params: dict) -> set[str]:
    fn = _PRODUCES.get(kind)
    return fn(params) if fn else set()


# ── registry ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Primitive:
    kind: str
    fn: Callable[[pd.DataFrame, dict], pd.DataFrame]
    contract: str
    verify: Callable[..., tuple[bool, str]]
    needs: Callable[[dict], set[str]]
    produces: Callable[[dict], set[str]]


def _P(kind, fn, contract, verify):
    return Primitive(kind, fn, contract, verify, _NEEDS[kind], _PRODUCES[kind])


REGISTRY: dict[str, Primitive] = {
    "select":    _P("select",    prim_select,    "needs ref'd cols; produces row-subset, same cols",   verify_select),
    "filter":    _P("filter",    prim_select,    "needs ref'd cols; produces row-subset, same cols",   verify_select),
    "compute":   _P("compute",   prim_compute,   "needs expr cols; produces new_col == eval(expr)",    verify_compute),
    "aggregate": _P("aggregate", prim_aggregate, "needs group_by+value_col; produces group_by+agg_col",verify_aggregate),
    "sort":      _P("sort",      prim_sort,      "needs by cols; produces an ordering, same rows/cols",verify_sort),
    "limit":     _P("limit",     prim_limit,     "needs an ordering; produces <= n rows",              verify_limit),
    "join":      _P("join",      prim_join,      "needs on-keys in both; produces merged columns",     verify_join),
    "write":     _P("write",     prim_write,     "pass-through (side-effect persist)",                 verify_write),
    "project":   _P("project",   prim_project,   "needs cols; produces requested columns only",        verify_project),
}

# `read` is the root/source, not a transform primitive; it is handled by the
# executor as the environment root, never executed as a primitive.
NON_PRIMITIVE_KINDS = frozenset({"read"})


def is_registered(kind: str) -> bool:
    return kind in REGISTRY


def run_primitive(kind: str, frame: pd.DataFrame, params: dict) -> pd.DataFrame:
    prim = REGISTRY.get(kind)
    if prim is None:
        raise KeyError(f"no registered primitive for kind {kind!r}")
    return prim.fn(frame, params)


def verify_primitive(kind: str, params: dict, source: pd.DataFrame,
                     out: pd.DataFrame, sources=None) -> tuple[bool, str]:
    prim = REGISTRY.get(kind)
    if prim is None:
        return False, f"no registered primitive for kind {kind!r}"
    try:
        return prim.verify(params, source, out, sources=sources)
    except Exception as exc:  # noqa: BLE001 — a verify must never crash the run
        return False, f"verify error: {exc}"


# ── state fingerprint (verify signature, SPEC 2.1 / 7) ───────────────────────

def _public_params(params: dict) -> dict:
    return {k: v for k, v in (params or {}).items() if not str(k).startswith("_")}


def _content_signature(frame: pd.DataFrame) -> str:
    """Stable, single-valued content hash of a frame (order-insensitive over
    rows; sensitive to columns + values). Used as the reproducible state key."""
    try:
        cols = sorted(map(str, frame.columns))
        row_hashes = pd.util.hash_pandas_object(frame[sorted(frame.columns, key=str)],
                                                index=False)
        h = hashlib.sha256()
        h.update(json.dumps(cols).encode())
        h.update(str(int(row_hashes.sum() if len(row_hashes) else 0)).encode())
        h.update(str(frame.shape).encode())
        return h.hexdigest()[:16]
    except Exception:
        return hashlib.sha256(str(frame.shape).encode()).hexdigest()[:16]


def state_fingerprint(kind: str, params: dict, out: pd.DataFrame) -> str:
    """Verify signature = identity of the produced STATE (SPEC 2.1). Combines the
    operation identity `{kind, params}` (the Phase-1 node-identity decision) with
    a content hash of the output, so a cached state is reused only when both the
    operation and its result match."""
    h = hashlib.sha256()
    h.update(json.dumps([kind, _public_params(params)], sort_keys=True, default=str).encode())
    if isinstance(out, pd.DataFrame):
        h.update(_content_signature(out).encode())
    return h.hexdigest()[:16]
