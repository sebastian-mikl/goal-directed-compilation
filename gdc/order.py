"""Ordering-as-projection (S13 Part A).

Order is PRESENTATION, not identity. So `OrderSpec` lives entirely OUTSIDE `Goal`
and must never enter `fingerprint()`, `satisfies()`, or `plan()` — two requests that
differ only in ordering ("top 5 by X" vs "all by X") share one Goal, one fingerprint,
one cached relation, and differ only here. `apply_order` realises the ordering on the
OUTPUT rows (a DataFrame), never on a State.

Nothing in this module imports or touches the planner core (state/operators/search);
it depends only on the typed-error surface in contract.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .contract import ContractVerdict
from .state import Goal

_DIRS = ("asc", "desc")


@dataclass(frozen=True)
class OrderSpec:
    """Presentation order for a result relation — SEPARATE from Goal.

    order_by: tuple of (col, dir) pairs, dir ∈ {"asc","desc"}; applied left-to-right
              as a stable multi-key sort.
    limit:    None (keep all) or an int row cap.
    """
    order_by: tuple[tuple[str, str], ...] = ()
    limit: int | None = None

    @classmethod
    def make(cls, order_by=(), limit=None) -> "OrderSpec":
        ob = []
        for o in order_by:
            if isinstance(o, dict):
                col, direction = o["col"], o.get("dir", "asc")
            else:
                col, direction = o[0], (o[1] if len(o) > 1 else "asc")
            ob.append((str(col), str(direction)))
        return cls(order_by=tuple(ob), limit=(None if limit is None else int(limit)))

    @property
    def is_identity(self) -> bool:
        return not self.order_by and self.limit is None


def apply_order(rows: pd.DataFrame, spec: OrderSpec) -> pd.DataFrame:
    """Stable multi-key sort (respecting each key's dir) then limit, on output rows.
    Pure: returns a new frame, never mutates State and never consults a Goal."""
    from gdc.primitives import stable_sort
    out = rows
    if spec.order_by:
        cols = [c for c, _ in spec.order_by]
        ascending = [d != "desc" for _, d in spec.order_by]
        # Stable, so ties keep input order (deterministic presentation). The
        # executor's own sort (gdc.primitives.stable_sort, which prim_sort runs on
        # the live path), so this documented rule and the live one are one code path.
        out = stable_sort(out, cols, ascending)
    if spec.limit is not None:
        out = out.head(spec.limit)
    return out.reset_index(drop=True)


def validate_order(spec: OrderSpec, goal: Goal) -> ContractVerdict:
    """Contract for ordering: every order_by col is an output column of the goal,
    each dir is valid, and limit is None or ≥ 0. Returns a verdict (never raises).

    The "goal output" set includes the goal's declared columns AND every derived
    column name (`goal.derive`): a row-level derive (e.g. `gap = cost - emv`) or a
    grouped aggregate's out_col is a legitimate sort key the executor produces, even
    when the LLM lists it only under `derive` and not under `columns`."""
    goal_cols = {n for n, _ in goal.columns} | {str(name) for name, _ in goal.derive}
    for col, direction in spec.order_by:
        if col not in goal_cols:
            return ContractVerdict(False, f"order_by column not in goal output: {col!r}")
        if direction not in _DIRS:
            return ContractVerdict(False, f"invalid sort direction: {direction!r}")
    if spec.limit is not None and spec.limit < 0:
        return ContractVerdict(False, f"negative limit: {spec.limit}")
    return ContractVerdict(True, "ok")
