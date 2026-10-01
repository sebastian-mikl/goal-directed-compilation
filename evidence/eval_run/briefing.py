"""Shared domain briefing for ALL 5 adapters (fairness — same knowledge of the
schema/conventions; only the architecture differs).

Reads schema.json's `domain` block + `business_rules`, and projects them in
REAL-column space (no aliases) so the briefing reads naturally for the four
non-Network adapters whose tools/columns are the actual register columns. The
Network adapter uses the planner's own alias-space schema_view internally — the
briefing here is informational and unconditionally appended to its system prompt
too, so all five see the same prose.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def load_briefing(schema_path: Path, register_path: Path) -> dict:
    """Return {description, column_descriptions{real_col: text}, business_rules}.
    Categorical values are recorded from the register so adapters know e.g. that
    "active" -> Status in ["Open High", "Open Medium", ...]."""
    schema = json.loads(Path(schema_path).read_text())
    df = pd.read_pickle(register_path)
    domain = schema.get("domain", {}) or {}
    columns_spec = schema.get("columns", {}) or {}
    rules_spec = schema.get("business_rules", {}) or {}

    col_descs: dict[str, str] = {}
    for col, info in columns_spec.items():
        desc = info.get("description") or domain.get("column_descriptions", {}).get(col, "")
        if desc:
            col_descs[col] = desc

    # Compute each business_rule's allowed value SET from the column's categories
    # (categorical - exclude) | include - mirrors gdc.schema_bridge but in
    # real-column space, briefing-only.
    rules: dict[str, dict] = {}
    for name, spec in rules_spec.items():
        col = spec.get("column")
        if not col or col not in df.columns:
            continue
        cats = sorted(map(str, df[col].dropna().unique()))
        excl = set(map(str, spec.get("exclude", []) or []))
        incl = set(map(str, spec.get("include", []) or []))
        allowed = (incl if incl else (set(cats) - excl))
        rules[name] = {"column": col, "allowed": sorted(allowed)}

    return {
        "description": domain.get("description", ""),
        "column_descriptions": col_descs,
        "business_rules": rules,
        "columns": list(df.columns),
    }


def render_briefing(briefing: dict) -> str:
    """Render the briefing as one block of prose for any adapter's system prompt."""
    parts: list[str] = []
    if briefing.get("description"):
        parts.append(f"DOMAIN: {briefing['description']}")
    if briefing.get("column_descriptions"):
        lines = ["COLUMN MEANINGS (advisory):"]
        for c, d in briefing["column_descriptions"].items():
            lines.append(f"  - {c!r}: {d}")
        parts.append("\n".join(lines))
    if briefing.get("business_rules"):
        lines = ["BUSINESS RULES (authoritative — these terms have a fixed meaning):"]
        for name, spec in briefing["business_rules"].items():
            lines.append(f"  - {name!r}: {spec['column']!r} in {spec['allowed']}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
