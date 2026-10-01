"""LLM1 goal emission — natural-language request -> declarative Goal JSON.

This is the ONLY LLM in the planner stack, and it is confined here: it emits the
GOAL (the shape of the answer), never the plan. The deterministic core
(state/operators/search) derives the operator sequence with no LLM. emit_goal
returns a parsed Goal (via contract.parse_goal) or raises ParseError; the contract
(contract.validate_goal) is applied separately by the caller, so a syntactically
valid but out-of-contract goal is still rejected before planning.

Live (needs ANTHROPIC_API_KEY): emit_goal calls Call-1's model. The JSON-extraction
and parsing path is unit-tested offline with a mock client; the live `--nl` mode is
exercised via scripts/the probe and handed off when a key is present.
"""
from __future__ import annotations

import re

from .contract import ParseError, parse_goal
from .state import Goal


class EmissionDecline(ParseError):
    """The LLM explicitly DECLINED via a {"refuse": "<reason>"} envelope (the network's
    decline channel). A ParseError subclass so callers that already map ParseError ->
    refused stay correct; the network adapter recognises it for a clean decline reason
    (distinct from a malformed-output ParseError). No new outcome class — it maps to the
    EXISTING refused outcome."""


def _schema_block(schema: dict[str, str], value_hints: dict | None = None) -> str:
    lines = []
    for name, typ in schema.items():
        line = f"  - {name}: {typ}"
        if value_hints and name in value_hints:
            vals = ", ".join(str(v) for v in value_hints[name])
            line += f"  (values: {vals})"
        lines.append(line)
    return "\n".join(lines)


# Bumped when the LLM1 goal-emission prompt changes, recorded in provenance so a
# results.jsonl ties to the exact emission instruction. s31: scalar aggregates
# (incl. max/min) emit exactly ONE derived field at the query grain — never the raw
# underlying column — and are kept distinct from row-selection (top-N) queries.
GOAL_PROMPT_VERSION = "s48"   # s48: t5_15 — STAGED FILTERS (a predicate on a derived
                              # column is a where conjunct on that derive name, even
                              # when the column is not in the output) + bare "top N"
                              # -> desc. This is s46a's text RETRIED on top of the
                              # s47 schema term-pin: s46a alone cost 22 T5 cells, 20
                              # of them via 'Technical' rebinding group->source, which
                              # the pin now fixes in schema data (measured 70/70).
                              # s45: Fix 2 — count_distinct(col) declared in the
                              # derive doc + ALLOWED_AGG_FNS (engine executes it
                              # via nunique; count(DISTINCT x) canonicalises).
                              # s44: Finding 7 — implicit-ranking instruction (bare
                              # superlative -> order_by + limit 1, with over-trigger
                              # guards), SEPARATE from s43's order-closure rule.
                              # s43: Fix 1 — order-closure made explicit (ranking
                              # columns must appear in columns/derive); s31 before.


GOAL_INSTRUCTION = """You translate an analytical request over a tabular dataset (see the
schema below) into a declarative GOAL — the SHAPE of the result relation, NOT the steps to compute it.

Base columns:
{schema}

Return ONLY a JSON object with EXACTLY these four fields:
  "columns": ["<name>", ...]                  the result's columns (derived names allowed)
  "grain":   "row"  OR  {{"grouped_by": ["<col>", ...]}}
  "where":   [{{"col": "<c>", "op": "==|!=|<|<=|>|>=|in|contains", "val": <value>}}, ...]
  "derive":  [{{"name": "<new>", "expr": "<expr>"}}, ...]
             expr is "fn(col)" for an aggregate (sum/avg/count/min/max),
             or "colA <op> colB" for a computed column (+ - * /).

CONTRACT (violations make the goal invalid — do not emit them):
  - There is NO ordering / sorting / ranking / top-k field. If the request says
    "top N", "highest", "ranked", emit the relation WITHOUT that ordering — it is
    not expressible. Never invent an unsupported field.
  - At MOST ONE aggregate derive entry.
  - Use only the base columns plus names you introduce in "derive".

Return JSON only — no prose, no code fences."""


def _extract_json(text: str) -> str:
    """Pull the first balanced JSON object out of the model's reply."""
    s = text.strip()
    start = s.find("{")
    if start == -1:
        raise ParseError("LLM1 output contained no JSON object")
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    raise ParseError("LLM1 output had an unbalanced JSON object")


def emit_goal(request: str, *, client, schema: dict[str, str],
              model: str | None = None) -> Goal:
    """Call LLM1 to emit a Goal for `request`. Returns a parsed Goal or raises
    ParseError. The contract is NOT applied here — the caller validates."""
    from gdc.config import MODEL_CALL1
    system = GOAL_INSTRUCTION.format(schema=_schema_block(schema))
    resp = client.messages.create(
        model=model or MODEL_CALL1,
        max_tokens=700,
        system=system,
        messages=[{"role": "user", "content": request}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", None) == "text")
    return parse_goal(_extract_json(text))


# ── S13 Part B: minimal live LLM1 → (Goal, OrderSpec), and solve() ───────────

ALLOWED_AGG_FNS = ("sum", "avg", "count", "min", "max", "count_distinct")

GOAL_ORDER_INSTRUCTION = """You translate an analytical request over a tabular dataset (see the
schema below) into a declarative GOAL plus a separate ORDER spec.

The GOAL is the SHAPE of the result relation (a set of rows — UNORDERED). The ORDER spec is
PRESENTATION ONLY (ranking / top-N) and lives OUTSIDE the goal.

Base columns:
{schema}
{briefing}
Return ONLY a JSON object: {{"goal": {{...}}, "order": {{...}}}}.

"goal":
  "columns": ["<name>", ...]                  result columns (derived names allowed)
  "grain":   "row"  OR  {{"grouped_by": ["<col>", ...]}}
  "where":   [{{"col": "<c>", "op": "==|!=|<|<=|>|>=|in|contains", "val": <value>}}, ...]
  "derive":  [{{"name": "<new>", "expr": "<expr>"}}, ...]
             expr is "fn(col)" for an aggregate (fn ∈ {fns}),
             or "colA <op> colB" for a computed column (op ∈ + - * /).
             "how many DISTINCT / unique / different <col>" is count_distinct(col) —
             plain count(col) counts ROWS, not distinct values.

"order" (presentation; NEVER put ordering inside the goal):
  "order_by": [{{"col": "<result col>", "dir": "asc|desc"}}, ...]   (may be empty)
  "limit":    <int>  or  null

CONTRACT (violations make it invalid — do not emit them):
  - NO ordering/sorting/ranking/top-k inside the goal. Ranking goes ONLY in "order".
  - At MOST ONE aggregate derive entry.
  - A GLOBAL aggregate over all rows uses grain {{"grouped_by": []}}, not "row".
    (grain "row" cannot carry an aggregate; any aggregate requires grouped grain.)
  - A SCALAR AGGREGATE query — "the highest / lowest / largest / smallest / max / min /
    total / average / count of <column>" — has as its output EXACTLY ONE derived aggregate
    field, at the query's grain (global grain {{"grouped_by": []}} when ungrouped). Put only
    that derived field in "columns"; do NOT also list the underlying raw <column>, nor any
    other per-row column (title, id, owner, …) — a row-grain column cannot coexist with a
    global or grouped aggregate in one output, so the goal becomes unreachable. max and min
    are scalar aggregates exactly like sum / average / count (they are NOT a request to
    return the row).
  - DISTINCT — ROW SELECTION: "the N entries with the highest / lowest <column>" is NOT an
    aggregate. List the requested columns at row grain and put the ranking in "order"
    (order_by <column> desc/asc, limit N), never in the goal.
  - IMPLICIT RANKING — asking for an attribute OF the single best/worst entity
    ("the <attribute> of the oldest/largest/cheapest <entity>", "which <entity> has
    the highest <metric>") IS a top-1 ranking: emit "order" with order_by on the
    deciding column (desc for highest/most, asc for lowest/least/fewest) and
    limit 1, exactly as if the request said "top 1" — never a plain unordered
    listing. This rule RECOGNIZES that a ranking is required at all; the next rule
    governs the goal's output once any order_by exists. GUARDS: a request for the
    VALUE of a maximum/minimum itself ("the highest <column>") stays a SCALAR
    AGGREGATE (rule above) — no order, no limit; and a superlative that does not
    single out one entity ("most <rows> are <X>", share comparisons) is NOT a
    ranking — do not add order or limit there.
  - "top N" with no stated direction means the N LARGEST — emit dir "desc" unless
    the request says lowest/smallest/least/fewest/ascending.
  - EVERY "order_by" column must be part of the goal's OUTPUT: either listed in
    "columns" or introduced as a "derive" name. This INCLUDES a ranking column the
    request does not ask to be displayed — "the <entity> with the highest <metric>"
    outputs the entity column but ranks by <metric>, so <metric> must ALSO appear in
    "columns" (or in "derive" when it is computed). An order_by column missing from
    the goal's output violates the contract: the request is refused, never repaired.
  - Use only the base columns plus names introduced in "derive".
  - STAGED FILTERS — a request that derives a quantity and then RESTRICTS rows by it
    ("keep only positive-gap entries", "for entries with a negative buffer", "where
    the gap exceeds X") contributes a "where" conjunct on that DERIVED name, exactly
    like a filter on a base column. Emit one conjunct per such restriction. This is
    REQUIRED even when the derived column is not itself reported: a row-membership
    filter that never reaches the output still selects which rows the aggregate sees,
    so dropping it silently changes the answer. A multi-stage request ("derive A,
    keep A>0, then derive B, and for B<0 report avg(B) per G") yields BOTH conjuncts
    — A>0 AND B<0 — not just the one feeding the reported value. Every derived
    quantity must be consumed by something: a where conjunct, another derive, the
    group key, order_by, or "columns". A derive consumed by nothing means a
    restriction was dropped — the request is refused rather than answered wrongly.

FILTER VALUES: when a column lists `(values: ...)`, a filter on it must use one of
those EXACT values (op "==" or "in"), or op "contains" for a [match: contains] column.
Do NOT invent a value that is not in the list.

{rules}

Return JSON only — no prose, no code fences."""


def _rich_schema_block(schema_view: dict) -> str:
    """Render the alias-space schema view: type, match_strategy, categorical values."""
    lines = []
    for name, info in schema_view.items():
        line = f"  - {name}: {info.get('type', '?')}"
        if info.get("match_strategy"):
            line += f" [match: {info['match_strategy']}]"
        if info.get("categories"):
            line += f"  (values: {', '.join(str(v) for v in info['categories'])})"
        lines.append(line)
    return "\n".join(lines)


def _briefing_block(domain: dict | None) -> str:
    """Render the ADVISORY domain briefing generically — background for interpreting the
    request, explicitly NOT rules. Never a basis for engine correctness (the engine depends
    only on the structured business_rules + the typed goal). Built from declared fields; no
    domain literals in code."""
    if not domain:
        return ""
    parts = ["CONTEXT (advisory — background for interpreting the request; NOT enforced rules):"]
    if domain.get("description"):
        parts.append(f"  {domain['description']}")
    cds = domain.get("column_descriptions") or {}
    if cds:
        parts.append("  Column meanings:")
        for col, text in cds.items():
            parts.append(f"    - {col}: {text}")
    return "\n".join(parts) + "\n"


def _rules_block(business_rules: dict | None, schema_view: dict | None = None) -> str:
    """Render the schema-declared business rules generically — no domain literals.
    Each rule {name: {column, allowed, aliases?}} becomes: when the request refers to
    "<name>" (or any of its aliases) rows, filter <column> to one of the allowed values
    via the `in` op. Aliases are multi-word trigger phrases that anchor the rule so the
    model doesn't decompose the phrase and independently reinterpret one of its words
    as an unrelated filter on a different column (S17 diagnosis); a rule with no
    aliases renders exactly as before."""
    parts = []
    for name, rule in (business_rules or {}).items():
        allowed = rule.get("allowed", [])
        if not allowed:
            continue
        vals = ", ".join(f'"{v}"' for v in allowed)
        triggers = " or ".join(f'"{t}"' for t in [name, *(rule.get("aliases") or [])])
        parts.append(f'  - When the request refers to {triggers} rows, filter {rule["column"]} to one '
                     f'of those values: emit {{"col": "{rule["column"]}", "op": "in", "val": [{vals}]}} '
                     f'(use the supported `in` op — do NOT invent a new operator).')
    example = ""
    if schema_view:                          # draw the contains example from THIS schema, if any
        cc = [a for a, info in schema_view.items() if info.get("match_strategy") == "contains"]
        if cc:
            example = f" (e.g. {cc[0]})"
    parts.append('  - Respect each column\'s [match: ...]: use op "contains" for contains-matched '
                 f'columns{example}, and an exact listed value ("==" or "in") for categoricals.')
    return "RULES (authoritative — these definitions are enforced):\n" + "\n".join(parts)


def _parse_order(obj) -> "OrderSpec":
    from .order import OrderSpec
    if obj is None:
        return OrderSpec.make()
    if not isinstance(obj, dict):
        raise ParseError(f"'order' must be an object, got {type(obj).__name__}")
    ob = obj.get("order_by", [])
    if not isinstance(ob, list):
        raise ParseError("'order.order_by' must be a list")
    for e in ob:
        if not (isinstance(e, dict) and "col" in e):
            raise ParseError(f"each order_by entry needs a 'col': {e!r}")
    lim = obj.get("limit", None)
    if lim is not None and not isinstance(lim, int):
        raise ParseError(f"'order.limit' must be an int or null, got {lim!r}")
    try:
        return OrderSpec.make(order_by=ob, limit=lim)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"could not build OrderSpec: {exc}") from exc


def llm1(nl: str, *, client, base_schema: dict[str, str],
         value_hints: dict | None = None, schema_view: dict | None = None,
         business_rules: dict | None = None, domain: dict | None = None,
         model: str | None = None, extra_instruction: str = ""):
    """One Call-1: NL request → (Goal, OrderSpec). Expressibility is the CALLER's
    job (validate_goal/validate_order); this only parses. Raises ParseError on
    malformed output.

    `schema_view`: alias-space rich schema {alias: {type, match_strategy, categories}}
    + `business_rules` ({name: {column, allowed}}) — AUTHORITATIVE, fed so filters use
    real values and the enforced rules. `domain` ({description, column_descriptions}) is
    ADVISORY context only — projected into the prompt, never a basis for correctness.
    Falls back to `value_hints` when no schema_view."""
    from gdc.config import MODEL_CALL1
    if schema_view is not None:
        schema_block = _rich_schema_block(schema_view)
        rules = _rules_block(business_rules, schema_view)
        briefing = _briefing_block(domain)
    else:
        schema_block = _schema_block(base_schema, value_hints)
        rules = ""
        briefing = ""
    system = GOAL_ORDER_INSTRUCTION.format(
        schema=schema_block, briefing=briefing, fns="/".join(ALLOWED_AGG_FNS), rules=rules)
    system += extra_instruction      # "" on the default path -> byte-identical prompt
    resp = client.messages.create(
        model=model or MODEL_CALL1, max_tokens=900, system=system,
        temperature=0,
        messages=[{"role": "user", "content": nl}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", None) == "text")
    import json
    try:
        obj = json.loads(_extract_json(text))
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"LLM1 output was not valid JSON: {exc}") from exc
    if isinstance(obj, dict) and "refuse" in obj and "goal" not in obj:
        raise EmissionDecline(str(obj.get("refuse") or "declined: unsupported operation"))
    if not isinstance(obj, dict) or "goal" not in obj:
        raise ParseError("LLM1 output missing 'goal'")
    goal = parse_goal(obj["goal"])
    order = _parse_order(obj.get("order"))
    return goal, order


def solve(nl: str, *, client, base_schema: dict[str, str],
          value_hints: dict | None = None, schema_view: dict | None = None,
          business_rules: dict | None = None, domain: dict | None = None,
          model: str | None = None):
    """NL → (Plan, OrderSpec) | ContractError | ParseError | Unreachable.

    Expressibility-gated only: llm1 → validate_goal + validate_order → plan(Goal).
    Typed errors are RETURNED (never raised); NO retry, NO completeness hardening.
    `domain` is advisory prompt context only — it cannot change the enforced result."""
    from .contract import validate_goal, ContractError
    from .order import validate_order
    from .search import plan, Unreachable
    from .state import State
    try:
        goal, order = llm1(nl, client=client, base_schema=base_schema,
                           value_hints=value_hints, schema_view=schema_view, domain=domain,
                           business_rules=business_rules, model=model)
    except ParseError as exc:
        return exc
    gv = validate_goal(goal)
    if not gv.ok:
        return ContractError(gv.reason)
    ov = validate_order(order, goal)
    if not ov.ok:
        return ContractError(ov.reason)
    p = plan(State.from_base(base_schema), goal)
    if isinstance(p, Unreachable):
        return p
    return p, order
