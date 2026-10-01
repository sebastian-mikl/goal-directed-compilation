"""S28 — REFUSE_UNSUPPORTED runtime toggle (default OFF).
S40 — REFUSE_OUT_OF_SCOPE_DATA runtime toggle (default OFF), added alongside it.

Two INDEPENDENT capability-boundary instructions, each added to the PRIMITIVE
systems' emission prompts (network, planner, react) only when its OWN toggle is ON:

  REFUSE_UNSUPPORTED       — the request needs an OPERATION outside {filter, select,
                              aggregate, compute} (S28; measures the out-of-range
                              approximate-vs-decline delta).
  REFUSE_OUT_OF_SCOPE_DATA — the request needs a concept/entity/attribute that is not
                              among the columns actually passed to the model (S40;
                              measures the out_of_scope_register approximate-vs-decline
                              delta — see the Spider wta_01/world_07/bike_11 diagnosis).

Kept as TWO toggles rather than folded into one, even though both are
"capability-boundary decline" instructions: REFUSE_UNSUPPORTED's docstring already
frames it as an isolated, single-variable measurement ("a separate ON/OFF run").
Folding a second, unrelated failure mode into the same env var would silently change
what REFUSE_UNSUPPORTED=1 measures for any future re-run of the ORIGINAL
operation-coverage ablation, and would make a table-coverage regression indistinguishable
from an operation-coverage one in the results. Each toggle defaults OFF and composes
independently in `refuse_suffix()` — turning one on never changes the other's text.

DISCIPLINE (both instructions): describe the SUPPORTED thing positively and say
"decline if the question needs anything outside it" — never enumerate the specific
unsupported operations/tables/columns that happen to appear in the eval corpus
(that would be authoring from the eval; describing the capability boundary is
authoring from the engine).

NOT given to code_agent / text_to_sql: neither "unsupported operation" nor "data
outside the register" applies to general-purpose pandas/SQL — both operate directly
on the full register with their own tool, not through a fixed column list — and
both are excluded from their respective sub-studies.
"""
from __future__ import annotations

import os

# The identical capability-boundary instruction added to network, planner, AND react.
REFUSE_INSTRUCTION = (
    "The only operations available are: filter (select rows by a predicate), select "
    "(project columns), aggregate (sum / count / average / min / max, over all rows or "
    "per group), and compute (arithmetic combinations of existing columns). If answering "
    "the question would require any operation that cannot be expressed with these, decline "
    "explicitly rather than approximating it with a different operation. It is better to "
    "decline than to return a confident answer to a question the available operations "
    "cannot actually answer."
)

# S40 — the table/data-coverage counterpart to REFUSE_INSTRUCTION above. Mirrors its
# structure exactly (positive capability statement / decline condition / closing
# "better to decline" sentence) but for missing DATA rather than missing OPERATIONS.
# Points at the "Base columns" block already earlier in the same system prompt rather
# than restating it, and covers both observed failure shapes generically (substituting
# a related-but-wrong column, or silently dropping an unexpressable condition) without
# naming any specific table/column from the eval corpus.
REFUSE_OUT_OF_SCOPE_INSTRUCTION = (
    "The only data available is whatever is listed under \"Base columns\" above — there "
    "is no other table, entity, or attribute behind the scenes. If answering the question "
    "would require information that is not represented by any of those columns, decline "
    "explicitly rather than substituting a different, only superficially related column "
    "as an approximation, or silently dropping part of the question to answer what "
    "remains. It is better to decline than to return a confident answer built from data "
    "that does not actually address what was asked."
)

# The network has no decline channel (planner/react already do), so when EITHER
# instruction is active it is also told HOW to decline — emit a refuse envelope, which
# llm1 maps to the EXISTING refused/unreachable outcome (no new outcome class). The doc
# itself is reason-agnostic, so it's shared by both toggles rather than duplicated.
NETWORK_DECLINE_DOC = (
    'To decline, return {"refuse": "<short reason>"} instead of the '
    '{"goal": ..., "order": ...} object.'
)

REFUSE_PROMPT_TAG = "refuse_unsupported"                    # provenance tag when S28 ON
REFUSE_OOS_PROMPT_TAG = "refuse_out_of_scope_data"           # provenance tag when S40 ON


def refuse_unsupported_enabled() -> bool:
    """Runtime toggle, read at CALL time (a run sets REFUSE_UNSUPPORTED in env). Default OFF."""
    return os.getenv("REFUSE_UNSUPPORTED", "").strip().lower() in ("1", "true", "yes", "on")


def refuse_out_of_scope_data_enabled() -> bool:
    """Runtime toggle, read at CALL time (a run sets REFUSE_OUT_OF_SCOPE_DATA in env).
    Default OFF — independent of REFUSE_UNSUPPORTED."""
    return os.getenv("REFUSE_OUT_OF_SCOPE_DATA", "").strip().lower() in ("1", "true", "yes", "on")


def refuse_suffix(system: str) -> str:
    """Text appended to a primitive system's emission prompt: the ON instructions,
    whichever of the two toggles are active, joined in a fixed order; empty when both
    are OFF — so the OFF prompt stays byte-identical to the main-run prompt. Byte-
    identical to the pre-S40 output when only REFUSE_UNSUPPORTED is ON (verified by
    the s28 suite, unmodified)."""
    blocks = []
    if refuse_unsupported_enabled():
        blocks.append(REFUSE_INSTRUCTION)
    if refuse_out_of_scope_data_enabled():
        blocks.append(REFUSE_OUT_OF_SCOPE_INSTRUCTION)
    if not blocks:
        return ""
    text = "\n\n" + "\n\n".join(blocks)
    if system == "network":      # the only system that needed a new decline channel
        text += "\n" + NETWORK_DECLINE_DOC
    return text


# ─── S48 — DECLINE_AFFORDANCE: a uniform, general, non-leading "you may decline"
# affordance for the BASELINES, for the boundary-tier fairness experiment. ────────
# Distinct from REFUSE_INSTRUCTION / REFUSE_OUT_OF_SCOPE_INSTRUCTION above: those name
# a SPECIFIC capability boundary (the four primitives / the listed columns) and are
# fair only for the constrained systems (network, planner, react) that actually have
# that boundary. This one names NO boundary at all — it is a general "if you cannot
# answer correctly for ANY reason, you may decline" affordance, so it is fair to give
# to a general-purpose system too, and it does not tell the model which questions are
# out of scope (that would manufacture the result). It is applied UNIFORMLY to every
# question (boundary AND in-contract — the in-contract ones are the false-decline
# check) and includes an explicit anti-over-decline guard.
#
# code_agent and text_to_sql ALREADY carry an equivalent always-on inline affordance
# in their own system prompts ("If the query genuinely cannot be answered from this
# data/table, reply … explaining you cannot"); this toggle adds the matching
# affordance to react and planner, which have none in the main run. Default OFF —
# react/planner's OFF prompt stays byte-identical to the main-run prompt.
#
# Signal: the sentinel token CANNOT_ANSWER, caught by is_decline_text() (a
# `cannot_answer` alternative was added to its pattern — the underscore is a word
# char, so the pre-existing `can(?:not|'t)` branch did NOT match "CANNOT_ANSWER")
# and, for planner, by the existing {"refuse": …} envelope. The parser change is
# additive and provably non-regressing (is_decline_text runs only inside the
# baseline adapters, never in the offline comparator; no recorded emission contains
# the token) — so no existing number moves and no new outcome class is introduced.
DECLINE_AFFORDANCE_TAG = "decline_affordance"        # provenance tag when S48 ON

_DECLINE_AFFORDANCE_BODY = (
    "You may decline this question. If you cannot produce a correct answer from the "
    "register table available to you — whether because the data the question needs is "
    "not present in the columns above, or because the question is otherwise beyond what "
    "{capability} can compute here — do not guess. {how} Decline only when you genuinely "
    "cannot answer correctly; do not decline a question {capability} can answer."
)
# per-baseline: (capability noun, how-to-decline clause phrased for the output format)
_DECLINE_AFFORDANCE_FORMAT = {
    "react": ("these tools",
              "Instead of calling final_answer, reply with plain text beginning with the "
              "token CANNOT_ANSWER followed by a short reason."),
    "planner": ("these operations",
                'Instead of emitting a plan, return {"refuse": "CANNOT_ANSWER: <short reason>"}.'),
    "code_agent": ("general Python/pandas",
                   "Instead of calling final_answer, reply with plain text beginning with the "
                   "token CANNOT_ANSWER followed by a short reason."),
    "text_to_sql": ("a single SQL SELECT",
                    "Instead of emitting SQL, reply with plain text beginning with the token "
                    "CANNOT_ANSWER followed by a short reason."),
}


def decline_affordance_enabled() -> bool:
    """Runtime toggle, read at CALL time. Default OFF, independent of the two
    capability-boundary toggles above."""
    return os.getenv("DECLINE_AFFORDANCE", "").strip().lower() in ("1", "true", "yes", "on")


def decline_affordance(system: str) -> str:
    """The uniform general decline affordance for a baseline, phrased for its output
    format; empty when DECLINE_AFFORDANCE is OFF (so the OFF prompt is byte-identical
    to the main run). Only defined for the four baselines."""
    if not decline_affordance_enabled() or system not in _DECLINE_AFFORDANCE_FORMAT:
        return ""
    capability, how = _DECLINE_AFFORDANCE_FORMAT[system]
    return "\n\n" + _DECLINE_AFFORDANCE_BODY.format(capability=capability, how=how)
