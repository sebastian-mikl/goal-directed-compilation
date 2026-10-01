"""State descriptor + Goal descriptor + fingerprint + satisfies.

A State is the SHAPE of a relation — columns, grain, accumulated predicates, derived
columns. It NEVER carries data (SPEC: states are descriptors). Two states with the
same fingerprint are interchangeable for planning.

Canonicalisation rules (used by both fingerprint and satisfies):
  - operators ('=' vs '==', 'gt' vs '>') normalised to a fixed spelling;
  - columns/conjuncts/derive entries SORTED at fingerprint time so input order
    cannot change identity;
  - values stringified via `repr` so two different objects with the same Python
    `repr` collide — full value-equivalence normalisation is out of scope.

Module exposes: State, Goal, fingerprint, satisfies, OP_CANON, normalise_op.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any


# ── operator-string canonicalisation ─────────────────────────────────────────

OP_CANON: dict[str, str] = {
    "=": "==", "==": "==", "eq": "==",
    "!=": "!=", "<>": "!=", "ne": "!=",
    "<":  "<",  "lt": "<",
    "<=": "<=", "le": "<=",
    ">":  ">",  "gt": ">",
    ">=": ">=", "ge": ">=",
    "in": "in",
    "contains": "contains",
}


def normalise_op(op: str) -> str:
    """Map an operator spelling to its canonical form. Unknown ops pass through
    lower-cased — the planner treats them as opaque but consistent."""
    s = str(op).strip().lower()
    return OP_CANON.get(s, s)


# ── descriptors ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class State:
    """SHAPE descriptor of a relation. Never materialises data.

    columns:    ordered map name -> type, stored as a tuple of (name, type) pairs
                (declaration order preserved; fingerprint canonicalises).
    grouped_by: None for row-grain; tuple[str,...] for grouped grain.
    where:      conjunction of comparisons; stored as a tuple of (col, op, val)
                triples (`op` already normalised, `val` opaque).
    derive:     map name -> expr, stored as a tuple of (name, expr) pairs
                (declaration order preserved; fingerprint canonicalises).
    """
    columns: tuple[tuple[str, str], ...]
    grouped_by: tuple[str, ...] | None
    where: tuple[tuple[str, str, Any], ...]
    derive: tuple[tuple[str, str], ...]

    @property
    def is_row_grain(self) -> bool:
        return self.grouped_by is None

    @property
    def column_names(self) -> set[str]:
        return {n for n, _ in self.columns}

    @property
    def derive_map(self) -> dict[str, str]:
        return dict(self.derive)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self)

    @classmethod
    def from_base(cls, columns: list[tuple[str, str]] | dict[str, str]) -> "State":
        """Start state: declared columns, row grain, no predicates or derives."""
        cols = (tuple(columns.items()) if isinstance(columns, dict)
                else tuple((str(n), str(t)) for n, t in columns))
        return cls(columns=cols, grouped_by=None, where=(), derive=())


@dataclass(frozen=True)
class Goal:
    """Declarative goal — the shape of the result relation. **No ordering field.**

    Same field semantics as State; the planner treats Goal as a structural
    sub-condition (see `satisfies`). The Goal is always passed to `plan` as a
    descriptor: never an operator list, never an ordering hint.
    """
    columns: tuple[tuple[str, str], ...]
    grouped_by: tuple[str, ...] | None
    where: tuple[tuple[str, str, Any], ...]
    derive: tuple[tuple[str, str], ...]

    @classmethod
    def make(cls, *, columns, grain, where, derive) -> "Goal":
        """Build a Goal from a small DSL. Accepts:
          - `columns`: a dict {name: type}, or a list of either bare names
            (typeless → 'unknown') or (name, type) pairs;
          - `grain`: 'row' / None for row grain, or {'grouped_by': [...]} /
            iterable of cols for grouped grain;
          - `where`:  list of {'col', 'op', 'val'} dicts;
          - `derive`: list of {'name', 'expr'} dicts or (name, expr) pairs.
        """
        if grain == "row" or grain is None:
            gb = None
        elif isinstance(grain, dict) and "grouped_by" in grain:
            gb = tuple(grain["grouped_by"])
        else:
            gb = tuple(grain)

        if isinstance(columns, dict):
            cols = tuple((str(n), str(t)) for n, t in columns.items())
        else:
            normed = []
            for c in columns:
                if isinstance(c, str):
                    normed.append((c, "unknown"))
                else:
                    n, t = c
                    normed.append((str(n), str(t)))
            cols = tuple(normed)

        w = tuple((str(c["col"]), normalise_op(c["op"]), c["val"]) for c in where)
        d = tuple((str(e["name"] if isinstance(e, dict) else e[0]),
                   str(e["expr"] if isinstance(e, dict) else e[1])) for e in derive)
        return cls(columns=cols, grouped_by=gb, where=w, derive=d)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self)


# ── canonical form + fingerprint ─────────────────────────────────────────────

def _canon_columns(cols) -> list:
    """Sort columns by name (declaration order is cosmetic for fingerprinting)."""
    return sorted(([str(n), str(t)] for n, t in cols), key=lambda x: x[0])


def _canon_val(v) -> str:
    """Canonical string for a comparison value. Sequence values (list/tuple — e.g.
    the membership set of an `in [..]` filter) are order-normalised, so (a) a list
    goal-value canonicalises identically to the tuple the operator stores in `eff`,
    and (b) member order is irrelevant. Scalars fall through to `repr`."""
    if isinstance(v, (list, tuple)):
        return repr(tuple(sorted(v, key=repr)))
    return repr(v)


def _canon_where(where) -> list:
    """Sort conjuncts by (col, normalised_op, canonical val). Operator spelling is
    normalised at construction time, but normalise again here to be defensive."""
    return sorted(
        ([str(c), normalise_op(o), _canon_val(v)] for c, o, v in where),
        key=lambda x: (x[0], x[1], x[2]),
    )


def _canon_derive(derive) -> list:
    """Sort derive entries by name; expressions are opaque strings."""
    return sorted(([str(n), str(e)] for n, e in derive), key=lambda x: x[0])


def _canonical_form(obj: State | Goal) -> dict:
    """The single canonical dict used for both fingerprint and satisfies."""
    return {
        "columns":    _canon_columns(obj.columns),
        "grouped_by": (None if obj.grouped_by is None else sorted(map(str, obj.grouped_by))),
        "where":      _canon_where(obj.where),
        "derive":     _canon_derive(obj.derive),
    }


def fingerprint(obj: State | Goal) -> str:
    """Deterministic SHA256 over the canonical form. Two State/Goal instances
    that differ only in column / conjunct / derive ORDER produce the same hash
    (V5). Two that differ structurally never collide (V4 determinism is the
    consequence: identical input → identical bytes → identical hash)."""
    payload = json.dumps(_canonical_form(obj), sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ── structural goal-condition test ───────────────────────────────────────────

def effective_output_columns(goal: Goal) -> set[str]:
    """The column set a satisfying state must produce EXACTLY.

    Row grain: the declared goal.columns. Grouped grain: goal.columns PLUS the
    grain keys — a grouped relation's keys are definitionally part of its output
    (a grouped result without its keys is meaningless), so naming a column in
    grouped_by counts as requesting it even when the emission omits it from
    columns[].
    """
    cols = {str(n) for n, _ in goal.columns}
    if goal.grouped_by is not None:
        cols |= set(map(str, goal.grouped_by))
    return cols


def satisfies(state: State, goal: Goal) -> bool:
    """True iff:
      - grain is equal (both row, or same grouped_by set);
      - state.columns == the goal's effective output columns, EXACTLY (by name;
        see effective_output_columns — grain keys count as requested);
      - goal.where ⊆ state.where (canonicalised triples);
      - goal.derive ⊆ state.derive (canonicalised name → expr).

    Column satisfaction is EXACT set equality, not subset (S42 / Spider Fix 3).
    Under the original subset test a filter-only plan already "satisfied" any
    narrower goal, so the terminal projection never had to fire and every
    unrequested register column leaked into the output — surfaced live by
    Spider world_04 (a 1-column goal answered with the full 15-column row) and
    present, comparator-masked, across every hand-built domain. where/derive
    stay subset checks: extra predicates or derives narrow/annotate the result
    without changing which columns it exposes.
    """
    if (goal.grouped_by is None) != (state.grouped_by is None):
        return False
    if goal.grouped_by is not None:
        if set(map(str, goal.grouped_by)) != set(map(str, state.grouped_by)):
            return False
    if state.column_names != effective_output_columns(goal):
        return False
    sw = {tuple(t) for t in _canon_where(state.where)}
    gw = {tuple(t) for t in _canon_where(goal.where)}
    if not gw <= sw:
        return False
    sd = {tuple(t) for t in _canon_derive(state.derive)}
    gd = {tuple(t) for t in _canon_derive(goal.derive)}
    return gd <= sd
