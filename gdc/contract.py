"""Enforceable contract for emitted Goals (the "Enforceable Contract" half of the
LLM1 Goal-Emission step).

LLM1 will emit a Goal as JSON. Before it ever reaches the deterministic planner it
must (1) PARSE into a well-formed Goal (else ParseError) and (2) satisfy the
CONTRACT — the structural rules the LLM-free planner can actually build with the
four primitives (else ContractError). The contract is what makes goal emission
*enforceable*: a malformed or out-of-scope goal is rejected up front, never
silently mis-planned.

Rules (kept to what the planner can express; everything else is a future step):
  - every function the Goal applies is a declared aggregate (the executor's
    gdc.primitives.SUPPORTED_AGG_FNS plus their Goal spellings in
    AGG_FN_ALIASES), in a derive expr and in any column, derive name, where
    column or group key that spells an application → "function X is not a
                                                   supported aggregate"
  - at most one aggregate derive entry          → "more than one aggregate"
  - grouped grain ⇔ exactly one aggregate       → grain/aggregate consistency
  - every non-aggregate derive is in the compute grammar (gdc.primitives.
    arith_parse: + - * /, parentheses, unary minus, numeric literals, column
    names)                                      → "outside the compute grammar"
  - no ordering / top-k                          → the Goal cannot carry it; an
    inexpressible request shows up as an ABSENT field (see unexpressible_keys),
    never as a silently-honoured one.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from gdc.primitives import AGG_FN_ALIASES, SUPPORTED_AGG_FNS, arith_parse
from .state import Goal
from .operators import _classify_derive, expr_refs

# Bumped whenever the contract/lint rules change; recorded in provenance so a
# results.jsonl ties to the exact validation that gated its goals.
# s35: goal validity lints — dead-derive rejection (lint_goal); order closure was
# already enforced live via order.validate_order (S13). Pre-s35 was unversioned.
# s49 over s35: an undeclared function anywhere in the Goal, or a derive outside the compute grammar, is a ContractError.
CONTRACT_VERSION = "s49"

# The aggregate functions a Goal may apply: the executor's declared set and the
# Goal-language spellings that map onto it (avg -> mean, count_distinct ->
# nunique). One declaration, read here, by aggregate_pre and by op_to_primitive.
DECLARED_AGG_FNS: frozenset[str] = frozenset(AGG_FN_ALIASES) | SUPPORTED_AGG_FNS

# fn(EXPR): an aggregate over an arbitrary expression. The planner only knows
# fn(col); the goal language accepts fn(expr) and normalises it to a compute over
# the expression + an aggregate over the intermediate (planner core unchanged).
# The alternatives are the declared set, longest spelling first, so every
# declared fn splits the same way.
_AGG_FN = re.compile(
    r"^\s*(" + "|".join(sorted(map(re.escape, DECLARED_AGG_FNS), key=lambda f: (-len(f), f)))
    + r")\s*\(\s*(.+?)\s*\)\s*$")
_BARE_COL = re.compile(r"^[A-Za-z_]\w*$")
# A function application anywhere in a derive expr: `name(`. The Goal language's
# only functions are the declared aggregates, so each application must name one.
_FN_APPLICATION = re.compile(r"([A-Za-z_]\w*)\s*\(")
# A NAME (column, derive name, where column, group key) that is an application:
# the whole name is `fn(ARG)`, the parenthesis straight after the function's
# name, as code spells a call. A label (`Tenure (Years)`, `share of cost (%)`,
# `cost minus target (net)`) is not one.
_NAME_APPLICATION = re.compile(r"^\s*([A-Za-z_]\w*)\((.*)\)\s*$")

# S45 (Spider Fix 2): distinct-count aggregates. The CANONICAL spelling is
# count_distinct(col); the SQL-flavoured spelling count(DISTINCT col) is
# rewritten to it here, at parse time, BEFORE the fn(EXPR) split below — one
# spelling everywhere is what keeps fingerprint()/the A* closed set collapsing
# identical goals (the derive expr string feeds both), and it also keeps the
# SQL spelling out of the Finding-8 fail-open compute path the split used to
# send it down ("distinct col" is not a safe binary expression).
_COUNT_DISTINCT_SQL = re.compile(r"^\s*count\s*\(\s*distinct\s+(.+?)\s*\)\s*$",
                                 re.IGNORECASE)


def _canon_distinct(expr: str) -> str:
    m = _COUNT_DISTINCT_SQL.match(str(expr))
    return f"count_distinct({m.group(1)})" if m else str(expr)


def _normalize_derives(derive_list) -> list[dict]:
    """Split each `fn(EXPR)` whose EXPR is not a bare column into
    {tmp: EXPR} (compute) + {name: fn(tmp)} (aggregate). `fn(col)` and plain
    computes pass through unchanged. Semantics-preserving; keeps the aggregate
    count intact (the split adds a compute, not an aggregate). count(DISTINCT x)
    is first canonicalised to count_distinct(x) — see _canon_distinct."""
    out: list[dict] = []
    for d in derive_list:
        name = d["name"] if isinstance(d, dict) else d[0]
        expr = _canon_distinct(d["expr"] if isinstance(d, dict) else d[1])
        m = _AGG_FN.match(str(expr))
        if m and not _BARE_COL.match(m.group(2).strip()):
            fn, inner = m.group(1), m.group(2).strip()
            tmp = f"_{name}_pre"
            out.append({"name": tmp, "expr": inner})
            out.append({"name": str(name), "expr": f"{fn}({tmp})"})
        else:
            out.append({"name": str(name), "expr": str(expr)})
    return out

# No baked-in default schema — callers always pass an explicit, domain-specific schema
# (read from the domain's schema.json). The engine itself carries no domain columns.

GOAL_KEYS = frozenset({"columns", "grain", "where", "derive"})


class ParseError(Exception):
    """The goal payload is not a well-formed Goal (bad JSON / missing or
    mistyped fields). Raised before the contract is even consulted."""


class ContractError(Exception):
    """A well-formed Goal violates the enforceable contract. `str(exc)` is the
    reason; `.reason` exposes it too."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ContractVerdict:
    ok: bool
    reason: str

    def raise_if_violated(self) -> None:
        if not self.ok:
            raise ContractError(self.reason)


# ── parsing (payload -> Goal, or ParseError) ─────────────────────────────────

def parse_goal(obj) -> Goal:
    """Parse a goal payload (a Goal, a dict, or a JSON string) into a Goal.
    Raises ParseError on anything malformed — never returns a partial Goal."""
    if isinstance(obj, Goal):
        return obj
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except Exception as exc:  # noqa: BLE001
            raise ParseError(f"not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ParseError(f"goal must be an object, got {type(obj).__name__}")
    for k in ("columns", "grain", "where", "derive"):
        if k not in obj:
            raise ParseError(f"missing required field: {k!r}")
    if not isinstance(obj["columns"], list):
        raise ParseError("'columns' must be a list")
    if not isinstance(obj["where"], list):
        raise ParseError("'where' must be a list")
    if not isinstance(obj["derive"], list):
        raise ParseError("'derive' must be a list")
    for c in obj["where"]:
        if not (isinstance(c, dict) and {"col", "op", "val"} <= set(c)):
            raise ParseError(f"each where conjunct needs col/op/val: {c!r}")
    for d in obj["derive"]:
        if not (isinstance(d, dict) and {"name", "expr"} <= set(d)):
            raise ParseError(f"each derive entry needs name/expr: {d!r}")
    try:
        return Goal.make(columns=obj["columns"], grain=obj["grain"],
                         where=obj["where"], derive=_normalize_derives(obj["derive"]))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"could not build Goal: {exc}") from exc


def unexpressible_keys(obj) -> list[str]:
    """Keys in a goal payload that the Goal schema cannot express (e.g. order_by,
    sort, limit, top). The probe surfaces these so an inexpressible request is a
    VISIBLE drop, not a silently-assumed feature."""
    if not isinstance(obj, dict):
        return []
    return sorted(k for k in obj if k not in GOAL_KEYS)


# ── the contract ─────────────────────────────────────────────────────────────

def aggregate_derives(goal: Goal) -> list[tuple[str, str]]:
    """Derive entries whose expr is a single `fn(col)` aggregate (sum/avg/...).
    Everything else (e.g. `total_cost * 100`) is a computed column, not an
    aggregate."""
    return [(n, e) for n, e in goal.derive if _classify_derive(e) is not None]


def _goal_names(goal: Goal) -> set[str]:
    """Every column name the Goal itself mentions or introduces."""
    return ({str(n) for n, _t in goal.columns} | {str(n) for n, _e in goal.derive}
            | {str(c) for c, _o, _v in goal.where} | set(map(str, goal.grouped_by or ())))


def _split_temporaries(goal: Goal) -> set[str]:
    """The names the fn(EXPR) split introduced (`_<name>_pre`, see
    _normalize_derives): engine-made references, never an emitted application."""
    names = {str(n) for n, _e in goal.derive}
    return {n for n in names if n.startswith("_") and n.endswith("_pre") and n[1:-4] in names}


def undeclared_fn(expr, references=()) -> str | None:
    """The first function `expr` applies (fn(col), fn(EXPR), or nested in a larger
    expression) that is not a declared aggregate; None when every application is
    declared or there is none (`a - b`). `references` are masked first: names a
    derive expr may refer to that are not applications (see validate_goal), such
    as the split's `_count(*)_pre` for an emitted derive named `count(*)`, which
    must not read as a call to `_count`. Was: every non-identifier Goal name was
    masked, so a Goal that also used the application as a name (a column, derive
    name or where column `median(cost)`) hid `median(cost)` from this check."""
    s = str(expr)
    for n in sorted({str(n) for n in references}, key=len, reverse=True):
        s = s.replace(n, "_ref_")
    for fn in _FN_APPLICATION.findall(s):
        if fn not in DECLARED_AGG_FNS:
            return fn
    return None


def undeclared_fn_in_name(name) -> str | None:
    """The undeclared function a NAME applies: the whole name is `fn(ARG)` with fn
    outside the declared set (`median(cost)` -> 'median'); None for `count(*)` and
    for a label such as `Tenure (Years)`."""
    m = _NAME_APPLICATION.match(str(name))
    return m.group(1) if m and m.group(1) not in DECLARED_AGG_FNS else None


def validate_goal(goal: Goal) -> ContractVerdict:
    """Structural contract check. Returns a verdict (never raises) — callers that
    want an exception use verdict.raise_if_violated()."""
    names = _goal_names(goal)
    tmps = _split_temporaries(goal)
    supported = f"(supported: {sorted(DECLARED_AGG_FNS)})"
    # Declared aggregates first: the most specific diagnosis, wherever the Goal
    # spells an application. Was: an undeclared fn(col) passed the structural
    # rules below and either ran as whatever pandas method shares its name or,
    # once aggregate_pre refused it (P22), ended as an opaque Unreachable from
    # plan(); an undeclared fn(EXPR) was refused under a misleading structural
    # reason.
    #
    # What a derive expr may refer to besides a register column (a plain
    # identifier: the network adapter aliases every column) is another derive:
    # its name and the split's temporaries are masked, unless the name is itself
    # an undeclared application. Nothing else is masked, so a column or where
    # column spelled `median(cost)` does not hide `median(cost)` in an expr, and
    # a derive's own name never hides its own expr.
    derive_names = {str(n) for n, _e in goal.derive}
    for name, expr in goal.derive:
        refs = {n for n in derive_names - {str(name)} if not _BARE_COL.match(n)
                and (n in tmps or undeclared_fn_in_name(n) is None)}
        fn = undeclared_fn(expr, refs)
        if fn is not None:
            return ContractVerdict(
                False, f"derive {name!r}: function {fn!r} is not a supported aggregate {supported}")
    # The same rule for a name that is a whole application: `median(cost)` is no
    # register column, so planning it would end in an opaque Unreachable (or, as a
    # derive name, label another aggregate's value as a median).
    for kind, ns in (("column", [str(n) for n, _t in goal.columns]),
                     ("derive name", [str(n) for n, _e in goal.derive]),
                     ("where column", [str(c) for c, _o, _v in goal.where]),
                     ("group key", [str(k) for k in goal.grouped_by or ()])):
        for n in ns:
            fn = None if n in tmps else undeclared_fn_in_name(n)
            if fn is not None:
                return ContractVerdict(
                    False, f"{kind} {n!r}: function {fn!r} is not a supported aggregate {supported}")
    aggs = aggregate_derives(goal)
    if len(aggs) > 1:
        return ContractVerdict(False, "more than one aggregate")
    if goal.grouped_by is not None:
        if len(aggs) != 1:
            return ContractVerdict(False, "grouped grain requires exactly one aggregate")
    else:
        if len(aggs) >= 1:
            return ContractVerdict(False, "aggregate requires grouped grain")
    # Every computed column must be in the compute grammar compute_pre and the
    # executor share. The Goal's own names count as known columns, so a
    # non-identifier name the Goal introduces still tokenises as one operand;
    # whether each operand exists is compute_pre's check.
    for name, expr in goal.derive:
        if _classify_derive(expr) is None and arith_parse(str(expr), names) is None:
            return ContractVerdict(
                False, f"derive {name!r}: expr {expr!r} is outside the compute grammar "
                       f"(+ - * /, parentheses, unary minus, numeric literals, column names)")
    return ContractVerdict(True, "ok")


# ── goal validity lints (S35) ────────────────────────────────────────────────

def lint_goal(goal: Goal, order_cols=()) -> ContractVerdict:
    """LINT 1 — dead derive. Every derive[] entry must be CONSUMED by at least one
    of: a where predicate, another derive's expr (which covers the aggregate, since
    aggregates live in derive[] as fn(col)), an order_by column, the group key, or
    columns[]. A derived column consumed by nothing indicates the emission dropped
    the use it was created for (e.g. a membership predicate) — the goal is still
    well-formed and REACHABLE, so the engine would faithfully execute it into a
    confident wrong answer. Refusing it up front converts that silent wrong answer
    into an honest refusal.

    A structural invariant of the Goal's own semantics: a correct Goal never has a
    dead derive, so well-formed goals cannot false-positive. Deterministic; no LLM.
    (Order closure — every order_by col is a real output — is the companion lint,
    enforced by order.validate_order.)
    """
    where_cols = {str(c) for c, _op, _v in goal.where}
    out_cols = {str(n) for n, _t in goal.columns}
    group_cols = set(goal.grouped_by or ())
    order_set = {str(c) for c in order_cols}
    for name, _expr in goal.derive:
        name = str(name)
        consumed = (name in where_cols or name in out_cols
                    or name in group_cols or name in order_set
                    or any(name in expr_refs(e2) for n2, e2 in goal.derive
                           if str(n2) != name))
        if not consumed:
            return ContractVerdict(
                False, f"dead derive: {name!r} is consumed by nothing "
                       f"(no where predicate, derive expr, order_by, group key, "
                       f"or output column uses it)")
    return ContractVerdict(True, "ok")
