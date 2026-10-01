"""A* / uniform-cost forward search.

From `start`, expand every operator (from the goal-derived universe) whose
`pre` holds; stop at `satisfies(state, goal)`; return the operator sequence.
On exhaustion return `Unreachable` (a typed return — the future Call-2 seam,
NOT a fall-back LLM call here).

Determinism (V4):
  - operator universe is canonical (operators.enumerate_ops);
  - priority queue uses a strict insertion counter as the tiebreak, so identical
    inputs produce identical pop orders;
  - closed set is keyed by State.fingerprint, so structurally-identical states
    are visited once regardless of how they were reached.

Heuristic (inadmissible-but-greedy, S36 FIX 3c): counts the number of unmet
goal conditions, but a single operator can satisfy MULTIPLE units at once —
`aggregate_eff` (operators.py) sets grouped_by, columns AND derive in one op,
clearing up to three counted units. So the heuristic over-estimates remaining
cost on those steps, breaking admissibility; the search finds a satisfying
plan QUICKLY but is not proved to find the SHORTEST one. Refusal correctness
rests on the closed-set exhaustion gate (every reachable state under
`max_expansions` has been popped and none satisfies the goal), NOT on the
admissibility guarantee.
"""
from __future__ import annotations

import heapq
import time
from dataclasses import dataclass
from typing import Iterable

from .state import State, Goal, satisfies, _canon_where, _canon_derive
from .operators import Op, OPERATORS, apply, pre_holds, enumerate_ops


@dataclass(frozen=True)
class Unreachable:
    """Typed unreachable result — the future Call-2 seam. The planner does NOT
    call an LLM; callers may use this signal to decide what to do next."""
    goal_fingerprint: str
    expanded: int
    reason: str

    def __bool__(self) -> bool:
        return False


def _unmet(state: State, goal: Goal) -> int:
    """Count of goal conditions not yet satisfied by `state`: grouping mismatch (1
    if grouped_by differs), missing column names, missing where predicates, missing
    derive entries. INADMISSIBLE-BUT-GREEDY (S36 FIX 3c): the "each op clears at
    most one unit" claim is false — `aggregate_eff` sets grouped_by + adds the
    aggregate column + adds the derive entry in a single step, so it can satisfy
    up to three counted units in one op. The heuristic therefore over-estimates
    remaining cost on aggregate steps and the search may return a satisfying plan
    that is not the shortest one. It can also UNDER-estimate: only MISSING goal
    columns are counted, so a state carrying extra columns has h=0 yet still
    needs the terminal select under exact-column satisfaction (state.satisfies,
    S42). Determinism + refusal correctness still hold either way: they rest on
    closed-set exhaustion under `max_expansions`, not on h being a true lower
    bound."""
    miss = 0
    if (goal.grouped_by is None) != (state.grouped_by is None):
        miss += 1
    elif goal.grouped_by is not None and set(map(str, goal.grouped_by)) != set(
            map(str, state.grouped_by)):
        miss += 1
    miss += len({n for n, _ in goal.columns} - state.column_names)
    sw = {tuple(t) for t in _canon_where(state.where)}
    gw = {tuple(t) for t in _canon_where(goal.where)}
    miss += len(gw - sw)
    sd = {tuple(t) for t in _canon_derive(state.derive)}
    gd = {tuple(t) for t in _canon_derive(goal.derive)}
    miss += len(gd - sd)
    return miss


def plan(start: State, goal: Goal,
         max_expansions: int = 1024, *, trace: list | None = None,
         stats: dict | None = None) -> list[Op] | Unreachable:
    """Forward A* search. Returns the operator sequence whose application via
    `eff` yields a state satisfying the goal, or `Unreachable` on exhaustion.

    `trace` (optional): a list the search APPENDS event dicts to (start / pop /
    expand / result). It is pure observation — `trace=None` (default) is zero
    behaviour change: identical return type, results, and determinism. The return
    is NEVER branched on `trace`; the per-op `pre` flag in every expand event makes
    precondition gating directly observable.

    `stats` (optional): a dict the search POPULATES with opt-in measurement of the
    search itself — 'nodes_expanded' (states popped and expanded), 'max_frontier'
    (peak priority-queue size, measured after each expansion), 'search_ms'
    (wall-clock of THIS plan() call only — excludes the LLM emission and the data
    execution, which happen outside plan()), and 'reachable'. `stats=None`
    (default) is zero overhead and byte-identical behaviour; the search never
    reads it back, so it cannot affect results or determinism. The DataFrame never
    enters plan() (the start State carries only column descriptors), so these
    numbers are functions of the GOAL, not the data size.

    Invariants enforced by construction:
      - No LLM is called anywhere in this function (or its dependencies).
      - The goal is read as a declarative descriptor — never as an operator list
        or an ordering hint.
      - No operator ordering is hardcoded; order emerges only from `pre`.
      - States are descriptors; data is never materialised.
    """
    _t0 = time.perf_counter() if stats is not None else 0.0
    _max_frontier = 0
    ops_universe = enumerate_ops(goal)
    if trace is not None:
        trace.append({"ev": "start", "start_fp": start.fingerprint,
                      "goal_fp": goal.fingerprint,
                      "universe": [repr(op) for op in ops_universe]})

    # Priority queue: (f, counter, state, plan_tuple). The counter is a strict
    # FIFO tiebreak so two pushes with the same f pop in insertion order — the
    # source of determinism (V4).
    pq: list = []
    counter = 0
    closed: set[str] = set()

    heapq.heappush(pq, (_unmet(start, goal), counter, start, ()))
    counter += 1
    if stats is not None:
        _max_frontier = 1

    expanded = 0
    while pq and expanded < max_expansions:
        f, _, state, plan_so_far = heapq.heappop(pq)
        fp = state.fingerprint
        if fp in closed:
            if trace is not None:
                trace.append({"ev": "pop", "state_fp": fp, "g": len(plan_so_far),
                              "h": _unmet(state, goal), "f": f, "closed_skip": True,
                              "satisfies": satisfies(state, goal)})
            continue
        closed.add(fp)

        sat = satisfies(state, goal)
        if trace is not None:
            trace.append({"ev": "pop", "state_fp": fp, "g": len(plan_so_far),
                          "h": _unmet(state, goal), "f": f, "closed_skip": False,
                          "satisfies": sat})
        if sat:
            if trace is not None:
                trace.append({"ev": "result", "plan": [repr(o) for o in plan_so_far],
                              "unreachable_reason": None, "expanded": expanded})
            if stats is not None:
                stats.update(nodes_expanded=expanded, max_frontier=_max_frontier,
                             search_ms=(time.perf_counter() - _t0) * 1000.0, reachable=True)
            return list(plan_so_far)

        expanded += 1
        for op in ops_universe:                          # canonical order
            ph = pre_holds(op, state)
            new_state = apply(op, state) if ph else None
            if trace is not None:
                evt = {"ev": "expand", "state_fp": fp, "op": repr(op), "pre": ph}
                if ph:
                    evt["new_fp"] = new_state.fingerprint
                    evt["closed"] = new_state.fingerprint in closed
                trace.append(evt)
            if not ph:
                continue
            new_fp = new_state.fingerprint
            if new_fp in closed:
                continue
            new_plan = plan_so_far + (op,)
            heapq.heappush(pq, (len(new_plan) + _unmet(new_state, goal),
                                counter, new_state, new_plan))
            counter += 1
        if stats is not None and len(pq) > _max_frontier:   # peak after this expansion
            _max_frontier = len(pq)

    reason = ("expansion cap reached" if expanded >= max_expansions
              else "exhausted operator universe under preconditions")
    if trace is not None:
        trace.append({"ev": "result", "plan": None,
                      "unreachable_reason": reason, "expanded": expanded})
    if stats is not None:
        stats.update(nodes_expanded=expanded, max_frontier=_max_frontier,
                     search_ms=(time.perf_counter() - _t0) * 1000.0, reachable=False)
    return Unreachable(goal_fingerprint=goal.fingerprint, expanded=expanded, reason=reason)


def apply_plan(start: State, plan_seq: Iterable[Op]) -> State:
    """Compose `eff` across a plan. Used by tests to confirm that the returned
    sequence, when applied, yields a state satisfying the goal."""
    s = start
    for op in plan_seq:
        s = apply(op, s)
    return s
