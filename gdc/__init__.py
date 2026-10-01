"""GDC — the deterministic goal-directed compilation engine.

Decouples navigation from the LLM. The engine takes a declarative goal
descriptor (the shape of the result relation, with no ordering field) and a
start state, and derives the operator sequence by forward search over primitive
preconditions / effects. The search core is LLM-free; the single Call-1 goal
emission lives in `llm`, isolated from it.

Module map:
  - state, operators, search   — the LLM-free planning core
  - contract, order            — goal/order validation and lints
  - integrate, primitives      — the executor (`run_primitive` + plan execution)
  - schema_bridge              — alias map and briefing construction
  - llm                        — the single Call-1 goal-emission prompt
  - config                     — model tiering and logging setup
  - probe                      — the standalone demo/probe harness

`config` is NOT imported eagerly: it initialises an API client, so callers that
need it import it inside the function that uses it. Everything below is
LLM-free and safe to import at module load.

Public surface:
  - State, Goal       — descriptors (NEVER carry data)
  - fingerprint       — deterministic SHA256 over canonical form
  - satisfies         — structural goal-condition test
  - Op, OPERATORS     — operator instances + the four primitives' pre/eff
  - enumerate_ops     — canonical operator universe for a goal
  - plan              — A* forward search → [Op] | Unreachable
  - apply_plan        — compose eff across a plan (used by tests)
  - Unreachable       — typed return on exhaustion (future Call-2 seam)
"""
from .state import State, Goal, fingerprint, satisfies, normalise_op       # noqa: F401
from .operators import (                                                    # noqa: F401
    Op, Operator, OPERATORS, apply, pre_holds, enumerate_ops, expr_refs,
)
from .search import plan, apply_plan, Unreachable                           # noqa: F401
from .contract import (                                                     # noqa: F401
    ParseError, ContractError, ContractVerdict,
    parse_goal, validate_goal, unexpressible_keys, aggregate_derives,
)
from .order import OrderSpec, apply_order, validate_order                   # noqa: F401
from .schema_bridge import build_bridge, build_briefing, AliasMap, SchemaRuleError  # noqa: F401
