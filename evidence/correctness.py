"""Correctness axis: compare the system's produced terminal output against a
reference answer computed independently in pure pandas.

Public entry points:
  - check(produced, expected): tolerant comparison returning (correct, detail).
  - values_equal(produced, expected): the same comparison as a bare bool.

Tolerance rationale: the system writes DataFrames whose row order, index, and
dtypes are not guaranteed to match a hand-written reference (a groupby may
order groups differently; an int column may come back float). So DataFrame
equality aligns columns, sorts rows by all columns, resets the index, and
compares values with check_dtype=False and a float tolerance. Scalars compare
with a small relative+absolute tolerance.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


_RTOL = 1e-5
_ATOL = 1e-6


# ── Resolve the system's produced output ─────────────────────────────────────

def _frames_equal(a: pd.DataFrame, b: pd.DataFrame) -> tuple[bool, str]:
    cols_a, cols_b = set(map(str, a.columns)), set(map(str, b.columns))
    if cols_a != cols_b:
        only_p = sorted(cols_a - cols_b)
        only_e = sorted(cols_b - cols_a)
        return False, (f"column mismatch (produced-only={only_p}, "
                       f"expected-only={only_e})")
    if len(a) != len(b):
        return False, f"row count differs: produced={len(a)} expected={len(b)}"

    # Align column order to the produced frame, then sort rows by all columns
    # (stringified, so mixed/NaN values sort deterministically) and reset index.
    order = list(a.columns)
    b = b[order]
    try:
        a_sorted = a.sort_values(order, key=lambda s: s.astype(str)).reset_index(drop=True)
        b_sorted = b.sort_values(order, key=lambda s: s.astype(str)).reset_index(drop=True)
    except Exception:
        a_sorted = a.reset_index(drop=True)
        b_sorted = b.reset_index(drop=True)

    try:
        pd.testing.assert_frame_equal(
            a_sorted, b_sorted,
            check_dtype=False, check_like=True,
            check_index_type=False, rtol=_RTOL, atol=_ATOL,
        )
        return True, "DataFrame match"
    except AssertionError as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else "values differ"
        return False, f"value mismatch: {first[:200]}"


def _scalars_equal(a: Any, b: Any) -> tuple[bool, str]:
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return (a == b), ("scalar match" if a == b else f"scalar differ: {a!r} != {b!r}")
    import math
    if math.isnan(fa) and math.isnan(fb):
        return True, "scalar match (nan)"
    ok = abs(fa - fb) <= (_ATOL + _RTOL * abs(fb))
    return ok, ("scalar match" if ok else f"scalar differ: {fa} != {fb}")


def check(produced: Any, expected: Any) -> tuple[bool, str]:
    """Tolerant equality. Returns (correct, detail). Never raises."""
    if produced is None:
        return False, "no produced output to compare"

    # Normalise Series to single-column frames / scalars where sensible.
    if isinstance(expected, pd.Series):
        expected = expected.to_frame()
    if isinstance(produced, pd.Series):
        produced = produced.to_frame()

    if isinstance(expected, pd.DataFrame):
        if not isinstance(produced, pd.DataFrame):
            return False, f"type mismatch: produced {type(produced).__name__}, expected DataFrame"
        try:
            return _frames_equal(produced, expected)
        except Exception as exc:  # noqa: BLE001 — comparison must not crash the run
            return False, f"compare error: {exc}"

    # Scalar / other
    if isinstance(produced, pd.DataFrame):
        # A 1x1 frame can still match a scalar
        if produced.shape == (1, 1):
            return _scalars_equal(produced.iat[0, 0], expected)
        return False, f"type mismatch: produced DataFrame{produced.shape}, expected scalar"
    return _scalars_equal(produced, expected)


# ── Value-based (label-agnostic) comparison — S7 ─────────────────────────────
#
# A correct multi-hop chain may NAME its derived column differently from the
# oracle (Cost_Difference vs cost_diff, Total_Cost vs total). The column label is
# cosmetic; the VALUES are the answer. values_equal compares the value matrices
# only — shapes must match, then columns and rows are canonicalised by content
# (so label and order differences are ignored) and cells compared with float
# tolerance. A real divergence (different numbers, extra/missing column, wrong
# shape) still fails — that is the signal S7 triage wants surfaced cleanly.

def _cell_sort_key(x):
    import math
    if isinstance(x, float):
        return (0, x) if not math.isnan(x) else (2, 0.0)
    if x is None:
        return (3, "")
    return (1, str(x))


def _canonical_matrix(df: pd.DataFrame):
    """Return df's values as a list-of-rows, with columns ordered by content and
    rows sorted — so two frames carrying the same values (under any labels / order)
    canonicalise identically. Numeric columns are kept as floats; others as str."""
    typed_cols = []
    for c in df.columns:
        s = df[c]
        num = pd.to_numeric(s, errors="coerce")
        if len(s) > 0 and num.notna().all():
            typed_cols.append((1, [float(v) for v in num]))
        else:
            typed_cols.append((0, [None if pd.isna(v) else str(v) for v in s]))

    def _col_key(col):
        is_num, vals = col
        return (is_num, tuple(sorted((_cell_sort_key(v) for v in vals))))

    order = sorted(range(len(typed_cols)), key=lambda i: _col_key(typed_cols[i]))
    nrows = len(df)
    matrix = []
    for r in range(nrows):
        matrix.append([typed_cols[ci][1][r] for ci in order])
    matrix.sort(key=lambda row: tuple(_cell_sort_key(v) for v in row))
    return matrix


def _matrices_equal(A, B, rtol: float, atol: float) -> bool:
    """Cell-wise equality of two canonical matrices (floats with tolerance,
    everything else by string). Assumes equal dimensions."""
    import math
    if len(A) != len(B):
        return False
    for i in range(len(A)):
        if len(A[i]) != len(B[i]):
            return False
        for a, b in zip(A[i], B[i]):
            if isinstance(a, float) and isinstance(b, float):
                if math.isnan(a) and math.isnan(b):
                    continue
                if not abs(a - b) <= (atol + rtol * abs(b)):
                    return False
            elif str(a) != str(b):
                return False
    return True


def values_equal(produced: Any, expected: Any,
                 rtol: float = _RTOL, atol: float = _ATOL,
                 tolerate_extra_cols: bool = False) -> tuple[bool, str]:
    """Label-agnostic VALUE equality (S7). Same values ⇒ correct, regardless of
    column names or row/column order. Never raises.

    tolerate_extra_cols (shape-tolerant, value-strict — the eval/react-baseline
    scorer): a produced frame is correct iff SOME column-subset of it value-matches
    the oracle under the same label/order-agnostic comparison, with the SAME row
    count. Passes free-form output carrying the right values plus stray columns;
    still FAILS missing value columns, wrong numbers, or a wrong row count."""
    if produced is None:
        return False, "no produced output to compare"
    if isinstance(expected, pd.Series):
        expected = expected.to_frame()
    if isinstance(produced, pd.Series):
        produced = produced.to_frame()
    if not isinstance(expected, pd.DataFrame):
        return _scalars_equal(produced, expected)
    if not isinstance(produced, pd.DataFrame):
        return False, f"type mismatch: produced {type(produced).__name__}, expected DataFrame"

    pe_rows, pe_cols = produced.shape
    ex_rows, ex_cols = expected.shape
    if pe_rows != ex_rows:
        return False, f"row count differs: produced={pe_rows} expected={ex_rows}"
    try:
        B = _canonical_matrix(expected)
    except Exception as exc:  # noqa: BLE001
        return False, f"canonicalise error (expected): {exc}"

    if not tolerate_extra_cols:
        if pe_cols != ex_cols:
            return False, f"shape differs: produced {produced.shape} expected {expected.shape}"
        try:
            A = _canonical_matrix(produced)
        except Exception as exc:  # noqa: BLE001
            return False, f"canonicalise error (produced): {exc}"
        return ((True, "values match (labels ignored)") if _matrices_equal(A, B, rtol, atol)
                else (False, "value mismatch"))

    if pe_cols < ex_cols:
        return False, (f"missing value columns: produced has {pe_cols} col(s), "
                       f"expected {ex_cols}")
    import itertools
    import math as _m
    if _m.comb(pe_cols, ex_cols) > 50000:
        return False, f"too many columns to subset-search ({pe_cols} choose {ex_cols})"
    for combo in itertools.combinations(range(pe_cols), ex_cols):
        try:
            A = _canonical_matrix(produced.iloc[:, list(combo)])
        except Exception:  # noqa: BLE001
            continue
        if _matrices_equal(A, B, rtol, atol):
            extra = pe_cols - ex_cols
            note = f", {extra} extra col(s) tolerated" if extra else ""
            return True, f"values match on {ex_cols}-col subset (labels ignored{note})"
    return False, (f"no {ex_cols}-col subset of produced ({pe_cols} cols) "
                   f"value-matches the oracle")
