"""Verbose probe harness — manual explorer for the goal-emission → contract →
plan pipeline.  `python -m gdc.probe` with one of two mutually-exclusive modes:

  --nl "<request>"    LLM1 emits a Goal → print it → validate_goal → print verdict
                      → if Ok: plan(trace) → print the full trace → print plan +
                      satisfies.  (LLM1 is live; needs ANTHROPIC_API_KEY.)
  --goal <path.json>  skip LLM1; load the Goal from JSON → same pipeline.

Every stage prints under a clear header. All failures render legibly and NEVER
raise out of the harness: ParseError, ContractError(reason), Unreachable(reason).
The full Goal is ALWAYS printed once parsed, so a request the schema cannot
express (e.g. top-k / ranking — there is no Goal field for it) is visibly ABSENT
rather than silently assumed handled.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .state import State, satisfies
from .contract import (
    ParseError, ContractError, parse_goal, validate_goal, unexpressible_keys,
)
from .search import plan, apply_plan, Unreachable

# A generic demo/test schema for the standalone probe — NOT the engine's default
# (the engine carries no domain columns; production callers pass a real schema.json).
DEMO_SCHEMA: dict[str, str] = {
    "record_id":  "str",
    "owner":    "str",
    "severity": "int",
    "cost":     "float",
    "status":   "str",
    "colour":   "str",
}


@dataclass
class ProbeResult:
    goal: object | None = None            # Goal | None
    parse_error: str | None = None
    unexpressible: list = field(default_factory=list)
    contract_ok: bool | None = None
    contract_reason: str = ""
    trace: list = field(default_factory=list)
    plan: object = None                   # list[Op] | None
    unreachable: object = None            # Unreachable | None
    satisfies: bool | None = None
    reached_planner: bool = False


def goal_to_dict(goal) -> dict:
    """Render a Goal back to its declarative JSON form — note there is no
    ordering field, by design."""
    return {
        "columns": [list(c) for c in goal.columns],
        "grain": ("row" if goal.grouped_by is None
                  else {"grouped_by": list(goal.grouped_by)}),
        "where": [{"col": c, "op": o, "val": (list(v) if isinstance(v, tuple) else v)}
                  for c, o, v in goal.where],
        "derive": [{"name": n, "expr": e} for n, e in goal.derive],
    }


def _run_from_goal(goal, schema, raw_obj=None) -> ProbeResult:
    res = ProbeResult(goal=goal)
    if raw_obj is not None:
        res.unexpressible = unexpressible_keys(raw_obj)
    verdict = validate_goal(goal)
    res.contract_ok = verdict.ok
    res.contract_reason = verdict.reason
    if not verdict.ok:
        return res                                   # never reaches the planner
    res.reached_planner = True
    start = State.from_base(schema)
    sink: list = []
    p = plan(start, goal, trace=sink)
    res.trace = sink
    if isinstance(p, Unreachable):
        res.unreachable = p
    else:
        res.plan = p
        res.satisfies = satisfies(apply_plan(start, p), goal)
    return res


def run_goal(goal_obj, *, schema=DEMO_SCHEMA) -> ProbeResult:
    """--goal core: payload (dict/JSON/Goal) → parse → validate → plan(trace)."""
    try:
        goal = parse_goal(goal_obj)
    except ParseError as exc:
        return ProbeResult(parse_error=str(exc))
    raw = goal_obj if isinstance(goal_obj, dict) else None
    return _run_from_goal(goal, schema, raw_obj=raw)


def run_nl(request, *, client, schema=DEMO_SCHEMA) -> ProbeResult:
    """--nl core: LLM1 emits a Goal → parse → validate → plan(trace)."""
    from .llm import emit_goal
    try:
        goal = emit_goal(request, client=client, schema=schema)
    except ParseError as exc:
        return ProbeResult(parse_error=str(exc))
    return _run_from_goal(goal, schema)


# ── rendering (never raises) ─────────────────────────────────────────────────

def _h(title: str) -> None:
    print(f"\n{'=' * 3} {title} {'=' * (72 - len(title))}")


def render(res: ProbeResult, *, request: str | None = None) -> None:
    if request is not None:
        _h("STAGE 1 — request (NL)")
        print(f"  {request}")

    _h("STAGE 2 — emitted Goal")
    if res.parse_error is not None:
        print(f"  ParseError: {res.parse_error}")
        print("  (no Goal produced — pipeline stops)")
        return
    print(json.dumps(goal_to_dict(res.goal), indent=2))
    if res.unexpressible:
        print(f"  NOTE: request keys the Goal cannot express, dropped: {res.unexpressible}")
    print("  (a Goal has NO ordering/top-k field — any such request is ABSENT above)")

    _h("STAGE 3 — contract")
    if res.contract_ok:
        print("  validate_goal: OK")
    else:
        print(f"  ContractError: {res.contract_reason}")
        print("  (planner NOT reached)")
        return

    _h("STAGE 4 — search trace")
    for ev in res.trace:
        print("  " + _fmt_event(ev))

    _h("STAGE 5 — plan + satisfies")
    if res.unreachable is not None:
        print(f"  Unreachable: {res.unreachable.reason} (expanded={res.unreachable.expanded})")
        return
    for i, op in enumerate(res.plan):
        print(f"  {i}. {op!r}")
    print(f"  satisfies(goal) = {res.satisfies}")


def _fmt_event(ev: dict) -> str:
    e = ev["ev"]
    if e == "start":
        return f"start  goal_fp={ev['goal_fp'][:12]}  |universe|={len(ev['universe'])}"
    if e == "pop":
        return (f"pop    fp={ev['state_fp'][:12]} g={ev['g']} h={ev['h']} f={ev['f']} "
                f"closed_skip={ev['closed_skip']} satisfies={ev['satisfies']}")
    if e == "expand":
        tail = f" -> new_fp={ev['new_fp'][:12]} closed={ev['closed']}" if ev["pre"] else ""
        return f"expand fp={ev['state_fp'][:12]} pre={ev['pre']!s:<5} {ev['op']}{tail}"
    if e == "result":
        if ev["plan"] is None:
            return f"result UNREACHABLE ({ev['unreachable_reason']}) expanded={ev['expanded']}"
        return f"result plan={ev['plan']} expanded={ev['expanded']}"
    return str(ev)


def _make_client():
    try:
        from gdc.config import CLIENT
        if CLIENT is not None:
            return CLIENT
    except Exception:  # noqa: BLE001
        pass
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        from anthropic import Anthropic
        return Anthropic()
    except Exception:  # noqa: BLE001
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gdc.probe",
                                 description="Probe: goal emission → contract → plan.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--nl", metavar="REQUEST", help="natural-language request (LLM1, live)")
    mode.add_argument("--goal", metavar="PATH", help="path to a Goal JSON file (skip LLM1)")
    args = ap.parse_args(argv)

    if args.goal:
        try:
            obj = json.loads(Path(args.goal).read_text())
        except Exception as exc:  # noqa: BLE001
            print(f"[load] could not read goal JSON: {exc}")
            return 2
        render(run_goal(obj))
        return 0

    client = _make_client()
    if client is None:
        print("[--nl] ANTHROPIC_API_KEY required for LLM1 emission "
              "(build/handoff: use --goal for the offline pipeline).")
        return 2
    render(run_nl(args.nl, client=client), request=args.nl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
