"""S21 shared comparator — ONE function applied identically to all 5 systems.

Reads `gold.result_kind` and applies the corresponding rule:
  - scalar        : |sys - gold| within tolerance
  - table (grouped-aggregate): {group_key: metric} both sides; keys must match;
    metrics within tolerance; ORDER-INSENSITIVE.
  - table (filter/list rows) : SET of the stable per-row id key (employee_id/
    ... — see ID_COLUMN_ALIASES); extra columns TOLERATED — project to the needed
    key before comparing.
  - ordered_list  : SEQUENCE of (key, metric) up to k, ORDER-SENSITIVE; row top-k
    keyed on the id key; grouped top-k keyed on the group column.
  - Numbers       : round both to NUM_DP (currency 2 dp, ratios 6 dp), eps 1e-6.

A list-of-records gold value carrying one of RID_ALIASES as a key signals
"row-list"; otherwise it's a grouped-aggregate. The first non-{metric} key is
treated as the group key.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .types import ID_COLUMN_ALIASES, REFUSAL

CURRENCY_DP = 2
RATIO_DP = 6
EPS = 1e-6
# Domain-portable: any of these signals "this gold record is a row-list, keyed on
# its stable per-row id" rather than a grouped-aggregate. Derived from the same
# source adapters/base.py::df_to_result() renames TO — see ID_COLUMN_ALIASES.
RID_ALIASES = frozenset(ID_COLUMN_ALIASES.values())

# Bumped whenever the scoring rules change, and recorded in provenance (V6) so a
# results.jsonl can be tied to the exact comparator that produced its verdicts.
# See git history for prior versions and the rationale behind each change.
#
# Current (s48) rules beyond the module docstring's core dispatch:
#   * SCALAR UNWRAP — scalar gold answered as a 1-row x (<=2-col) record is correct
#     iff SOME cell value-equals gold under the normal tolerance rules. The
#     <=2-column cap is the deliberate guard: a WIDE row that happens to contain
#     the right value stays wrong; multi-row lists never unwrap.
#   * GROUPED KEY-ORIENTATION — for 2x2 grouped tables the shared group-key column
#     is identified by a CONSERVATIVE name match (case-fold + strip `__group` +
#     drop underscores; NOT aggregate wrappers, NOT punctuation — so count(x)!=x
#     and (%)!=($) never merge). Metric = the remaining column, compared strictly
#     by key. Guarded: collides-within-a-side or non-unique cross match -> no
#     recovery, never a mis-pair.
#   * CASE-INSENSITIVE header matching (_ci_find) on the grouped, row-list-fallback
#     and ordered column lookups; two columns differing ONLY by case in one result
#     are NOT collapsed (ambiguous -> not matched).
#   * _num_eq falls back to string equality on a non-numeric pair rather than
#     treating it as unequal.
#   * GATED RULES (s48) — a gold record carrying a `match` field is scored by the
#     rule it names instead of by result_kind. Only five records in the withheld
#     risk domain's algebra_boundary tier carry one, so every gold record in this
#     release takes the s47 path unchanged. Three kinds, each for a question s47
#     has no faithful rule for:
#       grouped_value — the metric column is found by name, else by VALUE among the
#         produced numeric columns, with optional scales (proportion vs percentage)
#         and a relative tolerance of 1e-3; `keys_are_answer` accepts the right key
#         set shown alone.
#       row_values — a per-row computed value (e.g. a rank) keyed on the entry: the
#         row-list path compares the id set only and would pass an unranked list.
#       pair_set — unordered pairs of entries.
COMPARATOR_VERSION = "s48"


# ── normalisers ──────────────────────────────────────────────────────────────

def _to_num(x):
    """Coerce a python/pandas scalar to float when sensible; else return as-is."""
    try:
        import numpy as np
        if isinstance(x, (np.floating, np.integer)): return float(x)
    except Exception:
        pass
    if isinstance(x, (int, float)) and not isinstance(x, bool): return float(x)
    return x


def _norm_cell(v):
    """Hashable, tolerance-aware key for multiset row matching: numbers rounded to
    RATIO_DP, None preserved, everything else stringified."""
    n = _to_num(v)
    if isinstance(n, float):
        return round(n, RATIO_DP)
    return None if v is None else str(v)


def _round(v, dp):
    return round(float(v), dp) if isinstance(v, (int, float)) else v


def _num_eq(a, b, dp=CURRENCY_DP) -> bool:
    if a is None or b is None: return False
    na, nb = _to_num(a), _to_num(b)
    if isinstance(na, float) and isinstance(nb, float):
        return abs(_round(na, dp) - _round(nb, dp)) <= EPS
    # s37: at least one side isn't numeric (a name/category value used as a
    # grouped-table "metric", or a text scalar) — compare as strings instead of
    # letting the numeric subtraction's TypeError be swallowed as "not equal"
    # (was: any equal-but-non-numeric pair, e.g. 'Algeria' == 'Algeria', always
    # scored as a mismatch).
    return str(a) == str(b)


_MISSING = object()


def _ci_find(rec: dict, name) -> Any:
    """s47 case-insensitive header matching. The key in `rec` naming column
    `name`: the exact match if present, else the UNIQUE case-insensitive match,
    else None. Ambiguity (two keys differing ONLY by case) returns None rather
    than silently collapsing them — the case-fold analog of the value-based
    mis-pairing guard."""
    if name in rec:
        return name
    matches = [k for k in rec if str(k).lower() == str(name).lower()]
    return matches[0] if len(matches) == 1 else None


def _ci_get(rec: dict, name, default=_MISSING):
    k = _ci_find(rec, name)
    return rec[k] if k is not None else default


def _norm_col(name) -> str:
    """s47 conservative column-name canonical form, used ONLY to identify the
    shared GROUP-KEY column of a 2×2 grouped table. Case-fold, strip the
    executor's `__group` suffix, drop underscores/whitespace. Deliberately does
    NOT strip aggregate wrappers (count(x) stays distinct from x) or punctuation
    (`Avg Gap (%)` stays distinct from `($)`), so it never merges genuinely
    different columns."""
    s = str(name).strip().lower()
    s = re.sub(r'__group$|__grp$', '', s)
    return re.sub(r'[\s_]+', '', s)


def _id_key(rows) -> str | None:
    """The stable per-row id key actually present in `rows[0]`, if any — whichever
    of RID_ALIASES the gold author used for this domain. A new domain only needs a
    new entry in ID_COLUMN_ALIASES (types.py); nothing here changes."""
    if not rows or not isinstance(rows[0], dict):
        return None
    return next((k for k in RID_ALIASES if k in rows[0]), None)


def _records_have_rid(rows) -> bool:
    return _id_key(rows) is not None


def _group_key_and_metric(rec: dict) -> tuple[str, str]:
    """For grouped-aggregate gold/produced rows: the first key is the group, the
    second (or any non-group) is the metric. Build_gold writes them in this order."""
    keys = list(rec.keys())
    if len(keys) < 2:
        return keys[0], keys[0]
    return keys[0], keys[1]


def _resolve_substitutions(absent_gold_cols, produced_rows, gold_rows, *,
                           ordered_positional: bool, gold_key: str | None = None):
    """s36 — match a renamed gold column to a produced column by VALUE.

    Returns a non-overlapping {gold_col: produced_col} mapping such that produced
    carries each absent gold column's values under the produced_col name, or None
    if any absent column has no eligible candidate. The candidate pool is produced
    columns that are NOT named after a gold column and NOT the key. Numeric/string
    type must agree: a string column can never substitute for a numeric one.
    For ordered_positional=True the values are compared positionally
    (within RATIO_DP for numerics); for False they are compared as Counters."""
    if not produced_rows or not gold_rows:
        return None
    excluded = {str(c).lower() for c in gold_rows[0]}          # s47 case-insensitive
    if gold_key:
        excluded.add(str(gold_key).lower())
    pool = [c for c in produced_rows[0] if str(c).lower() not in excluded]
    mapping: dict[str, str] = {}
    for a in absent_gold_cols:
        gold_is_num = isinstance(_to_num(gold_rows[0][a]), float)
        for c in pool:
            if c in mapping.values():
                continue
            cand_is_num = isinstance(_to_num(produced_rows[0].get(c)), float)
            if cand_is_num != gold_is_num:
                continue
            if ordered_positional:
                if gold_is_num:
                    ok = all(_num_eq(pr.get(c), gr.get(a), RATIO_DP)
                             for pr, gr in zip(produced_rows, gold_rows))
                else:
                    ok = all(_norm_cell(pr.get(c)) == _norm_cell(gr.get(a))
                             for pr, gr in zip(produced_rows, gold_rows))
            else:
                p_ms = Counter(_norm_cell(r.get(c)) for r in produced_rows)
                g_ms = Counter(_norm_cell(r.get(a)) for r in gold_rows)
                ok = (p_ms == g_ms)
            if ok:
                mapping[a] = c
                break
        else:
            return None
    return mapping


# ── per-kind comparators ─────────────────────────────────────────────────────

def _cmp_scalar(produced, gold) -> tuple[bool, str]:
    pn = _to_num(produced); gn = _to_num(gold)
    # currency / counts: 2 dp; ratios (smaller decimals): 6 dp
    if isinstance(gn, float) and abs(gn) < 1.0:
        ok = _num_eq(pn, gn, RATIO_DP)
    else:
        ok = _num_eq(pn, gn, CURRENCY_DP)
    return ok, ("ok" if ok else f"scalar mismatch: got {pn!r}, gold {gn!r}")


def _cmp_scalar_shape_tolerant(produced, gold) -> tuple[bool, str]:
    """s46 SCALAR UNWRAP. A scalar gold answered as EXACTLY one record with at
    most two columns is the canonical shape the S42 exact-column + S43
    order-closure rules produce for argmax queries (the answer beside its
    mandated ranking column). Correct iff SOME cell value-equals gold under
    _cmp_scalar's own tolerance rules.

    Guards (what keeps this a shape fix, not a loosening):
      - multi-ROW lists never unwrap (a 300-row column containing the gold
        value somewhere is a genuinely multi-value answer — stays wrong);
      - >2 columns never unwrap (the pre-S42 leak shape — a full register row
        that happens to contain the right value — stays wrong);
      - a 1x2 row where NO cell matches stays wrong.
    Accepted residual (documented): a wrong top-1 row whose COMPANION cell
    coincidentally equals gold exactly would pass — requires an exact value
    collision between a ranking metric and the gold answer in a 1-row result;
    the strict alternatives (positional or name-based cell selection) are
    unsound under emission variance, so the value rule is kept."""
    if (isinstance(produced, list) and len(produced) == 1
            and isinstance(produced[0], dict) and 1 <= len(produced[0]) <= 2):
        rec = produced[0]
        for col, cell in rec.items():
            ok, _ = _cmp_scalar(cell, gold)
            if ok:
                extra = [c for c in rec if c != col]
                note = f" beside tolerated column(s) {extra}" if extra else ""
                return True, f"ok (scalar unwrapped from 1x{len(rec)} row: {col!r}{note}, s46)"
        return False, (f"scalar mismatch: got 1x{len(rec)} row {rec!r}, "
                       f"gold {_to_num(gold)!r} — no cell matches after unwrap")
    return _cmp_scalar(produced, gold)


def _cmp_table_rowlist(produced_rows, gold_rows) -> tuple[bool, str]:
    """Compare row-lists. PRIMARY path: SET of the stable per-row id key (tolerate
    extra columns). FALLBACK (produced omits the id key — a correct projection of
    non-id columns, e.g. {Title, Owner}): match on the MULTISET of ALL gold non-id
    columns — multiset, not set, so templated/duplicate rows (e.g. repeated
    {Status, Level}) must match by count.
    s36: produced must COVER the gold projection (intersection-only matching let a
    result MISSING requested columns slip through); extra columns stay tolerated."""
    if not isinstance(produced_rows, list):
        return False, f"expected row-list, got {type(produced_rows).__name__}"
    produced = [r for r in produced_rows if isinstance(r, dict)]
    rid = _id_key(gold_rows)                 # resolved from gold; caller guarantees non-None

    if any(r.get(rid) is not None for r in produced):           # primary: key on the id
        pset = {r.get(rid) for r in produced}
        gset = {r[rid] for r in gold_rows}
        if pset == gset:
            return True, f"row-set match ({len(gset)} ids)"
        miss = gset - pset; extra = pset - gset
        return False, (f"row-set mismatch: missing {len(miss)}, extra {len(extra)} "
                       f"(sample miss: {list(miss)[:3]}, sample extra: {list(extra)[:3]})")

    # fallback: no id key on produced -> multiset over ALL gold non-id columns
    needed = [c for c in gold_rows[0] if c != rid]
    if not produced or not needed:
        return False, "row-list lacks an id key and shares no columns with gold"
    absent = [c for c in needed if any(_ci_find(r, c) is None for r in produced)]  # s47 ci
    sub_note = ""
    mapping: dict[str, str] = {}
    if absent:
        # Value-verified rename tolerance (symmetric with the renamed-metric guard):
        # an absent column may be matched by a renamed produced column carrying the
        # same values; an under-projection (no value match for some column) still
        # fails. Multiset semantics — produced rows may be in any order.
        mapping = _resolve_substitutions(absent, produced, gold_rows,
                                         ordered_positional=False) or {}
        if not mapping:
            return False, (f"row-list lacks an id key and is missing gold column(s) {absent} "
                           f"— an under-projection cannot match the requested columns")
        sub_note = f", values matched by rename: {sorted(mapping)}"

    def _ms(rows, *, prod):
        return Counter(tuple(_norm_cell(_ci_get(r, mapping.get(c, c), None) if prod
                                        else r.get(c))
                             for c in needed) for r in rows)

    pm, gm = _ms(produced, prod=True), _ms(gold_rows, prod=False)
    if pm == gm:
        return True, f"row-multiset match on {needed} ({sum(gm.values())} rows, id-less{sub_note})"
    miss = gm - pm; extra = pm - gm
    return False, (f"row-multiset mismatch on {needed}: missing {sum(miss.values())}, "
                   f"extra {sum(extra.values())}")


def _grouped_map_match(pd_map: dict, gd: dict) -> tuple[bool, str]:
    """{group: metric} equality: key sets equal, metrics within tolerance."""
    if set(pd_map) != set(gd):
        miss = set(gd) - set(pd_map); extra = set(pd_map) - set(gd)
        return False, (f"group-key mismatch: missing {sorted(miss, key=repr)[:5]}, "
                       f"extra {sorted(extra, key=repr)[:5]}")
    for k, gv in gd.items():
        if not _num_eq(pd_map[k], gv, RATIO_DP):
            return False, f"metric mismatch at {k!r}: got {pd_map[k]!r}, gold {gv!r}"
    return True, f"grouped match ({len(gd)} keys)"


def _grouped_via_keyname(produced_rows, gold_rows):
    """s47 — identify the shared GROUP-KEY column by CONSERVATIVE name match
    (replaces s46's value-based orientation fallback). The group key is a real
    schema column preserved on both sides (`year`, `GovernmentForm`); only the
    aggregate metric gets an arbitrary per-system name (`match_count`,
    `total_population`). Find the UNIQUE (gold_col, produced_col) pair whose
    normalized names match — that is the group key; the metric is the remaining
    column on each side; compare {key: metric} strictly.

    Name-based only — never infers a column's role from its values. Guarded to
    2×2 tables; returns None (no recovery → caller's strict result stands) if
    normalization collides two columns within either side, or the cross-side
    name match is not unique. When the key IS uniquely identified the comparison
    is authoritative (correct, or accurate-wrong on metric disagreement)."""
    gcols = list(gold_rows[0]); pcols = list(produced_rows[0])
    if len(gcols) != 2 or len(pcols) != 2:
        return None
    gn = {c: _norm_col(c) for c in gcols}
    pn = {c: _norm_col(c) for c in pcols}
    if len(set(gn.values())) < 2 or len(set(pn.values())) < 2:
        return None                                   # within-side collision guard
    pairs = [(gc, pc) for gc in gcols for pc in pcols if gn[gc] == pn[pc]]
    if len(pairs) != 1:
        return None                                   # key not uniquely identifiable
    gk, pk = pairs[0]
    gm = next(c for c in gcols if c != gk)
    pmc = next(c for c in pcols if c != pk)
    gd = {r[gk]: r[gm] for r in gold_rows}
    pd_map = {r[pk]: r[pmc] for r in produced_rows}
    ok, reason = _grouped_map_match(pd_map, gd)
    if ok:
        return True, (f"grouped match ({len(gd)} keys; group key matched by name: "
                      f"gold {gk!r}, produced {pk!r}, s47)")
    return False, reason        # key identified, metrics disagree → authoritative wrong


def _cmp_table_grouped(produced_rows, gold_rows) -> tuple[bool, str]:
    """Compare grouped aggregates as {group: metric}; order-insensitive.

    Primary path: gold's first column is the group key (build_gold convention),
    the produced key/metric columns matched case-insensitively (s47 _ci_find).
    When that fails on a 2×2 table, _grouped_via_keyname identifies the group
    key by conservative NAME normalization (s47 — replaces s46's value-based
    orientation fallback; see the module version note). 3+-column tables keep
    the first-column rule."""
    if not isinstance(produced_rows, list):
        return False, f"expected grouped table, got {type(produced_rows).__name__}"
    if not gold_rows:
        return produced_rows == [], "empty gold"
    if not produced_rows or not isinstance(produced_rows[0], dict):
        return False, "produced rows are not dicts"
    gk, mk = _group_key_and_metric(gold_rows[0])
    gd = {r[gk]: r[mk] for r in gold_rows}
    pd_rec0 = produced_rows[0]
    pk = _ci_find(pd_rec0, gk) or next(iter(pd_rec0))          # s47 case-insensitive
    pm = _ci_find(pd_rec0, mk) or next((k for k in pd_rec0 if k != pk), pk)
    pd_map = {r[pk]: r[pm] for r in produced_rows}
    ok, reason = _grouped_map_match(pd_map, gd)
    if ok:
        return ok, reason
    res = _grouped_via_keyname(produced_rows, gold_rows)       # s47 name-based fallback
    if res is not None:
        return res
    return ok, reason


def _tie_groups(gold_rows, numeric_cols) -> list[tuple[int, int]]:
    """Maximal runs [start, end) of consecutive gold rows equal on EVERY numeric
    column (within tolerance) — a tie has no defined order, so any permutation
    within the run is a valid answer. Conservative on purpose: rows must tie on
    ALL numeric columns, never on a guessed "the" metric column.
    With no numeric columns every row is its own group (order stays fully checked)."""
    groups, start = [], 0
    for i in range(1, len(gold_rows) + 1):
        tied = (i < len(gold_rows) and bool(numeric_cols) and
                all(_num_eq(gold_rows[i].get(k), gold_rows[i - 1].get(k), RATIO_DP)
                    for k in numeric_cols))
        if not tied:
            groups.append((start, i))
            start = i
    return groups


def _cmp_ordered(produced_rows, gold_rows) -> tuple[bool, str]:
    """Compare ordered lists as a SEQUENCE. Correct iff:
      (1) the KEY sequence matches in order — the id key for row top-k when the
          result carries it; the group column for grouped top-k; an ID-LESS result
          (no id key on ANY row) is keyed positionally on ALL gold non-id
          columns and must COVER them (a missing requested column is wrong; extra
          columns stay tolerated); AND
      (2) every NUMERIC gold column also present (by name) in the result matches
          positionally within tolerance — and a gold numeric column ABSENT by name
          must be matched BY VALUE by some produced numeric column: a result
          whose metric values cannot be verified anywhere is not a correct answer,
          key order alone never is; AND
      (3) within a run of gold rows tied on EVERY numeric column, produced rows may
          appear in any order — the run compares as a multiset.
    String columns (e.g. Title) are ignored on the keyed paths; extra result columns
    are tolerated. No attempt is made to identify "the" metric column: picking one
    heuristically risks selecting a string column and numeric-comparing it."""
    if not isinstance(produced_rows, list):
        return False, f"expected ordered list, got {type(produced_rows).__name__}"
    if not gold_rows:
        return produced_rows == [], "empty gold"
    if len(produced_rows) != len(gold_rows):
        return False, f"length mismatch: got {len(produced_rows)}, gold {len(gold_rows)}"
    if not all(isinstance(r, dict) for r in produced_rows):
        return False, "produced rows are not dicts"

    rid = _id_key(gold_rows)
    idless = rid is not None and not any(r.get(rid) is not None for r in produced_rows)
    if rid is not None:
        gkey = pkey = rid
    else:
        gkey, _ = _group_key_and_metric(gold_rows[0])
        pr0 = produced_rows[0]
        pkey = _ci_find(pr0, gkey) or next(iter(pr0))          # s47 case-insensitive

    # numeric gold columns (other than the key) — drive the positional value checks
    # AND the tie grouping (the key is an entity label, never a metric).
    numeric_cols = [k for k, v in gold_rows[0].items()
                    if k != gkey and isinstance(_to_num(v), float)]

    idless_mapping: dict[str, str] = {}
    idless_note = ""
    if idless:
        # s36 fallback — mirror of the S29 row-list fallback, ORDER-SENSITIVE: each
        # position is the tuple of ALL gold non-id columns. Produced must cover them,
        # but an absent column may be matched by a renamed produced column carrying
        # the same values (value-verified rule, symmetric with the keyed 1b path).
        needed = [c for c in gold_rows[0] if c != rid]
        if not needed:
            return False, "ordered list lacks an id key and shares no columns with gold"
        absent = [c for c in needed
                  if any(_ci_find(r, c) is None for r in produced_rows)]     # s47 ci
        if absent:
            idless_mapping = _resolve_substitutions(absent, produced_rows, gold_rows,
                                                    ordered_positional=True) or {}
            if not idless_mapping:
                return False, (f"ordered list lacks an id key and is missing gold column(s) "
                               f"{absent} — an under-projection cannot match the requested columns")
            idless_note = f", values matched by rename: {sorted(idless_mapping)}"

        def _sig(r, *, prod):
            return tuple(_norm_cell(_ci_get(r, idless_mapping.get(c, c), None) if prod
                                    else r.get(c))
                         for c in needed)
    metric_note = ""
    if not idless and numeric_cols:
        # Renamed-metric guard (VALUE-verified): a gold numeric column absent by
        # NAME must be matched by VALUE by some produced NUMERIC column at every
        # position — never a string column. Within a tied run gold values are
        # constant, so a valid tie permutation cannot break the positional value
        # match. If no produced column carries the gold values, the result is
        # unverifiable-or-wrong: key order alone is not a correct answer. This is
        # deliberately value-based rather than name-strict; see git history.
        absent = [k for k in numeric_cols
                  if not any(_ci_find(pr, k) is not None for pr in produced_rows)]  # s47 ci
        if absent:
            mapping = _resolve_substitutions(absent, produced_rows, gold_rows,
                                             ordered_positional=True, gold_key=pkey)
            if mapping is None:
                return False, (f"gold numeric column(s) {absent} neither present by name "
                               f"nor matched by value in any produced numeric column "
                               f"— renamed-or-missing metric, values unverifiable or wrong")
            metric_note = f", metric matched by value: {sorted(mapping)}"

    for gs, ge in _tie_groups(gold_rows, numeric_cols):
        if idless:
            if ge - gs == 1:
                if _sig(produced_rows[gs], prod=True) != _sig(gold_rows[gs], prod=False):
                    return False, (f"position {gs}: row mismatch (id-less) — "
                                   f"got {_sig(produced_rows[gs], prod=True)!r}, "
                                   f"gold {_sig(gold_rows[gs], prod=False)!r}")
            else:
                pms = Counter(_sig(r, prod=True) for r in produced_rows[gs:ge])
                gms = Counter(_sig(r, prod=False) for r in gold_rows[gs:ge])
                if pms != gms:
                    return False, (f"positions {gs}–{ge - 1} (tied run): row multiset "
                                   f"mismatch (id-less)")
            continue
        if ge - gs > 1:                                  # tied run: keys as a multiset
            pk_ms = Counter(pr.get(pkey) for pr in produced_rows[gs:ge])
            gk_ms = Counter(gr[gkey] for gr in gold_rows[gs:ge])
            if pk_ms != gk_ms:
                return False, (f"positions {gs}–{ge - 1} (tied run): key mismatch — "
                               f"got {sorted(pk_ms - gk_ms, key=repr)!r}, "
                               f"gold {sorted(gk_ms - pk_ms, key=repr)!r}")
        for i in range(gs, ge):
            pr, gr = produced_rows[i], gold_rows[i]
            if ge - gs == 1 and pr.get(pkey) != gr[gkey]:
                return False, f"position {i}: key mismatch — got {pr.get(pkey)!r}, gold {gr[gkey]!r}"
            for k in numeric_cols:
                # within a tied run every gold row carries the same values, so the
                # positional gold row is a valid reference either way
                pv = _ci_get(pr, k)                            # s47 case-insensitive
                if pv is not _MISSING and not _num_eq(pv, gr[k], RATIO_DP):
                    return False, (f"position {i}: {k!r} mismatch — "
                                   f"got {pv!r}, gold {gr[k]!r}")
    return True, (f"ordered match ({len(gold_rows)} rows"
                  f"{', id-less' if idless else ''}{metric_note}{idless_note})")


# ── s48 gated rules (gold["match"]) ─────────────────────────────────────────

_S48_REL = 1e-3
_ID_COL = re.compile(r"(?:^|[^a-z])id(?:$|[^a-z])")


def _close_rel(p, g) -> bool:
    pn, gn = _to_num(p), _to_num(g)
    if not (isinstance(pn, float) and isinstance(gn, float)):
        return False
    return abs(pn - gn) <= max(EPS, _S48_REL * abs(gn))


def _numeric_cols(rows, exclude) -> list:
    return [c for c in rows[0] if c not in exclude
            and all(isinstance(_to_num(r.get(c)), float) for r in rows)]


def _entity_ids(rec: dict, universe: set, id_map: dict) -> list:
    """Entries a produced row names: string cells that are known entry ids, plus
    integer cells in id-named columns mapped through the register's integer id."""
    out = []
    for k, v in rec.items():
        if isinstance(v, str) and v in universe:
            out.append(v)
        elif (isinstance(v, (int, float)) and not isinstance(v, bool)
              and _ID_COL.search(str(k).lower()) and float(v).is_integer()
              and str(int(v)) in id_map):
            out.append(id_map[str(int(v))])
    return list(dict.fromkeys(out))


def _cmp_grouped_value(produced, gold, rule) -> tuple[bool, str]:
    if not (isinstance(produced, list) and produced and all(isinstance(r, dict) for r in produced)):
        return False, "expected grouped table (s48)"
    gk, gm = _group_key_and_metric(gold[0])
    gd = {r[gk]: r[gm] for r in gold}
    pk = _ci_find(produced[0], gk) or next(iter(produced[0]))
    keys = [r.get(pk) for r in produced]
    if len(keys) != len(set(keys)) or set(keys) != set(gd):
        return False, (f"group-key mismatch (s48): got {len(set(keys))} keys over {len(keys)} rows, "
                       f"gold {len(gd)}")
    cands = _numeric_cols(produced, {pk})
    named = _ci_find(produced[0], gm)
    if named in cands:
        cands = [named] + [c for c in cands if c != named]
    for c in cands:
        for sc in rule.get("scales", [1]):
            if all(_close_rel(r[c], gd[r[pk]] * sc) for r in produced):
                return True, (f"grouped match by value (s48): key {pk!r}, metric {c!r}"
                              + (f" x{sc}" if sc != 1 else ""))
    if rule.get("keys_are_answer") and not cands:
        return True, f"key-set match (s48): the {len(gd)} keys are the answer, no metric shown"
    return False, f"metric mismatch (s48): no produced numeric column carries gold {gm!r}"


def _cmp_row_values(produced, gold, rule) -> tuple[bool, str]:
    rid, alt, vc = rule["id"], rule.get("alt_id"), rule["value"]
    universe = {r[rid] for r in gold}
    id_map = {str(int(r[alt])): r[rid] for r in gold} if alt else {}
    gv = {r[rid]: r[vc] for r in gold}
    if not (isinstance(produced, list) and produced and all(isinstance(r, dict) for r in produced)):
        return False, "expected row-list (s48)"
    ents = []
    for r in produced:
        e = _entity_ids(r, universe, id_map)
        if len(e) != 1:
            return False, f"a row does not identify exactly one entry (s48): {list(r)[:4]}"
        ents.append(e[0])
    if len(ents) != len(set(ents)) or set(ents) != set(gv):
        return False, f"row-set mismatch (s48): {len(set(ents))} entries over {len(ents)} rows, gold {len(gv)}"
    exclude = {k for k in produced[0] if _ID_COL.search(str(k).lower())}
    cands = _numeric_cols(produced, exclude)
    named = _ci_find(produced[0], vc)
    if named in cands:
        cands = [named] + [c for c in cands if c != named]
    for c in cands:
        if all(_num_eq(r[c], gv[e], RATIO_DP) for r, e in zip(produced, ents)):
            return True, f"per-row value match (s48): {c!r} over {len(gv)} entries"
    return False, f"per-row value mismatch (s48): no numeric column carries gold {vc!r}"


def _cmp_pair_set(produced, gold, rule) -> tuple[bool, str]:
    id_map = rule["id_map"]
    universe = set(id_map.values())
    gset = {frozenset(_entity_ids(r, universe, id_map)) for r in gold}
    if not (isinstance(produced, list) and all(isinstance(r, dict) for r in produced)):
        return False, "expected row-list of pairs (s48)"
    pairs = []
    for r in produced:
        e = _entity_ids(r, universe, id_map)
        if len(e) != 2:
            return False, f"a row does not name exactly two entries (s48): {list(r)[:4]}"
        pairs.append(frozenset(e))
    if len(pairs) != len(gset) or set(pairs) != gset:
        return False, (f"pair-set mismatch (s48): {len(set(pairs))} distinct pairs over {len(pairs)} rows, "
                       f"gold {len(gset)}")
    return True, f"pair-set match (s48): {len(gset)} pairs"


_S48_RULES = {"grouped_value": _cmp_grouped_value, "row_values": _cmp_row_values,
              "pair_set": _cmp_pair_set}


# ── top-level scoring ────────────────────────────────────────────────────────

def score(produced: Any, gold_record: dict, *, status: str = "completed") -> tuple[str, str, str | None]:
    """Score a produced result against a gold record.

    Returns (outcome_class, reason, failure_tag|None). The failure_tag is only
    set on non-correct in-range outcomes. Out-of-range refusal-vs-answer is
    classified here too (correct = refused).

    `status` is the adapter's RunResult status. OPERATIONAL failures are classified
    by status BEFORE any value comparison — a crash/timeout is never a "silent wrong
    answer" (scoring its empty/None value as silent_wrong corrupts the headline
    silent-error metric). Only "crashed"/"timeout" short-circuit; a genuine refusal
    (status="refused", result=REFUSAL) flows through the normal refusal logic below.
    """
    if status == "crashed":
        return "crash", "adapter crashed (no answer produced)", None
    if status == "timeout":
        return "timeout", "bound hit (max_iters / max_retries / wall) — no answer", None

    expected = gold_record["expected_behavior"]
    refused = (produced == REFUSAL)

    # ── out_of_range: correct = refused; silent_wrong = answered confidently ──
    if expected == "refuse":
        if refused:
            return "correct", "refused as expected (out of range)", None
        return "silent_wrong", "confident answer on an out-of-range query", "over_interpretation"

    # ── in-range: must produce a value matching gold ─────────────────────────
    if refused:
        return "refused_should_answer", "refused but the query has an answer", "gave_up"

    kind = gold_record["result_kind"]
    gold = gold_record["value"]
    rule = gold_record.get("match")
    if rule:                                                     # s48: gated rules only
        ok, reason = _S48_RULES[rule["kind"]](produced, gold, rule)
    elif kind == "scalar":
        ok, reason = _cmp_scalar_shape_tolerant(produced, gold)
    elif kind == "table":
        if _records_have_rid(gold):
            ok, reason = _cmp_table_rowlist(produced, gold)
        else:
            ok, reason = _cmp_table_grouped(produced, gold)
    elif kind == "ordered_list":
        ok, reason = _cmp_ordered(produced, gold)
    else:
        return "silent_wrong", f"unknown result_kind {kind!r}", "other"

    if ok:
        return "correct", reason, None
    # naive failure tagging from the reason
    tag: str = "other"
    if "missing" in reason and "extra" in reason:
        # both sides differ -> over_interpretation if produced has extra, under if missing
        tag = "over_interpretation" if "extra 0" not in reason else "under_declaration"
    elif "row-set" in reason and "missing" in reason:
        tag = "under_declaration"
    elif "scalar mismatch" in reason or "metric mismatch" in reason:
        tag = "arithmetic"
    elif "group-key mismatch" in reason or "key mismatch" in reason:
        tag = "over_interpretation"
    return "silent_wrong", reason, tag
