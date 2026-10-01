#!/usr/bin/env python
"""Declared-columns pilot — does a cheap post-hoc check recover GDC's silent-error
advantage on the text-to-SQL baseline?

STANDALONE. Touches nothing: no harness file is modified, no results_*.jsonl is
read for writing or written to. Output goes to its own directory (default
declared_columns_pilot/). The baseline adapter is REUSED, not reimplemented —
we subclass evidence.eval_run.adapters.text_to_sql.TextToSQLAdapter and append one
instruction to its existing system prompt, so the rest of the prompt stays
byte-identical to the recorded run.

WHAT IT DOES, per case:
  1. Re-run TextToSQLAdapter with an appended instruction: after emitting SQL,
     also emit a JSON list of expected output column names, drawn from the
     domain's schema['columns'].
  2. Execute the SQL against the same in-memory sqlite mirror the baseline uses
     (evidence.eval_run.tools_sql.run_sql).
  3. declared vs ACTUAL returned columns   -> is the model self-consistent?
  4. declared vs GOLD columns              -> is the declaration trustworthy at
     all, independent of what the SQL happened to return?

GOLD COLUMNS. These are out_of_scope_register cases, so the gold *answer* is a
refusal and carries no column list. The gold columns are therefore derived from
the corpus's `gold_sql` (the verbatim Spider reference query) by executing it
against the original Spider database and reading the cursor description. Falls
back to parsing the SELECT list when the database is unavailable; the method used
is recorded per case.

COST. --live makes exactly one API call per case (12 by default). Without --live
the script is a full dry run: it renders the exact prompts, resolves gold columns
from the Spider databases, validates every case, and makes NO API call.

    python scripts/pilot_declared_columns.py              # dry run, free
    python scripts/pilot_declared_columns.py --live       # 12 paid calls

PREREQUISITE: pyarrow. The three Spider registers (wta_1/matches.pkl,
world_1/country.pkl, bike_1/trip.pkl) are pickled with Arrow-backed dtypes and
cannot be unpickled without it. The script checks this up front and exits with
the install command rather than half-running.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════════════════════════
# Sample — 12 distinct (domain, qid) pairs, pooled from the s40off ∪ s40on sets
# ══════════════════════════════════════════════════════════════════════════════
# Every qid below was identified as a case where GDC scores `correct` on the
# out_of_scope_register tier while text_to_sql scores silent_wrong. The 11
# pre-identified ones, plus wta_21 as the 12th.
#
# wta_21 AND world_12 both qualify as missing-table shape (wta_21 -> `rankings`,
# world_12 -> `countrylanguage`; neither table is in its domain's register).
# wta_21 is taken because it balances the domains 5/5/2 rather than 4/6/2;
# world_12 is listed as a spare and is NOT run unless --swap-spare is passed.
# validate_cases() re-derives the missing-table property from the corpus at
# runtime and aborts on any case that does not hold it — the count is never
# padded with a case that doesn't earn its place.
CASES: list[tuple[str, str]] = [
    ("wta_1",   "wta_01"),
    ("wta_1",   "wta_11"),
    ("wta_1",   "wta_21"),      # 12th — see note above
    ("wta_1",   "wta_26"),
    ("wta_1",   "wta_28"),
    ("world_1", "world_07"),
    ("world_1", "world_25"),
    ("world_1", "world_26"),
    ("world_1", "world_27"),
    ("world_1", "world_30"),
    ("bike_1",  "bike_18"),
    ("bike_1",  "bike_19"),
]
SPARE: tuple[str, str] = ("world_1", "world_12")

DOMAINS = {
    "wta_1":   {"register": ROOT / "evaluation/domains/spider_wta_1/matches.pkl",   "schema": ROOT / "evaluation/domains/spider_wta_1/schema.json",
                "corpus": ROOT / "evaluation/domains/spider_wta_1/eval_corpus_spider_wta_1.json",
                "spider_db": ROOT / "spider/wta_1.sqlite"},
    "world_1": {"register": ROOT / "evaluation/domains/spider_world_1/country.pkl", "schema": ROOT / "evaluation/domains/spider_world_1/schema.json",
                "corpus": ROOT / "evaluation/domains/spider_world_1/eval_corpus_spider_world_1.json",
                "spider_db": ROOT / "spider/world_1.sqlite"},
    "bike_1":  {"register": ROOT / "evaluation/domains/spider_bike_1/trip.pkl",     "schema": ROOT / "evaluation/domains/spider_bike_1/schema.json",
                "corpus": ROOT / "evaluation/domains/spider_bike_1/eval_corpus_spider_bike_1.json",
                "spider_db": ROOT / "database/bike_1/bike_1.sqlite"},
}

# Recorded baseline model (results_spider_*.provenance.json). Kept as the default
# so the pilot differs from the recorded run in exactly ONE dimension — the
# appended declaration instruction — rather than in model as well.
BASELINE_MODEL = "claude-sonnet-4-5"

# The recorded out-of-scope baseline rows ran at the API-default temperature
# (provenance: temperature=null); the temp-0 parity overlay covers in-contract
# cells only. The pilot runs at temperature 0 as specified, so the "original
# baseline verdict" column and the pilot's own emission differ in temperature as
# well as in the appended instruction. Flagged in the output header.
PILOT_TEMPERATURE = 0.0
PILOT_SEED = 0


# ══════════════════════════════════════════════════════════════════════════════
# The one appended instruction
# ══════════════════════════════════════════════════════════════════════════════
DECLARATION_SUFFIX = """

ADDITIONAL REQUIREMENT (overrides "No commentary outside the tool call" above):
Alongside the run_sql tool call, emit ONE text block containing a JSON object
declaring the columns you expect your result to have:

  {{"expected_columns": ["ColA", "ColB"]}}

Rules:
  - Every name MUST be drawn verbatim from this table's column list:
    {allowed}
  - List them in the order your SELECT produces them.
  - Emit the JSON object and nothing else in the text block. Still emit the SQL
    via the run_sql tool call.
"""


def build_pilot_adapter_class():
    """Subclass the real baseline adapter; append the instruction to its prompt.

    Imported lazily so --help and the pyarrow preflight work without pandas
    unpickling anything.
    """
    from evidence.eval_run.adapters.text_to_sql import TextToSQLAdapter

    class DeclaringTextToSQLAdapter(TextToSQLAdapter):
        name = "text_to_sql_declared"

        def _build_system(self) -> str:
            base = super()._build_system()
            allowed = ", ".join(repr(c) for c in self.briefing["columns"])
            return base + DECLARATION_SUFFIX.format(allowed=allowed)

    return DeclaringTextToSQLAdapter


# ══════════════════════════════════════════════════════════════════════════════
# Column-name normalisation + comparison
# ══════════════════════════════════════════════════════════════════════════════
_AGG = re.compile(r"^\s*(?:count|sum|avg|min|max|distinct)\s*\((.*)\)\s*$", re.I)


def norm_strict(name: str) -> str:
    """Casefold + strip surrounding quotes/whitespace. Nothing else."""
    return str(name).strip().strip('"').strip("'").strip("`").strip().casefold()


def norm_loose(name: str) -> str:
    """norm_strict + unwrap one aggregate call and drop a table qualifier.

    `sum(tours)` -> `tours`; `T1.name` -> `name`; `count(*)` -> `*`. Used only for
    the secondary `loose` comparison so an aggregate wrapper or an alias does not
    masquerade as a genuine column-identity mismatch.
    """
    s = norm_strict(name)
    m = _AGG.match(s)
    if m:
        s = m.group(1).strip()
    if "." in s:
        s = s.rsplit(".", 1)[1]
    return s.strip()


def compare(a: list[str], b: list[str]) -> dict:
    """Set comparison under both normalisations. Order-insensitive by design —
    a declared list that names the right columns in the wrong order is not the
    failure mode this pilot is looking for."""
    sa, sb = {norm_strict(x) for x in a}, {norm_strict(x) for x in b}
    la, lb = {norm_loose(x) for x in a}, {norm_loose(x) for x in b}
    return {
        "strict_equal": sa == sb,
        "loose_equal": la == lb,
        "missing": sorted(sb - sa),   # in b, not declared in a
        "extra": sorted(sa - sb),     # declared in a, not in b
    }


# ══════════════════════════════════════════════════════════════════════════════
# Gold columns from the reference SQL
# ══════════════════════════════════════════════════════════════════════════════
def gold_columns_from_db(gold_sql: str, db_path: Path) -> tuple[list[str] | None, str]:
    """Execute the Spider reference SQL against the original Spider database and
    read the cursor description. Authoritative when the db is present."""
    if not db_path.exists():
        return None, f"db missing: {db_path.name}"
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            cur = conn.execute(gold_sql)
            cols = [d[0] for d in (cur.description or [])]
        return (cols, "executed against spider db") if cols else (None, "no cursor description")
    except Exception as exc:                                  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


_SELECT = re.compile(r"^\s*select\s+(?:distinct\s+)?(.*?)\s+from\s", re.I | re.S)


def gold_columns_from_sql_text(gold_sql: str) -> tuple[list[str] | None, str]:
    """Fallback: parse the SELECT list. Splits on top-level commas only, so
    `count(a, b)` is not torn in half."""
    m = _SELECT.match(gold_sql)
    if not m:
        return None, "SELECT list not parseable"
    body, out, depth, cur = m.group(1), [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return ([c for c in out if c], "parsed from gold_sql SELECT list") if out else (None, "empty SELECT list")


def resolve_gold_columns(gold_sql: str, db_path: Path) -> tuple[list[str], str]:
    cols, how = gold_columns_from_db(gold_sql, db_path)
    if cols is not None:
        return cols, how
    fallback, how2 = gold_columns_from_sql_text(gold_sql)
    return (fallback or []), f"{how2} (db path unusable: {how})"


# ══════════════════════════════════════════════════════════════════════════════
# Case validation — never pad the count
# ══════════════════════════════════════════════════════════════════════════════
def validate_cases(cases, corpora, schemas) -> list[dict]:
    """Assert the tier, then CLASSIFY each case by sub-shape.

    `out_of_scope_register` bundles two structurally different violations, and the
    declared-columns check has different odds against each:

      missing_table  the gold query targets table(s) the register does not expose
                     at all, so its gold columns cannot appear in the register's
                     column list. A declared-columns check has real signal here:
                     the model is forced to declare register columns it cannot
                     actually deliver.

      join_or_set_op the register table IS among the targets, but the query needs
                     a JOIN or a set operation (INTERSECT) the 4-operator algebra
                     lacks. Gold columns here are often register columns
                     (world_26 -> `Region`, wta_26 -> `winner_name`), so a
                     declared-columns check can come out clean on a query that is
                     nonetheless unanswerable. These are the expected negatives.

    Aborts only on a genuinely unusable case (unknown qid, wrong tier, no recorded
    target tables). Shape is reported, not enforced — stratify the results by it.
    """
    rows, bad = [], []
    for dom, qid in cases:
        q = corpora[dom].get(qid)
        if q is None:
            bad.append(f"{dom}/{qid}: not in corpus")
            continue
        if q.get("tier") != "out_of_scope_register":
            bad.append(f"{dom}/{qid}: tier={q.get('tier')!r}, expected out_of_scope_register")
            continue
        tables = list(q.get("tables") or [])
        if not tables:
            bad.append(f"{dom}/{qid}: corpus records no target tables")
            continue
        reg_table = (schemas[dom].get("domain") or {}).get("register_table")
        rows.append({"domain": dom, "qid": qid, "question": q.get("question"),
                     "gold_sql": q.get("gold_sql"), "target_tables": tables,
                     "register_table": reg_table,
                     "shape": "missing_table" if reg_table not in tables else "join_or_set_op",
                     "corpus_reason": q.get("reason")})
    if bad:
        raise SystemExit("CASE VALIDATION FAILED — refusing to run:\n  " + "\n  ".join(bad))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Declaration extraction
# ══════════════════════════════════════════════════════════════════════════════
def extract_declared(text: str) -> tuple[list[str] | None, str]:
    """Pull {"expected_columns": [...]} out of the model's text block."""
    if not text or not text.strip():
        return None, "no text block emitted"
    for m in re.finditer(r"\{.*?\}", text, re.S):
        try:
            obj = json.loads(m.group(0))
        except Exception:                                     # noqa: BLE001
            continue
        if isinstance(obj, dict) and isinstance(obj.get("expected_columns"), list):
            return [str(c) for c in obj["expected_columns"]], "ok"
    m = re.search(r"\[.*?\]", text, re.S)
    if m:
        try:
            arr = json.loads(m.group(0))
            if isinstance(arr, list):
                return [str(c) for c in arr], "bare JSON array (no expected_columns key)"
        except Exception:                                     # noqa: BLE001
            pass
    return None, "no parseable JSON column list in text block"


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════
def preflight() -> None:
    try:
        import pyarrow  # noqa: F401
    except ModuleNotFoundError:
        raise SystemExit(
            "ERROR: pyarrow is not installed, and all three Spider registers\n"
            "(wta_1/matches.pkl, world_1/country.pkl, bike_1/trip.pkl) are pickled with\n"
            "Arrow-backed dtypes — pandas cannot unpickle them without it.\n\n"
            "    .venv/bin/pip install pyarrow\n\n"
            "Nothing was run and nothing was written."
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true",
                    help="MAKES PAID API CALLS (1 per case). Without it: full dry run, no calls.")
    ap.add_argument("--model", default=BASELINE_MODEL,
                    help=f"default {BASELINE_MODEL} (the recorded baseline model)")
    ap.add_argument("--outdir", default=str(ROOT / "declared_columns_pilot"))
    ap.add_argument("--swap-spare", action="store_true",
                    help="run world_12 instead of wta_21 as the 12th case")
    ap.add_argument("--shape", choices=["missing_table", "join_or_set_op"],
                    help="restrict the run to one out_of_scope_register sub-shape "
                         "(default: run all 12, stratified in the report)")
    ap.add_argument("--control", action="store_true",
                    help="TEMPERATURE CONTROL ARM: same 12 cases, same temperature and "
                         "seed, but the UNMODIFIED text-to-SQL prompt (no declaration "
                         "instruction). Isolates whether the 4 refusals in the treatment "
                         "arm come from the declaration instruction or from temperature 0. "
                         "Writes pilot_control.* — never overwrites pilot_live.*")
    ap.add_argument("--dump-prompt", metavar="DOMAIN",
                    help="print the full rendered system prompt for a domain and exit")
    args = ap.parse_args()

    preflight()
    import pandas as pd
    from evidence.eval_run.briefing import load_briefing
    from evidence.eval_run.tools_sql import run_sql
    from evidence.eval_run.comparator import score, COMPARATOR_VERSION

    cases = list(CASES)
    if args.swap_spare:
        cases = [c for c in cases if c != ("wta_1", "wta_21")] + [SPARE]

    corpora = {d: {q["id"]: q for q in json.loads(v["corpus"].read_text())["queries"]}
               for d, v in DOMAINS.items()}
    schemas = {d: json.loads(v["schema"].read_text()) for d, v in DOMAINS.items()}
    golds = {d: json.loads((ROOT / "evidence/gold" / f"gold_spider_{d}.json").read_text())
             for d in DOMAINS}
    meta = validate_cases(cases, corpora, schemas)
    if args.shape:
        meta = [m for m in meta if m["shape"] == args.shape]
    n_mt = sum(1 for m in meta if m["shape"] == "missing_table")
    n_js = len(meta) - n_mt
    print(f"[validate] {len(meta)} cases, all tier=out_of_scope_register")
    print(f"[validate]   missing_table  : {n_mt:>2}  "
          f"({', '.join(m['qid'] for m in meta if m['shape'] == 'missing_table')})")
    print(f"[validate]   join_or_set_op : {n_js:>2}  "
          f"({', '.join(m['qid'] for m in meta if m['shape'] == 'join_or_set_op')})")
    if n_js:
        print("[validate]   NOTE: join_or_set_op gold columns are often register columns, "
              "so a declared-columns\n"
              "[validate]         check can come out clean on an unanswerable query. "
              "Expected negatives — stratify.")

    # ── build one adapter per domain ─────────────────────────────────────────
    # Control arm uses the STOCK adapter; treatment arm uses the declaring subclass.
    from evidence.eval_run.adapters.text_to_sql import TextToSQLAdapter
    AdapterCls = TextToSQLAdapter if args.control else build_pilot_adapter_class()
    adapter_name = "text_to_sql" if args.control else "text_to_sql_declared"
    client = None
    if args.live:
        from anthropic import Anthropic
        client = Anthropic()

    adapters, registers = {}, {}
    for dom in sorted({d for d, _ in cases}):
        reg = pd.read_pickle(DOMAINS[dom]["register"])
        briefing = load_briefing(DOMAINS[dom]["schema"], DOMAINS[dom]["register"])
        schema_cols = list((schemas[dom].get("columns") or {}).keys())
        if set(schema_cols) != set(briefing["columns"]):
            print(f"[warn] {dom}: schema['columns'] != register columns "
                  f"(schema-only={sorted(set(schema_cols) - set(briefing['columns']))}, "
                  f"register-only={sorted(set(briefing['columns']) - set(schema_cols))}) — "
                  f"the declaration allow-list uses the register columns the adapter sees")
        registers[dom] = reg
        adapters[dom] = AdapterCls(name=adapter_name, client=client, register=reg,
                                   briefing=briefing, model=args.model,
                                   temperature=PILOT_TEMPERATURE, timeout_s=60)

    # ── arm wiring check ─────────────────────────────────────────────────────
    # The control arm's prompt must be byte-identical to the recorded baseline's,
    # and the treatment arm's must be that prompt plus the appended block and
    # nothing else. Verified here rather than assumed — a control that silently
    # carried the declaration instruction would invalidate the whole comparison.
    deltas = {}
    for dom, ad in adapters.items():
        stock = TextToSQLAdapter(name="text_to_sql", client=None, register=registers[dom],
                                 briefing=ad.briefing, model=args.model,
                                 temperature=PILOT_TEMPERATURE, timeout_s=60).system_text
        if args.control:
            assert ad.system_text == stock, f"{dom}: control prompt is not the stock prompt"
            assert "ADDITIONAL REQUIREMENT" not in ad.system_text, \
                f"{dom}: control prompt still carries the declaration instruction"
        else:
            assert ad.system_text.startswith(stock), f"{dom}: treatment prompt is not stock + suffix"
            assert "ADDITIONAL REQUIREMENT" in ad.system_text, f"{dom}: suffix missing"
        deltas[dom] = len(ad.system_text) - len(stock)
    arm = "CONTROL (unmodified prompt)" if args.control else "TREATMENT (declaration appended)"
    print(f"[arm] {arm} — adapter={AdapterCls.__name__}")
    print(f"[arm] prompt delta vs recorded baseline prompt, per domain: "
          + ", ".join(f"{d}=+{n}" for d, n in sorted(deltas.items())) + " chars")

    if args.dump_prompt:
        print(adapters[args.dump_prompt].system_text)
        return

    if not args.live:
        print(f"\n[DRY RUN] no API call made. --live would make {len(cases)} calls "
              f"on {args.model} (~1.2k in / ~0.2k out tokens each).\n"
              f"Gold columns and prompts are still resolved below so the setup can be "
              f"checked before spending anything.\n")

    # ── run ──────────────────────────────────────────────────────────────────
    results = []
    for m in meta:
        dom, qid = m["domain"], m["qid"]
        gold_cols, gold_how = resolve_gold_columns(m["gold_sql"], DOMAINS[dom]["spider_db"])
        rec = {**m, "arm": "control" if args.control else "treatment",
               "model": args.model, "temperature": PILOT_TEMPERATURE, "seed": PILOT_SEED,
               "gold_columns": gold_cols, "gold_columns_source": gold_how,
               "sql": None, "declared_columns": None, "declared_parse": None,
               "actual_columns": None, "exec_error": None, "raw_text": None,
               "status": "dry_run", "vs_actual": None, "vs_gold": None,
               "actual_vs_gold": None, "outcome_class": None, "comparator_reason": None}

        if args.live:
            ad = adapters[dom]
            out = ad.run(m["question"], PILOT_SEED)
            art = out.get("artifact") or {}
            rec["status"] = out.get("status") or "completed"
            rec["raw_text"] = art.get("raw_text")
            rec["sql"] = art.get("sql")
            rec["llm_calls"] = out.get("llm_calls")
            rec["input_tokens"] = out.get("input_tokens")
            rec["output_tokens"] = out.get("output_tokens")

            # Declaration only exists in the treatment arm.
            if not args.control:
                declared, how = extract_declared(art.get("raw_text") or "")
                rec["declared_columns"], rec["declared_parse"] = declared, how

            if rec["sql"]:
                _, frame, err = run_sql(rec["sql"], registers[dom], timeout_s=60)
                rec["exec_error"] = err
                if err is None and frame is not None:
                    rec["actual_columns"] = [str(c) for c in frame.columns]

            if not args.control:
                if rec["declared_columns"] is not None and rec["actual_columns"] is not None:
                    rec["vs_actual"] = compare(rec["declared_columns"], rec["actual_columns"])
                if rec["declared_columns"] is not None and gold_cols:
                    rec["vs_gold"] = compare(rec["declared_columns"], gold_cols)

            # Returned columns vs gold columns — the only column comparison
            # available in the control arm (there is no declaration to check).
            if rec["actual_columns"] is not None and gold_cols:
                rec["actual_vs_gold"] = compare(rec["actual_columns"], gold_cols)

            # The repo's own scorer — the definitive "does this match gold".
            # On out_of_scope_register gold carries expected_behavior="refuse",
            # so refused -> correct and a confident answer -> silent_wrong. This
            # is what makes the two arms directly comparable.
            g = golds[dom].get(qid)
            if g is not None:
                cls, reason, _ = score(out.get("result"), g,
                                       status=out.get("status", "completed"))
                rec["outcome_class"], rec["comparator_reason"] = cls, reason

        results.append(rec)
        if not args.live:
            print(f"  [{qid:<9}] gold={gold_cols}  ({gold_how})")
        elif args.control:
            print(f"  [{qid:<9}] {rec['status']:<9} outcome={rec['outcome_class']}"
                  f"  actual={rec['actual_columns']}  gold={gold_cols}")
        else:
            print(f"  [{qid:<9}] {rec['status']:<9} outcome={rec['outcome_class']}"
                  f"  declared={rec['declared_columns']}  actual={rec['actual_columns']}")

    # ── write ────────────────────────────────────────────────────────────────
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if args.control:
        tag = "control" if args.live else "control_dryrun"
    else:
        tag = "live" if args.live else "dryrun"
    jsonl = outdir / f"pilot_{tag}.jsonl"
    with jsonl.open("w") as fh:
        for r in results:
            fh.write(json.dumps(r) + "\n")

    md = outdir / f"pilot_{tag}.md"
    md.write_text(render_table(results, args))
    print(f"\nwrote {jsonl}\nwrote {md}")
    if args.live:
        print("\n" + render_table(results, args))


def render_table(results: list[dict], args) -> str:
    from evidence.eval_run.comparator import COMPARATOR_VERSION

    def fmt(v):
        if v is None:
            return "—"
        if isinstance(v, list):
            return "`" + ", ".join(str(x) for x in v) + "`" if v else "`(none)`"
        return str(v)

    def flag(cmp_):
        return "—" if cmp_ is None else ("**True**" if cmp_["strict_equal"] else "False")

    control = getattr(args, "control", False)
    title = ("# Declared-columns pilot — CONTROL ARM (unmodified prompt)" if control
             else "# Declared-columns pilot — text-to-SQL baseline")
    lines = [
        title,
        "",
        f"- model: `{args.model}`  temperature: `{PILOT_TEMPERATURE}`  seed: `{PILOT_SEED}`  "
        f"(single seed — pilot, not a scored run)",
        f"- mode: **{'LIVE (paid API calls)' if args.live else 'DRY RUN (no API calls)'}**",
        ("- prompt: the recorded `SQL_SYSTEM` prompt **verbatim and unmodified** — "
         "no declaration instruction. Asserted byte-identical to the stock "
         "`TextToSQLAdapter` prompt at run time."
         if control else
         "- prompt: the recorded `SQL_SYSTEM` prompt verbatim + one appended "
         "declaration instruction"),
        ("- PURPOSE: isolates temperature from the declaration instruction. The "
         "treatment arm (`pilot_live.*`) changed two things at once versus the "
         "recorded baseline — temperature 0 AND the appended instruction. This arm "
         "changes only temperature, so any refusals here are attributable to "
         "temperature alone."
         if control else
         "- CAVEAT: the recorded out-of-scope baseline rows ran at the API-default "
         "temperature, not 0 — the pilot differs from them in temperature as well as "
         "in the appended instruction. See `pilot_control.*` for the arm that "
         "separates them."),
        "- `outcome_class` is from the repo's own comparator "
        f"(`evidence.eval_run.comparator.score`, {COMPARATOR_VERSION}). On this tier "
        "gold carries `expected_behavior=\"refuse\"`, so **refused → `correct`** and a "
        "confident answer → `silent_wrong`.",
        "",
    ]
    if control:
        lines += [
            "| qid | shape | recorded baseline | this arm | SQL emitted | "
            "actual columns | gold columns | actual==gold |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in sorted(results, key=lambda x: (x["shape"], x["qid"])):
            sql = (r["sql"] or "").replace("\n", " ").strip()
            sql = (sql[:90] + "…") if len(sql) > 90 else (sql or "—")
            lines.append(
                f"| `{r['qid']}` | {r['shape']} | silent_wrong | "
                f"`{r['outcome_class'] or r['status']}` | "
                f"{'`' + sql + '`' if sql != '—' else '—'} | "
                f"{fmt(r['actual_columns'])} | {fmt(r['gold_columns'])} | "
                f"{flag(r['actual_vs_gold'])} |"
            )
    else:
        lines += [
            "| qid | shape | recorded baseline | this arm | SQL emitted | "
            "declared columns | actual columns | gold columns | decl==actual | decl==gold |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in sorted(results, key=lambda x: (x["shape"], x["qid"])):
            sql = (r["sql"] or "").replace("\n", " ").strip()
            sql = (sql[:90] + "…") if len(sql) > 90 else (sql or "—")
            lines.append(
                f"| `{r['qid']}` | {r['shape']} | silent_wrong | "
                f"`{r['outcome_class'] or r['status']}` | "
                f"{'`' + sql + '`' if sql != '—' else '—'} | "
                f"{fmt(r['declared_columns'])} | {fmt(r['actual_columns'])} | "
                f"{fmt(r['gold_columns'])} | {flag(r['vs_actual'])} | {flag(r['vs_gold'])} |"
            )
    # ── stratified summary ───────────────────────────────────────────────────
    if any(r.get("outcome_class") for r in results):
        lines += ["", "## Stratified summary", "",
                  "| shape | n | refused (`correct`) | answered (`silent_wrong`) |"]
        lines.append("|---|---|---|---|")
        for shape in ("missing_table", "join_or_set_op"):
            g = [r for r in results if r["shape"] == shape]
            if not g:
                continue
            ref = [r for r in g if r["outcome_class"] == "correct"]
            lines.append(f"| {shape} | {len(g)} | {len(ref)}/{len(g)} "
                         f"({', '.join(r['qid'] for r in ref) or '—'}) | "
                         f"{len(g) - len(ref)}/{len(g)} |")
        allref = [r for r in results if r["outcome_class"] == "correct"]
        lines.append(f"| **pooled** | {len(results)} | {len(allref)}/{len(results)} | "
                     f"{len(results) - len(allref)}/{len(results)} |")

    lines += [
        "",
        "`recorded baseline` is the recorded out_of_scope_register outcome for "
        "`text_to_sql` on this qid (comparator s47, API-default temperature): every "
        "case in the sample scored `silent_wrong` — it answered a question the "
        "register cannot support. `this arm` is what the same case did in this run.",
        "",
        "`shape` splits the tier into its two structurally different violations. "
        "`missing_table` cases target a table the register does not expose, so gold "
        "columns cannot be register columns and the declared-columns check has real "
        "signal. `join_or_set_op` cases need a JOIN or INTERSECT the 4-operator "
        "algebra lacks; their gold columns are often register columns, so the check "
        "can come out clean on an unanswerable query. **Read the two strata "
        "separately — pooling them understates the check on one and overstates it on "
        "the other.**",
        "",
        "## Gold-column provenance and case shape",
        "",
        "| qid | shape | gold_sql | target tables | register table | gold-column source |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(results, key=lambda x: (x["shape"], x["qid"])):
        lines.append(
            f"| `{r['qid']}` | {r['shape']} | `{r['gold_sql']}` | "
            f"`{', '.join(r['target_tables'])}` | "
            f"`{r['register_table']}` | {r['gold_columns_source']} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
