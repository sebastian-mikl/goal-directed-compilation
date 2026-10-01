"""Code-agent tools: `run_python` and `final_answer` over the register dataframe.

`df` is the domain's register; `pd` is pandas. The agent is asked to set a
variable `result` to either a pandas DataFrame, a pandas Series, or a scalar.
One namespace serves the whole episode (one query, one seed): what a call
defines, `result` included, is still there for the next run_python call and for
final_answer, as the model would expect of a notebook.
"""
from __future__ import annotations

import ast

import pandas as pd


def code_tools(n_rows: int) -> list[dict]:
    """The two tool definitions for a register of `n_rows` rows. They precede
    `system` in the cached prefix, so they depend on nothing but the domain's row
    count: the same bytes for every query and seed of that domain."""
    return [
        {"name": "run_python",
         "description": (f"Run pandas/python over the register. `df` is the {n_rows}-row "
                         "register; `pd` is pandas. Variables persist across run_python "
                         "and final_answer calls for this query. Set `result` to a "
                         "DataFrame (or Series, or scalar) holding the answer; a compact "
                         "summary is returned."),
         "input_schema": {"type": "object", "properties": {"code": {"type": "string"}},
                          "required": ["code"]}},
        {"name": "final_answer",
         "description": ("Run final code that sets `result`; result becomes the "
                         "final answer. Stops the loop."),
         "input_schema": {"type": "object", "properties": {"code": {"type": "string"}},
                          "required": ["code"]}},
    ]


def new_namespace(df: pd.DataFrame) -> dict:
    """A fresh episode namespace: `pd`, and `df` as the episode's own copy of the
    register (agent code may change it; the adapter's register is never touched)."""
    return {"pd": pd, "df": df.copy()}


def index_to_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Lift a label-carrying index into columns so to_dict("records") keeps it.

    A RangeIndex, or an unnamed integer index (row positions left by a filter or a
    sort), is positional: it is left alone and to_dict("records") drops it, as
    before. A named index, a MultiIndex, or an unnamed non-integer index (from
    value_counts(), groupby().agg(), set_index(), pd.DataFrame({...: series}))
    carries the group key, so it becomes a column. A level whose name is already a
    column is dropped rather than duplicated. If the lift cannot be done (an index
    level would land on an existing column name), the frame is returned as it was.
    """
    idx = frame.index
    if (not isinstance(idx, pd.MultiIndex) and idx.name is None
            and pd.api.types.is_integer_dtype(idx.dtype)):
        return frame
    dup = [i for i, n in enumerate(idx.names) if n is not None and n in frame.columns]
    try:
        if len(dup) == idx.nlevels:
            return frame.reset_index(drop=True)
        if dup:
            frame = frame.reset_index(level=dup, drop=True)
        return frame.reset_index()
    except ValueError:                              # the default name (index, level_0) is taken
        return frame


def run_code(code: str, ns: dict):
    """Exec agent code in the episode namespace `ns` (from new_namespace), which the
    caller keeps for the whole episode. Returns (summary_text, result_obj|None).

    The object is whatever `result` holds after this call. If this call did not
    assign it, that is an earlier call's value (perhaps changed in place since),
    and the summary says so: the model never reads an earlier answer as this
    call's. A call that raises returns no object, whatever `result` held."""
    before = ns.get("result")
    try:
        tree = ast.parse(code, "<agent>")
        exec(compile(tree, "<agent>", "exec"), ns)
    except Exception as exc:                       # noqa: BLE001 — feed errors back
        return f"error: {type(exc).__name__}: {exc}", None
    result = ns.get("result")
    if result is None:
        return "ran OK but `result` was not set.", None
    note = ""
    if result is before and not _binds_result(tree):
        note = ("ran OK but this call did not assign `result`; it holds the value an "
                "earlier call gave it, now:\n")
    if isinstance(result, pd.DataFrame):
        # Show the group key the same way df_to_result will score it: a label-
        # carrying index is lifted into a column, else the summary hides it.
        return note + _summary(index_to_columns(result), "result"), result
    if isinstance(result, pd.Series):
        # Pass the raw Series through — df_to_result handles reset_index so the
        # group key (Series index name) survives. to_frame() would have buried it.
        return note + _summary(result.reset_index(), "result"), result
    return note + f"result = {result!r}", result


def _binds_result(tree: ast.AST) -> bool:
    """True if the code binds the module-level name `result`: an assignment target
    (=, +=, :=, for, with ... as), `del`, or `global result` inside a function. A
    function's or class's own local `result` does not count. run_code pairs this
    with an identity check, which alone would miss a rebinding to the same object
    (`result = result`, or a small int computed twice)."""
    todo = list(ast.iter_child_nodes(tree))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            if any(isinstance(n, ast.Global) and "result" in n.names for n in ast.walk(node)):
                return True
            continue
        if (isinstance(node, ast.Name) and node.id == "result"
                and isinstance(node.ctx, (ast.Store, ast.Del))):
            return True
        todo.extend(ast.iter_child_nodes(node))
    return False


def _summary(frame: pd.DataFrame, name: str, n: int = 5) -> str:
    cols = [str(c) for c in frame.columns]
    try:
        head = frame.head(n).to_dict("records")
    except Exception:
        head = "<unprintable>"
    return (f"frame '{name}': {len(frame)} rows x {len(cols)} cols {cols}\n"
            f"head: {head}")
