"""S14 — schema bridge: flow environment/schema.json's content to LLM1 in the
simple-identifier (alias) space the planner's expr parser needs.

Provides a BIJECTIVE real<->alias column map (forward renames real data into alias
space; inverse maps results back at execution/output), an alias-space schema VIEW
carrying per-column type + match_strategy + categorical values, and the generic,
schema-declared `business_rules` (each {column, include|exclude} → an allowed value
set computed from the column's categories; malformations raise SchemaRuleError).

Reuses S13's `alias_name`; the planner core is untouched.
"""
from __future__ import annotations

import json
from pathlib import Path

from .integrate import alias_name

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "environment" / "schema.json"


class AliasMap:
    """Bijective real <-> simple-identifier column map (deterministic; dedups on
    collision so the inverse is well-defined)."""

    def __init__(self, real_columns):
        self._r2a: dict[str, str] = {}
        self._a2r: dict[str, str] = {}
        seen: dict[str, int] = {}
        for real in real_columns:
            a = alias_name(real)
            if a in seen:
                seen[a] += 1
                a = f"{a}_{seen[a]}"
            else:
                seen[a] = 0
            self._r2a[str(real)] = a
            self._a2r[a] = str(real)

    def to_alias(self, real: str) -> str:
        return self._r2a[str(real)]

    def to_real(self, alias: str) -> str:
        return self._a2r[str(alias)]

    def has_real(self, real: str) -> bool:
        return str(real) in self._r2a

    @property
    def reals(self):
        return list(self._r2a)

    @property
    def aliases(self):
        return list(self._a2r)


def load_schema(path=SCHEMA_PATH) -> dict:
    return json.loads(Path(path).read_text())


class SchemaRuleError(Exception):
    """A schema-declared business_rule is missing or malformed. Raised LOUDLY
    rather than silently defaulting to a permissive set — this is the correctness
    crux that turns the S16 silent-wrong-active bug into a clear failure."""


def _rule_allowed(name: str, rule, cols: dict) -> dict:
    """Compute a rule's allowed value set from its column's categories, generically.
    exclude → categories minus exclude; include → the listed values. Validates and
    raises SchemaRuleError on any problem; never returns a permissive 'all' by default."""
    if not isinstance(rule, dict):
        raise SchemaRuleError(f"rule {name!r}: must be an object {{column, include|exclude}}")
    col = rule.get("column")
    if not col or col not in cols:
        raise SchemaRuleError(f"rule {name!r}: column {col!r} is not a schema column")
    cats = cols[col].get("categories")
    if not cats:
        raise SchemaRuleError(f"rule {name!r}: column {col!r} has no categories to derive the set from")
    has_inc, has_exc = ("include" in rule), ("exclude" in rule)
    if has_inc == has_exc:
        raise SchemaRuleError(f"rule {name!r}: declare exactly one of 'include' or 'exclude'")
    listed = rule["include"] if has_inc else rule["exclude"]
    if not isinstance(listed, list):
        kind = "include" if has_inc else "exclude"
        raise SchemaRuleError(f"rule {name!r}: '{kind}' must be a list")
    bad = [v for v in listed if v not in cats]
    if bad:
        raise SchemaRuleError(f"rule {name!r}: values {bad} not in {col!r} categories {cats}")
    allowed = list(listed) if has_inc else [c for c in cats if c not in listed]
    return {"column": col, "allowed": allowed, "match_strategy": cols[col].get("match_strategy", "exact"),
            "aliases": list(rule.get("aliases") or [])}


def build_bridge(register_columns, schema_path=SCHEMA_PATH):
    """Return (alias_map, alias_schema_view, business_rules).

    alias_schema_view: {alias: {type, match_strategy, categories?}} for the
                       documented columns (intersection of schema and the data).
    business_rules:    {rule_name: {column(alias), allowed, match_strategy}} — each
                       rule's allowed set computed GENERICALLY from the column's
                       categories (categories−exclude, or include). Raises
                       SchemaRuleError on any malformation (never a permissive default).
    """
    amap = AliasMap(register_columns)
    schema = load_schema(schema_path)
    cols = schema.get("columns", {})

    view: dict[str, dict] = {}
    for real, info in cols.items():
        if not amap.has_real(real):
            continue
        entry = {"type": info.get("type"), "match_strategy": info.get("match_strategy")}
        if info.get("type") == "categorical" and info.get("categories"):
            entry["categories"] = list(info["categories"])
        view[amap.to_alias(real)] = entry

    business_rules: dict[str, dict] = {}
    for name, rule in (schema.get("business_rules") or {}).items():
        r = _rule_allowed(name, rule, cols)
        business_rules[name] = {"column": amap.to_alias(r["column"]),
                                "allowed": r["allowed"], "match_strategy": r["match_strategy"],
                                "aliases": r["aliases"]}
    return amap, view, business_rules


def build_briefing(schema, amap) -> dict | None:
    """Project the ADVISORY `domain` block into alias space:
    {description, column_descriptions: {alias: text}} — or None if absent.

    Advisory ONLY: this is context for the LLM prompt and is NEVER consulted by the
    engine for correctness (that depends solely on `business_rules` + the typed goal).
    No validation/fail-loud here — a missing or partial briefing simply yields less
    prompt context, never a wrong answer."""
    if isinstance(schema, (str, Path)):
        schema = load_schema(schema)
    dom = schema.get("domain")
    if not dom:
        return None
    cds = {}
    for real, text in (dom.get("column_descriptions") or {}).items():
        if amap.has_real(real):
            cds[amap.to_alias(real)] = text
    return {"description": dom.get("description", ""), "column_descriptions": cds}
