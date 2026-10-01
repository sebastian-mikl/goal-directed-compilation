"""Operator-consistency check: does the planner's _pre agree with the executor?

Back-ported from the engine's follow-up branch (with the P53-P58 batch), trimmed to
this tree's four planner operators: that branch's keep_top window operator and its rules
(P29, P32, P37, P39) do not exist here. The compute oracle reads the shared
compute grammar's semantics independently (P15b): Python's own parser decides
the syntax, pandas' eval computes the value.

For each of the four planner operators (filter, select, aggregate, compute) this
enumerates a parameter space against typed fixture frames and, for every
(state, params):

  1. asks the planner:   accepted = OPERATORS[kind].pre(state, params)
  2. runs the executor EXACTLY as execute_plan does: op_to_primitive(op) ->
     run_primitive(kind, frame, params) -> post(frame)
  3. classifies the executor: OK / TYPED (the FilterTypeError family) / UNTYPED
  4. for OK results, compares the frame with an independent pandas ORACLE for the
     operator's declared semantics, and the frame's columns with the planner's
     own claim (eff(state).columns) -- the State is a shape descriptor and the
     executor's frame must have that shape.

The property under test:

  every input _pre accepts, the executor either handles correctly or refuses
  with a TYPED error -- never an untyped crash, never a silently different
  result, never a frame whose shape differs from the State's claim;

and, in the other direction, inputs the executor handles correctly that _pre
refuses are reported as over-refusals, separated into the deliberate ones (each
named by the rule that refuses it) and the rest.

Findings carry stable ids so evidence/tests/test_operator_consistency.py can
hold the recorded set: a new finding OR a silently-fixed one both flip the test.

usage: python scripts/operator_consistency.py [--json out.json] [--verbose]
Pure local: no model, no network; pandas only.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ast                                                    # noqa: E402

from gdc.integrate import op_to_primitive                 # noqa: E402
from gdc.operators import OPERATORS, _make_op             # noqa: E402
from gdc.primitives import (AGG_FN_ALIASES, FilterTypeError,  # noqa: E402
                            SUPPORTED_AGG_FNS, SUPPORTED_FILTER_OPS, run_primitive)
from gdc.state import State, normalise_op                 # noqa: E402

TYPED = FilterTypeError          # the executor's declared refusal family (tools_decl re-raises exactly this)


# ── fixtures ──────────────────────────────────────────────────────────────────
# One frame with every column kind the aliased register can carry, as the demo's
# aliased_view would type them ("float" for any numeric dtype, "str" otherwise).

def base_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "k":  ["A", "A", "B", "C", "C", "C"],                     # str key
        "s":  ["ann", "bob", "ann", "cev", "dan", "ann"],          # str, non-numeric
        "d":  ["2024-01-01", "2024-01-02", "2024-01-03",
               "2024-01-04", "2024-01-05", "2024-01-06"],          # ISO date strings
        "ns": ["1", "2", "3", "4", "5", "6"],                      # numeric STRINGS
        "x":  [1.0, 2.5, np.nan, 4.0, 4.0, 6.0],                   # float with NaN and a tie
        "i":  [10, 20, 30, 40, 50, 60],                            # int
    })


FIXTURES = {
    "base":   base_frame(),
    "empty":  base_frame().iloc[0:0].copy(),
    "single": base_frame().iloc[0:1].copy(),
}


def schema_of(frame: pd.DataFrame) -> dict:
    return {str(c): ("float" if pd.api.types.is_numeric_dtype(frame[c]) else "str") for c in frame.columns}


def numeric_content(series: pd.Series) -> bool:
    """The S36 criterion (_coerce_numeric_or_refuse): a column has numeric content
    unless coercion drops EVERY originally non-null value. An all-null column counts
    as numeric-capable (nothing to refuse)."""
    coerced = pd.to_numeric(series, errors="coerce")
    return not (series.notna().any() and not coerced.notna().any())


# ── oracle ────────────────────────────────────────────────────────────────────
# Independent pandas semantics for each operator, on the OUTPUT the State claims.
# Returns ("ok", frame) or ("typed", reason) meaning "a correct executor refuses".

class Oracle:
    @staticmethod
    def filter(frame, params):
        col, op, val = str(params["col"]), normalise_op(params["op"]), params["val"]
        if col not in frame.columns:
            return "typed", "column absent"
        if op not in SUPPORTED_FILTER_OPS:
            return "typed", "op unsupported"
        s = frame[col]
        numeric_col = pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
        if val is None:
            # SQL's IS NULL / IS NOT NULL: JSON null under == / != is a null test on
            # any column; under any other op it is not a value.
            if op in ("==", "!="):
                return "ok", frame[s.isna()] if op == "==" else frame[s.notna()]
            return "typed", "None as a value"
        if op in ("<", "<=", ">", ">="):
            try:
                f = float(val)
            except (TypeError, ValueError):
                return "typed", "ordered op with a non-numeric value"
            if not numeric_col and not numeric_content(s):
                return "typed", "ordered op on a column with no numeric content"
            n = pd.to_numeric(s, errors="coerce")
            mask = {"<": n < f, "<=": n <= f, ">": n > f, ">=": n >= f}[op]
            return "ok", frame[mask.fillna(False)]
        if op in ("==", "!="):
            if isinstance(val, (list, tuple, set, dict)):
                return "typed", "equality against a non-scalar"
            if numeric_col:
                try:
                    f = float(val)
                except (TypeError, ValueError):
                    return "typed", "equality of a numeric column with a non-numeric value"
                eq = (pd.to_numeric(s, errors="coerce") == f).fillna(False)
            else:
                eq = (s.astype(str) == str(val)) & s.notna()
            # != keeps NaN rows: the executor's policy, contract silent (P59, observed below, not judged here)
            return "ok", frame[eq] if op == "==" else frame[~eq]
        if op == "in":
            if not isinstance(val, (list, tuple, set)):
                return "typed", "membership against a non-list"
            vals = list(val)
            if numeric_col:
                nums = []
                for v in vals:
                    try:
                        nums.append(float(v))
                    except (TypeError, ValueError):
                        return "typed", "membership of a numeric column with a non-numeric value"
                return "ok", frame[pd.to_numeric(s, errors="coerce").isin(nums).fillna(False)]
            return "ok", frame[s.astype(str).isin([str(v) for v in vals]) & s.notna()]
        if op == "contains":
            if isinstance(val, (list, tuple, set, dict)):
                return "typed", "contains against a non-scalar"
            return "ok", frame[s.astype(str).str.contains(str(val), case=False, regex=False, na=False)]
        return "typed", "unreachable"

    @staticmethod
    def select(frame, params):
        cols = [str(c) for c in params["cols"]]
        if not cols:
            return "typed", "empty projection"
        missing = [c for c in cols if c not in frame.columns]
        if missing:
            return "typed", f"columns absent: {missing}"
        return "ok", frame[cols]

    @staticmethod
    def compute(frame, params):
        """The declared grammar (+ - * /, parentheses, unary minus, numeric
        literals, column names), decided by Python's parser and evaluated by
        pandas' eval over the numerically coerced operands: independent of
        gdc.primitives.arith_parse / safe_eval_expr. Fixture columns are plain
        identifiers, so no column-aware tokenisation is needed here."""
        new_col, expr = str(params["new_col"]), str(params["expr"])
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            return "typed", "expression outside the grammar"
        allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)
        names = set()
        for node in ast.walk(tree):
            if not isinstance(node, allowed) or (
                    isinstance(node, ast.Constant)
                    and (isinstance(node.value, bool) or not isinstance(node.value, (int, float)))):
                return "typed", "expression outside the grammar"
            if isinstance(node, ast.Name):
                names.add(node.id)
        if not names <= set(map(str, frame.columns)):
            return "typed", "column absent"
        if not all(numeric_content(frame[c]) for c in names):
            return "typed", "arithmetic on a column with no numeric content"
        env = pd.DataFrame({c: pd.to_numeric(frame[c], errors="coerce") for c in names},
                           index=frame.index)
        out = frame.copy()
        value = env.eval(expr, engine="python") if names else float(eval(expr))  # noqa: S307
        out[new_col] = value if isinstance(value, pd.Series) else float(value)
        return "ok", out

    @staticmethod
    def aggregate(frame, params):
        fn = AGG_FN_ALIASES.get(str(params["fn"]), str(params["fn"]))
        col, by, out_col = str(params["col"]), [str(b) for b in params["by"]], str(params["out_col"])
        if fn not in SUPPORTED_AGG_FNS:
            return "typed", "fn unsupported"
        if col not in frame.columns or any(b not in frame.columns for b in by):
            return "typed", "column absent"
        if out_col in by:
            return "typed", "out_col is a group key"
        s = frame[col]
        if fn not in ("count", "nunique"):
            if not numeric_content(s):
                return "typed", "numeric aggregate on a column with no numeric content"
            s = pd.to_numeric(s, errors="coerce")
        work = frame.copy()
        work[col] = s
        if not by:
            val = getattr(work[col], fn)()
            if fn in ("count", "nunique"):
                val = int(val)
            return "ok", pd.DataFrame({out_col: [val]})
        ser = work.groupby(by, sort=True)[col].agg(fn)
        out = ser.rename(out_col).reset_index()
        return "ok", out


# ── comparison helpers ────────────────────────────────────────────────────────

def rows_key(frame: pd.DataFrame) -> list:
    def norm(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "NaN"
        if isinstance(v, (float, np.floating)):
            return f"{float(v):.9g}"
        if isinstance(v, (int, np.integer)):
            return f"{int(v)}"
        return str(v)
    cols = list(map(str, frame.columns))
    return sorted(tuple(norm(v) for v in row) for row in frame[cols].astype(object).values.tolist())


def same_result(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    if set(map(str, a.columns)) != set(map(str, b.columns)):
        return False
    b = b[list(map(str, a.columns))] if list(map(str, a.columns)) != list(map(str, b.columns)) else b
    return rows_key(a) == rows_key(b)


def diagnose_mismatch(kind, frame, params, out, oracle) -> str:
    """Name the mechanism behind an executor/oracle disagreement, so one id per cause."""
    if kind == "filter":
        op, col = normalise_op(params["op"]), str(params["col"])
        numeric_col = pd.api.types.is_numeric_dtype(frame[col]) if col in frame.columns else False
        if op == "contains":
            return "contains is a regex match"
        if op == "!=" and col in out.columns and out[col].isna().any() and not oracle[col].isna().any():
            return "!= keeps NaN rows"
        if op in ("==", "!=") and numeric_col:
            return "numeric equality compared as strings (P46)"
        if op == "in":
            return "membership does not coerce value types"
        return "other"
    if kind == "aggregate":
        return "key column renamed" if any(str(c).endswith("__group") for c in out.columns) else "other"
    if kind == "select":
        return "empty projection passes the frame through" if not list(params["cols"]) else "other"
    return "other"


# ── deliberate refusals: rule names, so over-refusals can be told apart ───────

def deliberate_refusal(kind: str, state: State, params: dict) -> str | None:
    """Return the rule that refuses (state, params) on purpose, or None."""
    if kind == "filter":
        if not state.is_row_grain:
            return "filter needs row grain (HAVING after aggregate is a declared non-goal)"
        if normalise_op(params["op"]) not in SUPPORTED_FILTER_OPS:
            return "P21: op not in SUPPORTED_FILTER_OPS"
        from gdc.primitives import NUMERIC_STATE_TYPES, value_shape_ok, values_numeric_ok
        if not value_shape_ok(normalise_op(params["op"]), params["val"]):
            return "P54: the value's shape does not fit the op"
        ctype = dict(state.columns).get(str(params["col"]))
        if ctype in NUMERIC_STATE_TYPES and normalise_op(params["op"]) in ("==", "!=", "in") \
                and not values_numeric_ok(normalise_op(params["op"]), params["val"]):
            return "P54: a non-numeric value against a numeric column"
        conj = (str(params["col"]), normalise_op(params["op"]), params["val"])
        if conj in state.where:
            return "idempotence: conjunct already present"
    if kind == "aggregate":
        if str(params["out_col"]) in set(map(str, params["by"])):
            return "P23: out_col is a group key"
        if not state.is_row_grain:
            return "aggregate needs row grain"
        if AGG_FN_ALIASES.get(str(params["fn"]), str(params["fn"])) not in SUPPORTED_AGG_FNS:
            return "P22: fn not in SUPPORTED_AGG_FNS"
    return None


# ── parameter spaces ──────────────────────────────────────────────────────────

COLS = ["k", "s", "d", "ns", "x", "i", "ghost"]
VALS = [1, 4.0, "4", "ann", "2024-01-03", "max(x)", ["ann", "cev"], [4, 6], ["4"], None, "A",
        "(", "a+", ".", "an|ce"]          # regex-shaped strings: contains() must not read them as patterns
OPS = sorted(SUPPORTED_FILTER_OPS) + ["=", "eq", "gt", "like", "argmax_by", "IN"]


def params_filter():
    for col in COLS:
        for op in OPS:
            for val in VALS:
                yield {"col": col, "op": op, "val": val}


def params_select():
    yield from ({"cols": c} for c in (
        ("k",), ("k", "x"), ("x", "k"), ("k", "s", "d", "ns", "x", "i"), ("ghost",), ("k", "ghost"), ("k", "k"), ()))


COMPUTE_EXPRS = ["x + i", "x*i", "x - x", "x / i", "i / i", "x + s", "s + s", "d - x", "ns + i", "ns * ns",
                 "x + 1", "x", "x + i + i", "x + i*x", "sum(x)", "ghost + x", "x+i", "i - x", "k + k",
                 "(x + i) / 2", "-x", "3", "x ** 2", "x > i", "abs(x)"]


def params_compute():
    for new_col in ("y", "x", "k", "i"):
        for expr in COMPUTE_EXPRS:
            yield {"new_col": new_col, "expr": expr}


AGG_FNS = sorted(SUPPORTED_AGG_FNS) + sorted(AGG_FN_ALIASES) + ["median", "SUM", "first"]


def params_aggregate():
    for fn in AGG_FNS:
        for col in COLS:
            for by in ([], ["k"], ["k", "s"], ["ghost"], ["x"]):
                for out_col in ("agg", col, (by[0] if by else "k")):
                    yield {"fn": fn, "col": col, "by": list(by), "out_col": out_col}


PARAMS = {"filter": params_filter, "select": params_select, "compute": params_compute,
          "aggregate": params_aggregate}


# ── states: the base row state plus the ones each rule makes different ────────

def states_for(kind: str, schema: dict):
    base = State.from_base(schema)
    yield "base", base
    if kind == "filter":
        yield "after-overwrite-x", OPERATORS["compute"].eff(base, {"new_col": "x", "expr": "x + i"})
        yield "with-conjunct", OPERATORS["filter"].eff(base, {"col": "k", "op": "==", "val": "A"})
    if kind in ("filter", "aggregate"):
        yield "grouped", OPERATORS["aggregate"].eff(base, {"fn": "sum", "col": "x", "by": ["k"], "out_col": "agg"})


def frame_for(state_name: str, fixture: pd.DataFrame) -> pd.DataFrame:
    """The frame that corresponds to the state: apply the same steps to the data."""
    f = fixture
    if state_name == "after-overwrite-x":
        f = run_primitive("compute", f, {"new_col": "x", "expr": "x + i"})
    elif state_name == "with-conjunct":
        f = run_primitive("select", f, {"predicates": [{"col": "k", "op": "==", "value": "A"}]})
    elif state_name == "grouped":
        f = run_step("aggregate", {"fn": "sum", "col": "x", "by": ["k"], "out_col": "agg"}, f)
    return f


# ── the check ─────────────────────────────────────────────────────────────────

FINDING_CATEGORIES = ("UNTYPED-CRASH", "SILENT-RESULT", "SHAPE-DIVERGENCE", "OVER-REFUSAL")   # violate the property
OBSERVATION_CATEGORIES = ("LATE-REFUSAL",)        # inside the property: typed, but the planner could have refused (P60)


@dataclass
class Finding:
    id: str
    kind: str
    category: str          # one of FINDING_CATEGORIES or OBSERVATION_CATEGORIES
    detail: str
    examples: list = field(default_factory=list)
    count: int = 0

    @property
    def is_finding(self) -> bool:
        return self.category in FINDING_CATEGORIES


def run_step(kind: str, params: dict, frame: pd.DataFrame):
    """Dispatch and run exactly as execute_plan does (run_primitive, then post)."""
    exe_kind, exe_params, post = op_to_primitive(_make_op(kind, dict(params)))   # the planner's own Op
    out = run_primitive(exe_kind, frame, exe_params)
    if post is not None:
        out = post(out)
    return out


def check() -> tuple[dict, list[Finding]]:
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning)   # pandas' regex-group notice, reported as a finding instead
    tallies = defaultdict(Counter)
    findings: dict[str, Finding] = {}

    def add(fid, kind, category, detail, example):
        f = findings.setdefault(fid, Finding(fid, kind, category, detail))
        f.count += 1
        if len(f.examples) < 3:
            f.examples.append(example)

    for kind, gen in PARAMS.items():
        for fix_name, fixture in FIXTURES.items():
            schema = schema_of(fixture)
            for state_name, state in states_for(kind, schema):
                frame = frame_for(state_name, fixture)
                for params in gen():
                    try:
                        accepted = bool(OPERATORS[kind].pre(state, params))
                    except Exception as exc:  # noqa: BLE001 — a crashing _pre is itself a finding
                        add(f"{kind}:pre-crash:{type(exc).__name__}", kind, "UNTYPED-CRASH",
                            f"_pre raised {type(exc).__name__}", (fix_name, state_name, params, str(exc)[:80]))
                        continue
                    try:
                        out = run_step(kind, params, frame)
                        outcome = "OK"
                    except TYPED as exc:
                        outcome, out = "TYPED", exc
                    except Exception as exc:  # noqa: BLE001
                        outcome, out = "UNTYPED", exc
                    verdict, oracle = Oracle.__dict__[kind].__func__(frame, params)
                    ex = (fix_name, state_name, params)
                    if accepted:
                        if outcome == "UNTYPED":
                            add(f"{kind}:untyped:{type(out).__name__}", kind, "UNTYPED-CRASH",
                                f"_pre accepts; executor raises {type(out).__name__}", ex + (str(out)[:100],))
                            tallies[kind]["accepted/UNTYPED"] += 1
                        elif outcome == "TYPED":
                            tallies[kind]["accepted/TYPED"] += 1
                            if verdict == "ok":
                                add(f"{kind}:typed-but-oracle-ok:{type(out).__name__}", kind, "OVER-REFUSAL",
                                    f"_pre accepts and the oracle computes a result, but the executor refuses ({type(out).__name__})",
                                    ex + (str(out)[:100],))
                            else:
                                add(f"{kind}:late-refusal:{oracle}", kind, "LATE-REFUSAL",
                                    f"_pre accepts what the executor refuses (typed): {oracle}", ex)
                        else:
                            tallies[kind]["accepted/OK"] += 1
                            claimed = [c for c, _ in OPERATORS[kind].eff(state, params).columns]
                            got = list(map(str, out.columns))
                            if set(claimed) != set(got):
                                add(f"{kind}:shape-divergence", kind, "SHAPE-DIVERGENCE",
                                    "the State's columns after eff differ from the executor's frame columns",
                                    ex + (f"state={claimed} frame={got}",))
                            if verdict == "typed":
                                add(f"{kind}:silent:{oracle}", kind, "SILENT-RESULT",
                                    f"_pre accepts; a correct executor would refuse ({oracle}); this one returns a frame",
                                    ex + (f"rows={len(out)}/{len(frame)}",))
                            elif not same_result(out, oracle):
                                why = diagnose_mismatch(kind, frame, params, out, oracle)
                                add(f"{kind}:wrong-result:{why}", kind, "SILENT-RESULT",
                                    f"_pre accepts; the executor's frame differs from the oracle's: {why}",
                                    ex + (f"got={rows_key(out)[:3]} want={rows_key(oracle)[:3]}",))
                    else:
                        rule = deliberate_refusal(kind, state, params)
                        if outcome == "OK" and verdict == "ok" and same_result(out, oracle):
                            if rule:
                                tallies[kind][f"refused-deliberately/{rule}"] += 1
                            else:
                                add(f"{kind}:over-refusal", kind, "OVER-REFUSAL",
                                    "_pre refuses what the executor handles correctly, and no rule names the refusal", ex)
                                tallies[kind]["refused/executor-OK-unexplained"] += 1
                        else:
                            tallies[kind]["refused/consistent"] += 1
    return {k: dict(v) for k, v in tallies.items()}, sorted(findings.values(), key=lambda f: (f.kind, f.category, f.id))


# ── part 2: planned sequences, end to end ─────────────────────────────────────
# The single-step check compares each executor step with the State after one
# eff. A divergence can also surface only downstream: the frame the plan returns
# must carry exactly the columns the Goal declared. A handful of Goals through
# plan() + execute, one per operator combination.

SEQUENCE_GOALS = {
    "filter+select":            dict(columns={"k": "str", "x": "float"}, grain="row",
                                     where=[{"col": "k", "op": "==", "val": "A"}], derive=[]),
    "compute+select":           dict(columns={"k": "str", "y": "float"}, grain="row", where=[],
                                     derive=[{"name": "y", "expr": "x + i"}]),
    "aggregate by k":           dict(columns={"k": "str", "t": "float"}, grain={"grouped_by": ["k"]}, where=[],
                                     derive=[{"name": "t", "expr": "sum(x)"}]),
    "aggregate of the key":     dict(columns={"k": "str", "n": "int"}, grain={"grouped_by": ["k"]}, where=[],
                                     derive=[{"name": "n", "expr": "count(k)"}]),
    "aggregate of the key, two keys": dict(columns={"k": "str", "s": "str", "n": "int"}, grain={"grouped_by": ["k", "s"]},
                                     where=[], derive=[{"name": "n", "expr": "count(s)"}]),
    "global aggregate":         dict(columns={"t": "float"}, grain={"grouped_by": []}, where=[],
                                     derive=[{"name": "t", "expr": "sum(x)"}]),
    "chained compute+filter+aggregate": dict(columns={"k": "str", "m": "float"}, grain={"grouped_by": ["k"]},
                                     where=[{"col": "y", "op": ">", "val": 20}],
                                     derive=[{"name": "y", "expr": "(x + i) * 2 - 1"},
                                             {"name": "m", "expr": "avg(y)"}]),
}


def check_sequences() -> list[Finding]:
    from gdc import Goal, OrderSpec
    from gdc.integrate import aliased_view, run_goal_plan
    from gdc.search import Unreachable
    findings = []
    _, adf, schema = aliased_view(base_frame())
    for label, spec in SEQUENCE_GOALS.items():
        goal = Goal.make(**spec)
        declared = [c for c, _ in goal.columns]
        try:
            frame, plan = run_goal_plan(goal, OrderSpec.make(), adf, base_schema=schema)
        except TYPED as exc:
            findings.append(Finding(f"sequence:{label}:typed", "plan", "LATE-REFUSAL", f"{type(exc).__name__}: {exc}"[:160]))
            continue
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding(f"sequence:{label}:untyped:{type(exc).__name__}", "plan", "UNTYPED-CRASH", str(exc)[:160]))
            continue
        if isinstance(plan, Unreachable):
            findings.append(Finding(f"sequence:{label}:unreachable", "plan", "OVER-REFUSAL", plan.reason))
            continue
        got = list(map(str, frame.columns))
        if set(got) != set(declared):
            f = Finding(f"sequence:{label}:shape-divergence", "plan", "SHAPE-DIVERGENCE",
                        "the plan's final frame does not carry the Goal's declared columns")
            f.count = 1; f.examples = [(label, [o.kind for o in plan], f"goal={declared} frame={got}")]
            findings.append(f)
    return findings


def policy_observations() -> list[str]:
    """Contract-silent behaviours the check does not judge, measured so a change is noticed (P59)."""
    f = base_frame()
    out = run_primitive("select", f, {"predicates": [{"col": "x", "op": "!=", "value": 4.0}]})
    notes = [f"P59: `x != 4.0` keeps the NaN row ({int(out['x'].isna().sum())} of {int(f['x'].isna().sum())}); SQL would drop it; the contract is silent"]
    g = pd.DataFrame({"k": ["A", None, "A"], "v": [1.0, 2.0, 3.0]})
    agg = run_primitive("aggregate", g, {"group_by": "k", "value_col": "v", "agg": "sum"})
    notes.append(f"P59: aggregate drops the NaN-key group ({len(agg)} group(s) of 2)")
    return notes


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tallies, findings = check()
    findings = findings + check_sequences()
    print("operator-consistency check — planner _pre vs executor, four operators")
    for kind in PARAMS:
        print(f"\n== {kind}")
        for k, n in sorted(tallies.get(kind, {}).items()):
            print(f"   {n:6}  {k}")
    real = [f for f in findings if f.is_finding]
    obs = [f for f in findings if not f.is_finding]
    print(f"\n== findings (property violations): {len(real)}")
    if not real:
        print("   none")
    for f in real:
        print(f"   [{f.category}] {f.id}  ×{f.count}\n      {f.detail}")
        if "--verbose" in argv:
            for ex in f.examples:
                print(f"      e.g. {ex}")
    print(f"\n== observations (inside the property): {len(obs)} late-refusal classes (P60) + policy notes (P59)")
    for f in obs:
        print(f"   [{f.category}] {f.id}  ×{f.count}\n      {f.detail}")
        if "--verbose" in argv:
            for ex in f.examples:
                print(f"      e.g. {ex}")
    for line in policy_observations():
        print(f"   [POLICY] {line}")
    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        dump = lambda fs: [{"id": f.id, "kind": f.kind, "category": f.category, "detail": f.detail,
                            "count": f.count, "examples": [str(e) for e in f.examples]} for f in fs]
        out.write_text(json.dumps({"tallies": tallies, "findings": dump(real), "observations": dump(obs),
                                   "policy": policy_observations()}, indent=1, default=str))
        print(f"\nfull detail -> {out}")
    return 1 if real else 0


if __name__ == "__main__":
    sys.exit(main())
