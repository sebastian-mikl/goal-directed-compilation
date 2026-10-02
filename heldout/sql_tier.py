"""The held-out tier rule, applied mechanically to a Spider gold SQL string.

Stdlib only, zero engine imports: the rule reads the reference SQL and the
database's table list, never a system output and never planner/ or gdc/ code.
Fixed by heldout/PREREGISTRATION.md (section 4) before any held-out cell runs.

    tier_of(sql, tables, register) -> dict(tier, expected_behavior, tables,
                                           architectural_gap, sql_structure)

Order of the rule:
  1. The SQL reads any table other than the register (anywhere: a JOIN, a
     subquery, a set-operation branch)            -> out_of_scope_register
  2. It reads only the register but uses a construct outside the Goal
     vocabulary (gap labels below)                -> out_of_contract_aggregate
  3. Otherwise                                    -> answer; the tier field is
     the SQL structure (filter / aggregate / group_by / order_by), as in the
     three released Spider corpora.

The Goal vocabulary is the one planner/contract.py, planner/order.py and
gdc/primitives.py declare at the frozen commit: a conjunction of `col op
literal` predicates with op in == != < <= > >= in contains; at most one
aggregate in sum / avg / count / min / max / count_distinct, over all rows or
per group, with only the group keys and that aggregate in a grouped output;
computed columns in + - * / over columns and numeric literals; an OrderSpec of
sort keys and a row limit. Gap labels: the four the paper names (having_filter,
multi_aggregate, aggregate_subquery, group_attribute_projection), the released
corpora's distinct_projection, and eight for constructs the three development
databases' answer and out_of_contract_aggregate tiers did not exercise
(set_operation, self_join, subquery, pattern_match, scalar_function,
disjunction, null_predicate, limit_offset).
"""
from __future__ import annotations

import re

AGG_FNS = {"count", "sum", "avg", "min", "max"}
SET_OPS = {"intersect", "union", "except"}
CLAUSE_KW = {"select", "from", "where", "group", "having", "order", "limit"}
STRUCTURES = ("filter", "aggregate", "group_by", "order_by", "multi_or_compound")

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<str>'(?:[^']|'')*'|"(?:[^"]|"")*")
  | (?P<bq>`[^`]*`)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|!=|<>|==|\|\||[=<>+\-*/%])
  | (?P<punct>[(),.;])
""", re.VERBOSE)


def lex(sql: str) -> list[tuple[str, str]]:
    """[(kind, text)] with kind in str/num/ident/op/punct. Double-quoted text is a
    string literal, as SQLite reads it in Spider's gold SQL; backticks quote an
    identifier."""
    out, i = [], 0
    while i < len(sql):
        m = _TOKEN_RE.match(sql, i)
        if not m:
            raise ValueError(f"cannot lex {sql[i:i + 20]!r} in {sql!r}")
        kind = m.lastgroup
        if kind == "bq":
            out.append(("ident", m.group()[1:-1]))
        elif kind != "ws":
            out.append((kind, m.group()))
        i = m.end()
    return out


def _kw(tok, *words) -> bool:
    return tok[0] == "ident" and tok[1].lower() in words


def _depths(toks) -> list[int]:
    d, out = 0, []
    for t in toks:
        if t == ("punct", "("):
            out.append(d)
            d += 1
        elif t == ("punct", ")"):
            d -= 1
            out.append(d)
        else:
            out.append(d)
    return out


def _split_top(toks, pred) -> list[list]:
    """Split a token list at depth-0 tokens satisfying pred (separators dropped)."""
    parts = [[]]
    for t, d in zip(toks, _depths(toks)):
        if d == 0 and pred(t):
            parts.append([])
        else:
            parts[-1].append(t)
    return parts


def _subqueries(toks) -> list[tuple[int, int]]:
    """(start, end) of each '(' SELECT ... ')' at any depth: token indexes of the
    inner query, outermost first."""
    out, stack = [], []
    for i, t in enumerate(toks):
        if t == ("punct", "("):
            stack.append(i)
        elif t == ("punct", ")") and stack:
            j = stack.pop()
            if j + 1 < len(toks) and _kw(toks[j + 1], "select"):
                out.append((j + 1, i))
    return sorted(out)


def _blank_subqueries(toks) -> list:
    """The token list with every top-level subquery replaced by one placeholder
    ident, so clause analysis of the outer query never reads into it."""
    out, i, d = [], 0, 0
    while i < len(toks):
        t = toks[i]
        if t == ("punct", "(") and i + 1 < len(toks) and _kw(toks[i + 1], "select"):
            depth, j = 1, i + 1
            while depth:
                if toks[j] == ("punct", "("):
                    depth += 1
                elif toks[j] == ("punct", ")"):
                    depth -= 1
                j += 1
            out.append(("ident", "__subquery__"))
            i = j
            continue
        out.append(t)
        i += 1
    return out


def _clauses(core) -> dict[str, list]:
    """Clause token lists of one SELECT core (no set operation, subqueries
    blanked): select / from / where / group / having / order / limit."""
    out, cur = {}, None
    depths = _depths(core)
    i = 0
    while i < len(core):
        t, d = core[i], depths[i]
        if d == 0 and t[0] == "ident" and t[1].lower() in CLAUSE_KW:
            cur = t[1].lower()
            out.setdefault(cur, [])
            if cur in ("group", "order") and i + 1 < len(core) and _kw(core[i + 1], "by"):
                i += 1
            i += 1
            continue
        if cur is not None:
            out[cur].append(t)
        i += 1
    return out


def _from_tables(from_toks) -> tuple[list[str], int]:
    """(table names, number of table references) of a FROM clause: the
    identifier after FROM, after each JOIN, and after each depth-0 comma
    outside an ON condition."""
    names, refs = [], 0
    expect, in_on = True, False
    for t, d in zip(from_toks, _depths(from_toks)):
        if d != 0:
            continue
        if _kw(t, "join"):
            expect, in_on = True, False
            continue
        if _kw(t, "on"):
            in_on = True
            continue
        if t == ("punct", ",") and not in_on:
            expect = True
            continue
        if expect and t[0] == "ident" and t[1].lower() not in ("as", "inner", "left", "outer", "cross", "natural"):
            names.append(t[1])
            refs += 1
            expect = False
    return names, refs


def _calls(toks) -> list[tuple[str, list]]:
    """(function name lower, argument tokens) for every name( ... ) application."""
    out = []
    for i in range(len(toks) - 1):
        if toks[i][0] == "ident" and toks[i + 1] == ("punct", "("):
            depth, j = 1, i + 2
            while j < len(toks) and depth:
                if toks[j] == ("punct", "("):
                    depth += 1
                elif toks[j] == ("punct", ")"):
                    depth -= 1
                j += 1
            out.append((toks[i][1].lower(), toks[i + 2:j - 1]))
    return out


def _norm(toks) -> str:
    return " ".join(t[1].lower() for t in toks)


def _col_names(toks) -> set[str]:
    """Bare column names an expression refers to (alias prefixes dropped,
    aggregate arguments and keywords excluded)."""
    out, skip_to = set(), -1
    i = 0
    while i < len(toks):
        t = toks[i]
        if t[0] == "ident" and i + 1 < len(toks) and toks[i + 1] == ("punct", "("):
            i += 1
            continue
        if t[0] == "ident" and t[1].lower() not in {"distinct", "as", "asc", "desc", "and", "or", "not", "null", "is"}:
            if i + 1 < len(toks) and toks[i + 1] == ("punct", "."):
                i += 1          # alias prefix
            else:
                out.add(t[1].lower())
        i += 1
    return out


def _outside_aggs(toks) -> list:
    """The tokens of an expression with every aggregate application removed."""
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t[0] == "ident" and t[1].lower() in AGG_FNS and i + 1 < len(toks) and toks[i + 1] == ("punct", "("):
            depth, j = 1, i + 2
            while j < len(toks) and depth:
                if toks[j] == ("punct", "("):
                    depth += 1
                elif toks[j] == ("punct", ")"):
                    depth -= 1
                j += 1
            i = j
            continue
        out.append(t)
        i += 1
    return out


def _is_eq_literal(pred) -> str | None:
    """The column of a `col = literal` (or `col IN (literals)`) predicate, else None."""
    p = _strip_parens(list(pred))
    if len(p) >= 3 and p[0][0] == "ident" and p[1] == ("punct", "."):
        p = p[2:]                                   # alias.col
    eq = (("op", "="), ("op", "=="))
    if len(p) == 3 and p[0][0] == "ident" and p[1] in eq and p[2][0] in ("str", "num"):
        return p[0][1].lower()
    if len(p) >= 4 and p[0][0] == "ident" and _kw(p[1], "in") and p[2] == ("punct", "(") \
            and all(t[0] in ("str", "num") or t == ("punct", ",") for t in p[3:-1]):
        return p[0][1].lower()
    return None


def _strip_parens(p):
    while p and p[0] == ("punct", "(") and p[-1] == ("punct", ")") and \
            all(d > 0 for d in _depths(p)[1:-1]):
        p = p[1:-1]
    return p


def _where_gaps(where) -> set[str]:
    """Gap labels of a WHERE clause (subqueries already blanked)."""
    gaps = set()
    if any(_kw(t, "is") for t in where) and any(_kw(t, "null") for t in where):
        gaps.add("null_predicate")
    toks = list(where)
    for k, t in enumerate(toks):
        if _kw(t, "like", "glob"):
            neg = k > 0 and _kw(toks[k - 1], "not")
            pat = toks[k + 1][1][1:-1] if k + 1 < len(toks) and toks[k + 1][0] == "str" else None
            inner = pat[1:-1] if pat and len(pat) >= 2 and pat[0] == "%" and pat[-1] == "%" else None
            if neg or _kw(t, "glob") or inner is None or "%" in inner or "_" in inner:
                gaps.add("pattern_match")
    gaps |= _bool_gaps(toks)
    return gaps


def _bool_gaps(toks) -> set[str]:
    toks = _strip_parens(toks)
    disj = _split_top(toks, lambda t: _kw(t, "or"))
    if len(disj) > 1:
        cols = {_is_eq_literal(_strip_parens(d)) for d in disj}
        return set() if (None not in cols and len(cols) == 1) else {"disjunction"}
    # conjunction: split at depth-0 AND, skipping the AND of a BETWEEN
    parts, cur, between = [], [], False
    for t, d in zip(toks, _depths(toks)):
        if d == 0 and _kw(t, "between"):
            between = True
        if d == 0 and _kw(t, "and"):
            if between:
                between = False
                cur.append(t)
                continue
            parts.append(cur)
            cur = []
            continue
        cur.append(t)
    parts.append(cur)
    gaps = set()
    for p in parts:
        if p and p[0] == ("punct", "(") and p[-1] == ("punct", ")"):
            gaps |= _bool_gaps(p)
    return gaps


def classify(sql: str, tables: list[str] | set[str]) -> dict:
    """Facts about one gold SQL: every real table it reads, structure, gap labels."""
    real = {t.lower(): t for t in tables}
    toks = [t for t in lex(sql) if t != ("punct", ";")]
    low = [t[1].lower() if t[0] == "ident" else None for t in toks]
    set_op = any(w in SET_OPS for w in low)

    # every query block: the whole query, each set-op branch, each subquery
    blocks = []
    for seg in _split_top(_blank_subqueries(toks), lambda t: _kw(t, *SET_OPS)):
        blocks.append(("main" if not blocks else "branch", seg))
    for s, e in _subqueries(toks):
        inner = toks[s:e]
        for seg in _split_top(_blank_subqueries(inner), lambda t: _kw(t, *SET_OPS)):
            blocks.append(("subquery", seg))

    read, unresolved, join = [], [], False
    for kind, seg in blocks:
        cl = _clauses(seg)
        names, refs = _from_tables(cl.get("from", []))
        if refs > 1:
            join = True
        for n in names:
            if n == "__subquery__":
                continue
            if n.lower() in real:
                if real[n.lower()] not in read:
                    read.append(real[n.lower()])
            else:
                unresolved.append(n)

    main = _clauses(blocks[0][1])
    sel, order, group = main.get("select", []), main.get("order", []), main.get("group", [])
    all_calls = _calls(toks)
    has_agg_anywhere = any(f in AGG_FNS for f, _ in all_calls)

    gaps = set()
    if set_op:
        gaps.add("set_operation")
    if any(_kw(t, "having") for t in toks):
        gaps.add("having_filter")
    # subqueries: aggregate ones are aggregate_subquery, the rest subquery
    for s, e in _subqueries(toks):
        inner_sel = _clauses(_blank_subqueries(toks[s:e])).get("select", [])
        if any(f in AGG_FNS for f, _ in _calls(inner_sel)):
            gaps.add("aggregate_subquery")
        else:
            gaps.add("subquery")
    # functions other than the five aggregates; DISTINCT inside a non-count aggregate
    for f, args in all_calls:
        if f not in AGG_FNS:
            gaps.add("scalar_function")
        elif args and _kw(args[0], "distinct") and f != "count":
            gaps.add("scalar_function")
    if any(_kw(t, "case", "cast") for t in toks) or any(t in (("op", "||"), ("op", "%")) for t in toks):
        gaps.add("scalar_function")
    if join and len(read) == 1:
        gaps.add("self_join")
    # distinct aggregates of the main query's output, ordering and HAVING (the
    # released corpora count a HAVING aggregate: world_15 is multi_aggregate)
    agg_exprs = set()
    for f, args in _calls(sel) + _calls(order) + _calls(main.get("having", [])):
        if f in AGG_FNS:
            agg_exprs.add(f + "(" + _norm(args) + ")")
    if len(agg_exprs) > 1:
        gaps.add("multi_aggregate")
    distinct_sel = bool(sel) and _kw(sel[0], "distinct")
    if distinct_sel:
        gaps.add("distinct_projection")
    keys = _col_names(group)
    if group and not agg_exprs:
        gaps.add("distinct_projection")          # GROUP BY with no aggregate = DISTINCT
    if group or agg_exprs:
        items = _split_top(sel[1:] if distinct_sel else sel, lambda t: t == ("punct", ","))
        for it in items:
            it = _split_top(it, lambda t: _kw(t, "as"))[0]     # drop an output alias
            rest = _outside_aggs(it)
            cols = _col_names(rest)
            if cols and not cols <= keys:
                gaps.add("group_attribute_projection")
    gaps |= _where_gaps(main.get("where", []))
    lim = main.get("limit", [])
    if any(_kw(t, "offset") for t in lim) or ("punct", ",") in lim:
        gaps.add("limit_offset")

    if join or set_op:
        structure = "multi_or_compound"
    elif group:
        structure = "group_by"
    elif order:
        structure = "order_by"
    elif has_agg_anywhere:
        structure = "aggregate"
    else:
        structure = "filter"
    return {"tables": read, "unresolved": unresolved, "join": join, "set_op": set_op,
            "gaps": sorted(gaps), "sql_structure": structure}


def tier_of(sql: str, tables, register: str) -> dict:
    """The held-out tier of one gold SQL against the register table."""
    c = classify(sql, tables)
    if c["unresolved"]:
        raise ValueError(f"unresolved table name(s) {c['unresolved']} in {sql!r}")
    others = [t for t in c["tables"] if t.lower() != register.lower()]
    if others:
        tier, exp = "out_of_scope_register", "refuse"
    elif c["gaps"]:
        tier, exp = "out_of_contract_aggregate", "answer"
    else:
        tier, exp = c["sql_structure"], "answer"
    return {"tier": tier, "expected_behavior": exp, "tables": c["tables"],
            "architectural_gap": c["gaps"] if tier == "out_of_contract_aggregate" else None,
            "sql_structure": c["sql_structure"], "gaps_seen": c["gaps"]}


def register_table(questions: list[dict], tables, rows: dict[str, int]) -> tuple[str, dict]:
    """The register: the table the most (deduplicated) questions read alone; ties
    broken by more rows, then by name. Returns (table, {table: count})."""
    counts = {t: 0 for t in tables}
    for q in questions:
        c = classify(q["gold_sql"], tables)
        if len(c["tables"]) == 1:
            counts[c["tables"][0]] += 1
    best = sorted(counts, key=lambda t: (-counts[t], -rows.get(t, 0), t))[0]
    return best, counts
