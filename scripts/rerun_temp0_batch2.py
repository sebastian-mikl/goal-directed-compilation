#!/usr/bin/env python
"""RELEASE NOTE. This is the development tree's file with two edits so that it imports
without the withheld risk domain: RISK_RERUN is a literal here (the development tree
imports RISK_RERUN_S31 from scripts/parity_overlay.py) and POST_MERGE reads
DOMAIN_SOURCES with .get("risk", ()). Nothing else differs. --dry and --run cannot run in
this repository: the plan is built from an audit manifest (docs/audit/work/manifest/) that
covers the withheld risk domain and is not shipped. heldout/run_heldout.py imports its
helpers (SubstitutingClient, fixed_code, ...) from here, which is why the file is shipped;
the files it produced are results/results_temp0_batch2_*. The text below is unchanged.

Batch 2: the one paid temperature-0 re-run, under the RECORDED prompt of each cell,
on the fixed engine and the fixed code-agent harness (decision 5, 1 Oct).

Which cells
-----------
The plan is built from the audit's cell manifest (docs/audit/work/manifest/
cell_manifest.csv: one row per reported cell, with its source results file and line,
its provenance file and its recorded prompt hash), read after the 1 Oct merge: a risk
cell whose key the temperature-0 re-run holds (results_temp0_rerun_s31_risk.jsonl)
takes that row as its source. One cell per (system, domain, qid, seed), carrying every
reason that selected it:

  i    every reported code_agent cell. The harness is fixed: the labelled index is kept,
       one namespace lasts the whole episode, the tool description states the actual
       frame, and the trace keeps the full code.
  ii   react and text_to_sql cells still at the API-default temperature on the Table 5
       tiers (Spider out_of_scope_register, employee out_of_range).
  iii  react cells whose recorded tool calls give different outputs on the fixed engine.
       The list (--react-affected, required) comes from re-executing every recorded react
       step on both engines. Only its key columns are read (domain, source_file, line_no,
       qid, seed, kind_of_difference); keep it outside the repository, since its other
       columns may quote register content.
  iv   temperature-0 baseline cells whose row is a copy of a default-temperature emission
       (audit item F). They are found here by fingerprint (system, qid, wall time, token
       counts), not read from a list.
  v    the alias arm (audit item C). For each system that sees only the shared briefing and
       whose recorded prompt lacks the domain's business-rule aliases, every reported cell
       whose question uses an alias phrase, re-run with one line per alias added after its
       rule in the recorded prompt. This is a separate arm with its own output file: these
       cells test the confound and replace no reported cell.
  vi   every reported text_to_sql cell, so that all of them run on one SQLite. The venv's
       SQLite has MEDIAN and PERCENTILE built in; the recorded runs did not record their
       SQLite version, and the one recorded MEDIAN query failed with 'no such function'.
       text_to_sql records a SQL error as a refusal, so the SQLite build alone can move a
       cell between a decline and an answer.

The alias line (v). GDC's recorded prompt (planner.llm._rules_block) states the rule as
'When the request refers to "current" or "active employees" rows, filter status to one
of those values'; its aliases are multi-word trigger phrases. The baselines'
briefing states each rule as `'<term>': <definition>`; the inserted line gives the alias
the rule's own definition in that format, e.g. `  - 'active employees': 'Status' in
[...]`. A question is in the arm when it uses the alias phrase itself: 'active
Directors' does not, so that question is not in the arm, although GDC's model applied
the rule to it.

What is sent
------------
Each system prompt is REPLACED, byte for byte, by the text recorded for that system in
the provenance of the cell's source file, as in rerun_temp0_risk_recorded_prompts.py.
One explicit exception, PROMPT_OVERRIDES (audit item D): every risk code_agent cell is
sent the June recorded code_agent prompt, including the cells whose source row was
recorded under the 23 Aug prompt, which carries a rule the June prompt lacks. The plan
row and the provenance record the override (`prompt_overrides`).
Cells run in groups that share one recorded prompt. Each group's client sends that text
and nothing else: an unrecognised prompt, a temperature other than 0 or another model is
refused before anything is sent. Tools, engine and harness are the current code, and
--run refuses to start unless the engine and code-agent fixes are present. Model
claude-sonnet-4-5, prompt caching on, react 15 iterations, code_agent 12 retries (13
calls), 60 s per query. The 60 s bounds text_to_sql's SQL only; the code_agent exec has
no wall-clock bound.

Output: results_temp0_batch2_<domain>.jsonl and .provenance.json per domain, and
results_temp0_batch2_<domain>_alias.jsonl for the alias arm. Each provenance has a
`batch2` block: the recorded-prompt hashes actually sent (per call and per cell), the
git commit, the fixed-code check, the engine files' hashes (parity_overlay.
engine_hashes()) and the SQLite, the keys of every cell planned for the file
(`plan_keys`), the prompt overrides applied, how the SQLite differs from the recorded
runs, and the code_agent tool-description choice. The harness's own `repetitions` field
holds only the seeds of the first batch; the per-cell map is the record. This script
merges nothing: under PARITY_TEMP0=1 the builder reads the per-domain files through
parity_overlay.RERUN_MAP, where each supersedes its domain's parity file and, for risk,
the 1 Oct re-run, and takes a file only with exactly its plan_keys, no row the run did
not complete, and the engine hashes of the build's own code. The alias file replaces no
reported cell and the builder does not read it.

Spend: hard cap (--cap). Runs one (system, qid) batch at a time and stops BEFORE a batch
whose worst case would take the spend over the cap. The spend is the billed ledger
(results_temp0_batch2.spend.json), not the logged rows: every response's usage is
billed as it arrives, so a cell interrupted by a transport error counts, and a batch's
worst case is reserved before it starts and stays counted if the process dies. The
worst case is an estimate (the call bound times the dearest recorded call of that system
and domain); a batch that bills more than its estimate stops the run, so the cap can be
passed by at most that one batch's excess. Resumable. Halts on the first guard refusal
or API/credit error.

Withheld outputs (risk cell keys or risk data; never committed or pushed):
results_temp0_batch2.cell_plan.csv (the plan, every domain), results_temp0_batch2_risk.jsonl
and .provenance.json. The spend ledger holds dollar totals only; .gitignore covers every
results_temp0_batch2* name.

Usage:
    python scripts/rerun_temp0_batch2.py --dry --react-affected PATH [--plan-out CSV]
                                          # plan, cost, and a no-API preflight of every cell
    python scripts/rerun_temp0_batch2.py --run --react-affected PATH [--cap 10]
                                          # also writes results_temp0_batch2.cell_plan.csv
    (--run needs ANTHROPIC_API_KEY, e.g. in .env at the repo root, which is git-ignored)
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, subprocess, sys, tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import evidence.eval_run.harness as H                                   # noqa: E402
from evidence.eval_run.pricing import cost_usd, cost_usd_cached          # noqa: E402
from evidence.eval_run.logger import sqlite_info                        # noqa: E402
from scripts.parity_overlay import (DOMAIN_SOURCES, GUARD,                # noqa: E402
                                    batch2_file, engine_hashes)
from scripts.probe_temperature import DOMAINS, MODEL                     # noqa: E402

TEMP = 0
BOUNDS = {"react_max_iters": 15, "code_max_retries": 12, "per_query_timeout_s": 60}
CALLS = {"planner": 1, "react": 15, "code_agent": 13, "text_to_sql": 1}   # calls per cell at the bounds
BASELINES = ("react", "code_agent", "text_to_sql")
BRIEFED = ("planner",) + BASELINES               # the systems that see only the shared briefing
RECORDED, ALIAS = "recorded", "recorded+alias"   # the two arms
MANIFEST = Path("docs", "audit", "work", "manifest", "cell_manifest.csv")
RECOVERED_PROV = MANIFEST.parent / "recovered_prov"   # provenance of files deleted in 6795e25
# Decision 4: the 1 Oct risk re-run replaces the rows it holds in risk's recorded files
# (parity_overlay's RERUN_MAP); the Haiku panel keeps the June emissions.
RISK_RERUN = "results_temp0_rerun_s31_risk.jsonl"
POST_MERGE = {"risk": set(DOMAIN_SOURCES.get("risk", ()))}
NO_OVERLAY_SETS = {"haiku_sonnet", "haiku_haiku"}
TABLE5_TIERS = {"employee": "out_of_range", "wta_1": "out_of_scope_register",
                "world_1": "out_of_scope_register", "bike_1": "out_of_scope_register"}
ALIAS_DOMAINS = ("employee",)
PLAN_FIELDS = ["arm", "domain", "system", "qid", "seed", "reasons", "detail", "source_file",
               "line_no", "provenance_file", "recorded_temperature", "recorded_code_max_retries",
               "recorded_status", "recorded_verdict", "recorded_calls", "recorded_cost_usd",
               "recorded_prompt_sha256", "sent_prompt_sha256", "briefing_sha256",
               "prompt_override", "query_matches_recorded", "report_sets", "realistic_usd",
               "worst_usd", "out_file"]
SPEND_LEDGER = "results_temp0_batch2.spend.json"
# Audit item D: one explicit, recorded exception to "send the recorded prompt". Every
# risk code_agent cell gets the June recorded code_agent prompt; the cells recorded under
# the 23 Aug prompt (a rule added after June) are sent the June text instead.
PROMPT_OVERRIDES = [{
    "id": "audit-D",
    "domain": "risk", "system": "code_agent",
    "recorded_sha256": "6ce96668b92518daef4d0f9aca3e9e3908aca5c2101f4d13ff64cdceb5849962",
    "send_from": "evidence/prompts_recorded/risk_prompts_2026-06-08.json",
    "send_sha256": "43aa769776e1a334a81486fdf37df7c4ae973b8be1c87ab84d33a72761bd7d20",
    "why": ("every risk code_agent cell runs under the June recorded prompt; the 23 Aug "
            "prompt adds a rule the June prompt and the other systems' prompts lack"),
}]
# Decision 3(c), as taken: the run_python description states the register's row count and
# calls the frame "the register", as the recorded prompts do; the frame's name is not
# added. Recorded in each batch2 provenance.
CODE_AGENT_TOOL_TEXT = {
    "choice": "row count only, called the register (the frame's table name is not stated)",
    "kept_sentence": "Variables persist across run_python and final_answer calls for this query.",
}
SQLITE_VS_RECORDED = ("The recorded runs did not record their SQLite version. Their one MEDIAN "
                      "query failed with 'no such function', so they ran without MEDIAN. This "
                      "batch's SQLite is in `sqlite` (`functions` says which of MEDIAN, "
                      "PERCENTILE and CORR it has); every reported text_to_sql cell is re-run "
                      "on it (reason vi).")


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def _load(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


_LINES: dict[Path, list[str]] = {}
_PROV: dict[Path, dict] = {}


def _row(root: Path, fn: str, line_no: int) -> dict:
    p = root / fn
    if p not in _LINES:
        _LINES[p] = p.read_text().splitlines()
    return json.loads(_LINES[p][line_no - 1])


def prov_path(name: str, root: Path = ROOT) -> Path:
    """A manifest provenance_file: a file at the repo root, or `git:<rev>:<name>` for one
    deleted from the tree and recovered read-only by the audit."""
    return root / RECOVERED_PROV / name.rsplit(":", 1)[1] if name.startswith("git:") else root / name


def provenance(name: str, root: Path = ROOT) -> dict:
    p = prov_path(name, root)
    if p not in _PROV:
        _PROV[p] = json.loads(p.read_text())
    return _PROV[p]


def _sibling_prov(fn: str) -> str:
    return fn[: -len(".jsonl")] + ".provenance.json"


# ── the plan ────────────────────────────────────────────────────────────────

def reported_cells(root: Path = ROOT, manifest: Path | None = None) -> dict[tuple, dict]:
    """{(system, domain, qid, seed): cell} for every reported cell of the four systems this
    batch can re-run (planner only where the alias arm needs it), with its source after the
    merge. The manifest records one source row per cell; two with different prompts abort."""
    rerun_p = root / RISK_RERUN
    if not rerun_p.exists():
        raise SystemExit(f"{RISK_RERUN} is missing: the plan is built after its merge (decision 4)")
    rerun_prov = _sibling_prov(RISK_RERUN)
    rerun = {(r["system"], r["qid"], int(r["seed"])): i
             for i, r in enumerate((json.loads(l) for l in rerun_p.read_text().splitlines()), 1)}
    cells: dict[tuple, dict] = {}
    with open(manifest or root / MANIFEST, newline="") as fh:
        for m in csv.DictReader(fh):
            sysn, dom = m["system"], m["domain"]
            if sysn not in BASELINES and not (sysn == "planner" and dom in ALIAS_DOMAINS):
                continue
            k3 = (sysn, m["qid"], int(m["seed"]))
            if (m["source_file"] in POST_MERGE.get(dom, ()) and m["report_set"] not in NO_OVERLAY_SETS
                    and k3 in rerun):
                pr = provenance(rerun_prov, root)["prompts"]
                src = (RISK_RERUN, rerun[k3], rerun_prov, _sha(pr[sysn]),
                       _sha(pr["_shared_briefing"]) if "_shared_briefing" in pr else "")
            else:
                src = (m["source_file"], int(m["line_no"]), m["provenance_file"], m["prompt_sha256"],
                       m["briefing_sha256"])
            c = cells.setdefault((sysn, dom, m["qid"], int(m["seed"])),
                                 {"tier": m["tier"], "sources": {}, "report_sets": set()})
            c["sources"].setdefault(src, set()).add(m["report_set"])
            c["report_sets"].add(m["report_set"])
    for key, c in cells.items():
        if len({s[3] for s in c["sources"]}) > 1:
            raise SystemExit(f"{key}: reported from rows with different recorded prompts {sorted(c['sources'])}")
        # the row most report sets use (they share the prompt)
        c["src"] = max(sorted(c["sources"]), key=lambda s: len(c["sources"][s]))
    return cells


def recorded_prompt(cell: dict, system: str, root: Path = ROOT) -> str:
    fn, _, prov, sha, _ = cell["src"]
    text = provenance(prov, root).get("prompts", {}).get(system)
    if text is None or _sha(text) != sha:
        raise SystemExit(f"{system} prompt in {prov} does not match the recorded hash {sha[:10]} ({fn})")
    return text


def override_text(ov: dict, root: Path = ROOT) -> str:
    """The text a PROMPT_OVERRIDES entry sends, checked against its recorded hash."""
    text = json.loads((root / ov["send_from"]).read_text())["prompts"][ov["system"]]
    if _sha(text) != ov["send_sha256"]:
        raise SystemExit(f"override {ov['id']}: {ov['send_from']} {ov['system']} does not hash to "
                         f"{ov['send_sha256'][:10]}")
    return text


def prompt_override(arm: str, domain: str, system: str, recorded_sha: str, overrides=None):
    """The PROMPT_OVERRIDES entry that applies to a recorded-arm cell, or None."""
    if arm != RECORDED:
        return None
    return next((ov for ov in (PROMPT_OVERRIDES if overrides is None else overrides)
                 if (ov["domain"], ov["system"], ov["recorded_sha256"]) == (domain, system, recorded_sha)),
                None)


def recorded_temperature(cell: dict, root: Path = ROOT):
    return provenance(cell["src"][2], root).get("temperature")


def load_react_affected(path) -> list[dict]:
    p = Path(path) if path else None
    if p is None or not p.exists():
        raise SystemExit(f"react_affected list not found ({path}). It is built by re-executing every "
                         f"recorded react step on both engines; pass it with --react-affected.")
    with open(p, newline="") as fh:
        rows = list(csv.DictReader(fh))
    need = {"domain", "source_file", "line_no", "qid", "seed"}
    if rows and need - set(rows[0]):
        raise SystemExit(f"{p} lacks columns {sorted(need - set(rows[0]))}")
    return rows


def rule_aliases(schema_path: Path) -> dict[str, list[str]]:
    rules = json.loads(Path(schema_path).read_text()).get("business_rules", {}) or {}
    return {n: list(s["aliases"]) for n, s in rules.items() if s.get("aliases")}


def alias_qids(corpus: dict, aliases: dict[str, list[str]]) -> set[str]:
    """Questions that use an alias's qualifier word, case-insensitive, at word boundaries
    ('active' from 'active employees'). GDC's model applied the rule wherever the qualifier
    qualified the register's rows ('active Directors' as well as 'active employees'), so
    the confound (audit item C) reaches every such question, not only the whole phrase."""
    words = sorted({a.split()[0] for al in aliases.values() for a in al}, key=len, reverse=True)
    pat = re.compile(r"\b(" + "|".join(map(re.escape, words)) + r")\b", re.I)
    return {q for q, rec in corpus.items() if pat.search(rec["question"])}


def with_aliases(text: str, aliases: dict[str, list[str]]) -> str:
    """The recorded prompt with one line per alias inserted after its rule's line in the
    BUSINESS RULES block, in that block's own words: the alias takes the rule's
    definition, as GDC's prompt gives the alias the rule (`'current': 'Status' in [...]`
    -> `'active employees': 'Status' in [...]`). Each rule line must occur exactly once."""
    lines = text.split("\n")
    for name, al in aliases.items():
        head = f"  - {name!r}: "
        at = [i for i, l in enumerate(lines) if l.startswith(head)]
        if len(at) != 1:
            raise ValueError(f"rule line for {name!r} occurs {len(at)} times, expected 1")
        definition = lines[at[0]][len(head):]
        lines[at[0] + 1:at[0] + 1] = [f"  - {a!r}: {definition}" for a in al]
    return "\n".join(lines)


def copied_default_emissions(cells: dict, root: Path = ROOT) -> dict[tuple, str]:
    """Temperature-0 baseline cells whose row is a copy of a row in a default-temperature
    file: same system, qid, wall time and token counts. A file without provenance counts
    as default-temperature."""
    fp = {}
    for key, c in cells.items():
        if key[0] in BASELINES and recorded_temperature(c, root) == 0:
            r = _row(root, c["src"][0], c["src"][1])
            fp[(r["system"], r["qid"], r.get("wall_time_s"), r.get("input_tokens"), r.get("output_tokens"))] = key
    own = {c["src"][:2] for c in cells.values()}
    out: dict[tuple, str] = {}
    for p in sorted(root.glob("results*.jsonl")):
        pp = p.with_name(_sibling_prov(p.name))
        if pp.exists() and json.loads(pp.read_text()).get("temperature") == 0:
            continue
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if not line.strip():
                continue
            r = json.loads(line)
            k = (r.get("system"), r.get("qid"), r.get("wall_time_s"), r.get("input_tokens"), r.get("output_tokens"))
            if k in fp and fp[k] not in out and (p.name, i) not in own:
                out[fp[k]] = f"copy of {p.name}:{i} (seed {r.get('seed')})"
    return out


def build_plan(react_affected, root: Path = ROOT, manifest: Path | None = None, domains=DOMAINS, log=print,
               overrides=None):
    """-> (rows, texts): the plan rows, and {sha256: text} for every prompt they send."""
    cells = reported_cells(root, manifest)
    sel: dict[tuple, dict] = {}                       # (arm, system, domain, qid, seed) -> reasons/detail

    def add(arm, key, reason, detail=""):
        s = sel.setdefault((arm,) + key, {"reasons": [], "detail": []})
        s["reasons"].append(reason)
        if detail:
            s["detail"].append(detail)

    for key, c in cells.items():
        if key[0] == "code_agent":
            add(RECORDED, key, "i_code_agent")
        if (key[0] in ("react", "text_to_sql") and TABLE5_TIERS.get(key[1]) == c["tier"]
                and recorded_temperature(c, root) is None):
            add(RECORDED, key, "ii_default_temperature")
        if key[0] == "text_to_sql":
            add(RECORDED, key, "vi_text_to_sql_one_sqlite")
    left = Counter((k[0], k[1], c["tier"]) for k, c in cells.items()
                   if k[0] in ("react", "text_to_sql") and recorded_temperature(c, root) is None
                   and TABLE5_TIERS.get(k[1]) != c["tier"])
    if left:
        log(f"  NOTE default-temperature react/text_to_sql cells outside the Table 5 tiers, not planned: {dict(left)}")

    superseded = unreported = 0
    for a in load_react_affected(react_affected):
        key = ("react", a["domain"], a["qid"], int(a["seed"]))
        c = cells.get(key)
        if c is None:
            unreported += 1
        elif not any((s[0], s[1]) == (a["source_file"], int(a["line_no"])) for s in c["sources"]):
            superseded += 1                            # its row is no longer the reported one
        else:
            add(RECORDED, key, "iii_react_engine", a.get("kind_of_difference", ""))
    log(f"  react_affected: {superseded} row(s) superseded by the merge, {unreported} not reported: not planned")

    for key, how in copied_default_emissions(cells, root).items():
        add(RECORDED, key, "iv_copied_default_emission", how)

    texts: dict[str, str] = {}
    alias_text: dict[str, str] = {}
    for dom in ALIAS_DOMAINS:
        cfg = domains[dom]
        aliases = rule_aliases(cfg["schema"])
        if not aliases:
            continue
        qs = alias_qids(H.load_corpus(cfg["corpus"]), aliases)
        terms = [a.lower() for al in aliases.values() for a in al]
        for key, c in cells.items():
            if key[1] != dom or key[2] not in qs or key[0] not in BRIEFED:
                continue
            rec = recorded_prompt(c, key[0], root)
            if any(t in rec.lower() for t in terms):
                continue                               # this system was given the alias
            new = with_aliases(rec, aliases)
            alias_text[_sha(rec)] = new
            add(ALIAS, key, "v_alias", "aliases " + "; ".join(f"{n}: {al}" for n, al in aliases.items()))

    corpora = {d: H.load_corpus(domains[d]["corpus"]) for d in {k[2] for k in sel}}
    rows = []
    for (arm, sysn, dom, qid, seed), s in sorted(sel.items()):
        c = cells[(sysn, dom, qid, seed)]
        fn, line, prov, sha, bsha = c["src"]
        rec = recorded_prompt(c, sysn, root)
        ov = prompt_override(arm, dom, sysn, sha, overrides)
        sent = alias_text[sha] if arm == ALIAS else override_text(ov, root) if ov else rec
        texts[_sha(sent)] = sent
        r = _row(root, fn, line)
        if (r.get("system"), r.get("qid"), int(r.get("seed", -1))) != (sysn, qid, seed):
            raise SystemExit(f"{fn}:{line} is not the row of {(sysn, qid, seed)}")
        pv = provenance(prov, root)
        rows.append({
            "arm": arm, "domain": dom, "system": sysn, "qid": qid, "seed": seed,
            "reasons": ";".join(s["reasons"]), "detail": " | ".join(s["detail"]),
            "source_file": fn, "line_no": line, "provenance_file": prov,
            "recorded_temperature": pv.get("temperature"),
            "recorded_code_max_retries": (pv.get("bounds") or {}).get("code_max_retries"),
            "recorded_status": r.get("status"), "recorded_verdict": r.get("outcome_class"),
            "recorded_calls": r.get("llm_calls"), "recorded_cost_usd": r.get("cost_usd") or 0.0,
            "recorded_prompt_sha256": sha, "sent_prompt_sha256": _sha(sent), "briefing_sha256": bsha,
            "prompt_override": ov["id"] if ov else "",
            "query_matches_recorded": r.get("query") == corpora[dom][qid]["question"],
            "report_sets": ";".join(sorted(c["report_sets"])),
            "out_file": out_name(arm, dom) + ".jsonl",
        })
    _price(rows, cells, root)
    return rows, texts


def out_name(arm: str, domain: str) -> str:
    """The recorded arm writes the file parity_overlay overlays for the domain."""
    stem = batch2_file(domain)[: -len(".jsonl")]
    return stem + ("_alias" if arm == ALIAS else "")


def _price(rows: list[dict], cells: dict, root: Path) -> None:
    """realistic: the recorded cost of the same cell; a code_agent cell that ended at a
    lower retry bound is priced at the mean of the cells already re-run at a raised bound
    in its domain. worst: the bound's call count x a per-call bound, cache-blind: three times
    the largest input any recorded call of that system in that domain sent, plus the adapters'
    output limit (max_tokens) billed in full. The dearest recorded call is not a bound: a
    fresh emission can be longer than any recorded one."""
    from evidence.eval_run.adapters.base import Adapter
    raised, dearest, in_max = defaultdict(list), defaultdict(float), defaultdict(int)
    for (sysn, dom, _, _), c in cells.items():
        r = _row(root, c["src"][0], c["src"][1])
        for t in r.get("per_call_tokens") or []:
            in_max[(sysn, dom)] = max(in_max[(sysn, dom)], int(t.get("in", 0) or 0))
    for key, n_in in in_max.items():
        dearest[key] = cost_usd(MODEL, 3 * n_in, Adapter.max_tokens)
    for (sysn, dom, _, _), c in cells.items():
        r = _row(root, c["src"][0], c["src"][1])
        b = (provenance(c["src"][2], root).get("bounds") or {}).get("code_max_retries") or 0
        if sysn == "code_agent" and b >= 10:
            raised[dom].append(r.get("cost_usd") or 0.0)
    pooled = [x for v in raised.values() for x in v]
    for row in rows:
        capped = (row["system"] == "code_agent" and row["recorded_status"] == "timeout"
                  and (row["recorded_code_max_retries"] or 0) < BOUNDS["code_max_retries"])
        ref = raised.get(row["domain"]) or pooled
        row["realistic_usd"] = round(sum(ref) / len(ref) if capped and ref else row["recorded_cost_usd"], 6)
        row["worst_usd"] = round(CALLS[row["system"]] * dearest[(row["system"], row["domain"])], 6)


def write_plan(rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PLAN_FIELDS)
        w.writeheader()
        w.writerows(rows)


def estimate(rows: list[dict]) -> list[tuple]:
    agg = defaultdict(lambda: [0, 0.0, 0.0])
    for r in rows:
        a = agg[(r["arm"], r["system"], r["domain"])]
        a[0] += 1; a[1] += r["realistic_usd"]; a[2] += r["worst_usd"]
    return [(k, *v) for k, v in sorted(agg.items())]


# ── what is sent ────────────────────────────────────────────────────────────

class SubstitutingClient:
    """Innermost client for one group of cells: swaps the current system prompt of the
    group's system for the group's recorded text, byte for byte, keeping the cache_control
    block structure. Anything else is refused before it is sent: another system text, a
    temperature other than 0, another model."""

    def __init__(self, inner, current: str, target: str, model: str = MODEL):
        self._inner, self._target, self._model = inner, target, model
        self._accept = {_sha(current), _sha(target)}
        self.billed = 0.0                              # $ of every response received (cache-aware)
        self.sent = Counter()
        self.kinds = Counter()                         # 'swapped' / 'already recorded' per call
        self.refused: list[str] = []
        self.messages = self

    def _refuse(self, why: str):
        self.refused.append(why)
        raise RuntimeError(f"{GUARD}: {why}")

    def create(self, **kw):
        t = kw.get("temperature")
        if isinstance(t, bool) or t != TEMP:
            self._refuse(f"temperature={t!r}, expected {TEMP}")
        if kw.get("model") != self._model:
            self._refuse(f"model={kw.get('model')!r}, expected {self._model}")
        sysv = kw.get("system")
        blocks = [{"type": "text", "text": sysv}] if isinstance(sysv, str) else [dict(b) for b in (sysv or [])]
        h = _sha("".join(b.get("text", "") for b in blocks))
        if h not in self._accept:
            self._refuse(f"unrecognised system prompt (sha {h[:10]})")
        last = blocks[-1] if blocks else {"type": "text"}
        kw["system"] = [{**{k: v for k, v in last.items() if k != "text"}, "type": "text", "text": self._target}]
        self.kinds["already recorded" if h == _sha(self._target) else "swapped"] += 1
        self.sent[_sha(self._target)] += 1
        resp = self._inner.messages.create(**kw)
        u = getattr(resp, "usage", None)
        if u is not None:                              # billed on arrival, whatever the cell does next
            n = lambda k: int(getattr(u, k, 0) or 0)   # noqa: E731
            self.billed += cost_usd_cached(self._model, n("input_tokens"), n("cache_creation_input_tokens"),
                                           n("cache_read_input_tokens"), n("output_tokens"))
        return resp


class _GuardHalt(Exception):
    pass


def _halting(sub: SubstitutingClient, echo=print):
    """run_cells progress hook: stops the run right after a cell the guard refused."""
    def progress(msg):
        echo(msg)
        if sub.refused:
            raise _GuardHalt(sub.refused[0])
    return progress


_CURRENT: dict[str, dict] = {}


def current_prompts(domain: str) -> dict[str, str]:
    """The system text the CURRENT code would send in `domain`, per system (no API)."""
    if domain not in _CURRENT:
        import pandas as pd
        cfg = DOMAINS[domain]
        ads = H.build_adapters(H.CachingClient(_FakeInner(), cache=True), pd.read_pickle(cfg["register"]),
                               H.load_briefing(cfg["schema"], cfg["register"]), model=MODEL,
                               temperature=TEMP, bounds=BOUNDS, schema_path=cfg["schema"])
        _CURRENT[domain] = {s: ad.cache_prefix_text() for s, ad in ads.items()}
    return _CURRENT[domain]


def groups(rows: list[dict]) -> dict[tuple, dict[tuple, dict[str, list[int]]]]:
    """{(arm, domain): {(system, sent sha): {qid: [seeds]}}}, system-outer for cache warmth."""
    out: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for r in rows:
        out[(r["arm"], r["domain"])][(r["system"], r["sent_prompt_sha256"])][r["qid"]].append(int(r["seed"]))
    return out


def prompt_keys(file_groups) -> dict[tuple, str]:
    """Provenance key per (system, sent sha) in one output file: the system's name for the
    prompt most of its cells get, `system@<sha12>` for any other."""
    by_sys = defaultdict(list)
    for (sysn, sha), qs in file_groups.items():
        by_sys[sysn].append((-sum(map(len, qs.values())), sha))
    keys = {}
    for sysn, v in by_sys.items():
        for i, (_, sha) in enumerate(sorted(v)):
            keys[(sysn, sha)] = sysn if i == 0 else f"{sysn}@{sha[:12]}"
    return keys


@contextmanager
def _prompts_recorded(prompts: dict[str, str]):
    """Provenance records the texts this file sends, not the current code's."""
    orig = H.collect_prompts
    H.collect_prompts = lambda adapters: dict(prompts)
    try:
        yield
    finally:
        H.collect_prompts = orig


def fixed_code(root: Path = ROOT) -> dict:
    """Which engine and code-agent fixes the running code carries, and the hashes of the
    files they live in."""
    import gdc.primitives as gp
    import planner.contract as pc
    import planner.integrate as pi
    import evidence.eval_run.tools_code as tc
    checks = {
        "engine: one arithmetic grammar for plan search and executor": hasattr(gp, "arith_parse"),
        "engine: stable total-order sort": hasattr(gp, "stable_sort"),
        "engine: executed step checked against its planned state": hasattr(pi, "PlanExecutionDivergence"),
        "engine: typed refusal of an undeclared aggregate": hasattr(pc, "DECLARED_AGG_FNS"),
        "code_agent: one namespace per episode": hasattr(tc, "new_namespace"),
        "code_agent: labelled index kept": hasattr(tc, "index_to_columns"),
        "code_agent: tool description states the frame": (
            hasattr(tc, "code_tools") and "7-row register" in tc.code_tools(7)[0]["description"]),
        "code_agent: tool description says variables persist": (
            hasattr(tc, "code_tools")
            and CODE_AGENT_TOOL_TEXT["kept_sentence"] in tc.code_tools(7)[0]["description"]),
    }
    # sha256: the engine files parity_overlay.check_batch2 compares with the build's code.
    return {"checks": checks, "contract_version": getattr(pc, "CONTRACT_VERSION", None),
            "sqlite": sqlite_info(), "sha256": engine_hashes(root)}


def expected_tools(system: str, domain: str):
    import pandas as pd
    from evidence.eval_run.tools_code import code_tools
    from evidence.eval_run.tools_decl import DECL_TOOLS
    from evidence.eval_run.tools_sql import SQL_TOOLS
    if system == "code_agent":
        return code_tools(len(pd.read_pickle(DOMAINS[domain]["register"])))
    return {"react": DECL_TOOLS, "text_to_sql": SQL_TOOLS}.get(system)


def _git(root: Path) -> dict:
    try:
        head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                       stderr=subprocess.DEVNULL).decode().strip()
        dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain",
                                         "--untracked-files=no"], stderr=subprocess.DEVNULL).strip()
        return {"commit": head, "tracked_changes": bool(dirty)}
    except Exception:                                  # noqa: BLE001 -- not a checkout
        return {"commit": "<unknown>", "tracked_changes": None}


def _record(prov: Path, arm: str, domain: str, keys: dict, meta: dict, fixes: dict,
            plan_sha: str, plan_keys: list, overrides: dict, sent: Counter, cells: dict) -> None:
    """Add this batch's facts to the harness provenance (fields the drift check ignores).
    plan_keys are every cell planned for this file: parity_overlay.check_batch2 requires
    the file to hold exactly them."""
    p = json.loads(prov.read_text())
    b = p.setdefault("batch2", {
        "script": "scripts/rerun_temp0_batch2.py", "arm": arm, "domain": domain,
        "git": [], "fixed_code": fixes, "plan_sha256": [],
        "plan_keys": plan_keys, "plan_keys_sha256": _sha(json.dumps(plan_keys)),
        "prompt_keys": {}, "prompt_overrides": overrides, "sent_calls_by_sha256": {}, "cell_prompt": {},
        "timeout_note": "per_query_timeout_s bounds text_to_sql's SQL only; code_agent exec has no wall-clock bound",
        "sqlite_vs_recorded": SQLITE_VS_RECORDED,
        "code_agent_tool_description": CODE_AGENT_TOOL_TEXT,
    })
    for k, v in (("git", _git(ROOT)), ("plan_sha256", plan_sha)):
        if v not in b[k]:
            b[k].append(v)
    for (sysn, sha), k in keys.items():
        b["prompt_keys"][k] = {"sha256": sha, "system": sysn, **meta.get(sha, {})}
    for h, n in sent.items():
        b["sent_calls_by_sha256"][h] = b["sent_calls_by_sha256"].get(h, 0) + n
    b["cell_prompt"].update(cells)
    prov.write_text(json.dumps(p, indent=2))


def _prompt_meta(rows: list[dict]) -> dict:
    """{sent sha: {recorded_sha256, recorded_in, alias_lines_added, prompt_overrides}} for
    provenance; a sent text that stands for several recorded ones (an override) lists each."""
    out: dict = {}
    for r in rows:
        m = out.setdefault(r["sent_prompt_sha256"], {"recorded_sha256": set(), "recorded_in": set(),
                                                     "alias_lines_added": r["arm"] == ALIAS,
                                                     "prompt_overrides": set()})
        m["recorded_sha256"].add(r["recorded_prompt_sha256"])
        m["recorded_in"].add(r["provenance_file"])
        if r.get("prompt_override"):
            m["prompt_overrides"].add(r["prompt_override"])
    return {h: {k: sorted(v) if isinstance(v, set) else v for k, v in m.items()} for h, m in out.items()}


def _overrides_of(rows: list[dict]) -> dict:
    """{override id: its entry and the number of cells it applies to} for one file."""
    n = Counter(r["prompt_override"] for r in rows if r.get("prompt_override"))
    return {ov["id"]: {**ov, "cells": n[ov["id"]]} for ov in PROMPT_OVERRIDES if n[ov["id"]]}


def guard_rows(root: Path = ROOT) -> list[tuple]:
    return [(p.name, r["system"], r["qid"], r["seed"]) for p in sorted(root.glob("results_temp0_batch2_*.jsonl"))
            for r in _load(p) if GUARD in str(r.get("error") or "")]


def _logged_spend(root: Path) -> float:
    return sum(r.get("cost_usd_cached") or r.get("cost_usd") or 0.0
               for p in root.glob("results_temp0_batch2_*.jsonl") for r in _load(p))


class SpendLedger:
    """What the batch has been billed, persisted in SPEND_LEDGER beside its outputs.
    `billed_usd`: every response received, interrupted cells included. `reserved`: the
    worst case of the batch now running, written before it starts and cleared when it
    returns; a reservation left by a process that died is moved to `unaccounted_usd`
    on the next start and stays counted. Dollar totals only, no cell keys."""

    def __init__(self, root: Path, recover: bool = True):
        self.path = Path(root) / SPEND_LEDGER
        self.d = (json.loads(self.path.read_text()) if self.path.exists()
                  else {"billed_usd": 0.0, "unaccounted_usd": 0.0, "reserved": None})
        if recover and self.d.get("reserved"):
            self.d["unaccounted_usd"] += self.d["reserved"]["usd"]
            self.d["reserved"] = None
            self._save()

    def _save(self):
        self.path.write_text(json.dumps(self.d, indent=1))

    def total(self) -> float:
        r = self.d.get("reserved")
        return self.d["billed_usd"] + self.d["unaccounted_usd"] + (r["usd"] if r else 0.0)

    def reserve(self, usd: float, file: str, system: str) -> None:
        self.d["reserved"] = {"usd": usd, "file": file, "system": system}
        self._save()

    def settle(self, billed: float) -> None:
        self.d["billed_usd"] += billed
        self.d["reserved"] = None
        self._save()


def spent(root: Path = ROOT) -> float:
    """The larger of the billed ledger and the logged rows' cost: the ledger counts
    calls of cells a transport error interrupted (never logged) and an open reservation."""
    led = SpendLedger(root, recover=False).total() if (Path(root) / SPEND_LEDGER).exists() else 0.0
    return max(led, _logged_spend(Path(root)))


def _execute(rows, texts, client_for, out_dir: Path, *, cap=None, worst=None, quiet=False):
    """Run every planned cell through the harness, one (system, qid) batch at a time.
    `client_for(current, target)` returns the innermost client of a group. Returns
    (completed, {group: SubstitutingClient})."""
    fixes = fixed_code()
    meta = _prompt_meta(rows)
    ledger = SpendLedger(out_dir)
    subs = {}
    for (arm, dom), fg in groups(rows).items():
        cfg = DOMAINS[dom]
        res, prov = out_dir / f"{out_name(arm, dom)}.jsonl", out_dir / f"{out_name(arm, dom)}.provenance.json"
        keys = prompt_keys(fg)
        mine = [r for r in rows if (r["arm"], r["domain"]) == (arm, dom)]
        plan_sha = _sha(json.dumps(sorted((r["system"], r["qid"], r["seed"], r["sent_prompt_sha256"])
                                          for r in mine)))
        plan_keys = sorted([r["system"], r["qid"], int(r["seed"])] for r in mine)
        old = json.loads(prov.read_text()).get("batch2", {}) if prov.exists() else {}
        if old and old["fixed_code"]["sha256"] != fixes["sha256"]:
            moved = sorted(f for f in set(old["fixed_code"]["sha256"]) | set(fixes["sha256"])
                           if old["fixed_code"]["sha256"].get(f) != fixes["sha256"].get(f))
            raise SystemExit(f"[resume guard] {res.name} was started on other engine/harness code "
                             f"(changed: {moved}). Start a fresh file or restore that code.")
        if old and old["fixed_code"].get("sqlite") != fixes["sqlite"]:
            raise SystemExit(f"[resume guard] {res.name} was started on another SQLite "
                             f"({old['fixed_code'].get('sqlite')} != {fixes['sqlite']}). Start a fresh "
                             f"file or restore that SQLite.")
        if old and old.get("plan_keys") != plan_keys:
            raise SystemExit(f"[resume guard] {res.name} was started for another cell plan "
                             f"({len(old.get('plan_keys') or [])} planned cells, now {len(plan_keys)}). "
                             f"The builder takes the file only with exactly its planned cells.")
        with _prompts_recorded({keys[g]: texts[g[1]] for g in fg}):
            for (sysn, sha), qs in fg.items():
                sub = subs[(arm, dom, sysn, sha)] = client_for(current_prompts(dom)[sysn], texts[sha])
                for qid, seeds in qs.items():
                    done = {(r["system"], r["qid"], r["seed"]) for r in _load(res)}
                    todo = sorted(s for s in seeds if (sysn, qid, s) not in done)
                    if not todo:
                        continue
                    reserve = worst[(sysn, dom)] * len(todo) if worst else 0.0
                    if cap is not None and spent(out_dir) + reserve > cap:
                        print(f"  STOP before {sysn}/{dom}/{qid}: spent ${spent(out_dir):.2f} + worst "
                              f"${reserve:.2f} would exceed cap ${cap:.2f}. "
                              f"Re-run with a higher --cap to continue.")
                        return False, subs
                    before, billed0 = Counter(sub.sent), getattr(sub, "billed", 0.0)
                    ledger.reserve(reserve, res.name, sysn)
                    try:
                        summary = H.run_cells(
                            qids=[qid], seeds=todo, systems=[sysn], client_factory=lambda: sub,
                            model=MODEL, temperature=TEMP, use_cache=True, bounds=BOUNDS,
                            corpus_path=cfg["corpus"], gold_path=cfg["gold"], register_path=cfg["register"],
                            schema_path=cfg["schema"], results_path=res, provenance_path=prov,
                            progress=_halting(sub, echo=(lambda *a: None) if quiet else print))
                    except _GuardHalt as exc:
                        ledger.settle(getattr(sub, "billed", 0.0) - billed0)
                        print(f"  HALT: the prompt guard refused {sysn}/{dom}/{qid} ({exc}). Nothing was "
                              f"sent for that cell; delete its row from {res.name} before re-running.")
                        return False, subs
                    billed = getattr(sub, "billed", 0.0) - billed0
                    ledger.settle(billed)
                    ran = {f"{r['system']}/{r['qid']}/{r['seed']}": keys[(sysn, sha)] for r in _load(res)
                           if r["system"] == sysn and r["qid"] == qid and r["seed"] in todo}
                    _record(prov, arm, dom, keys, meta, fixes, plan_sha, plan_keys, _overrides_of(mine),
                            sub.sent - before, ran)
                    if summary.get("aborted"):
                        print(f"  ABORTED: {summary.get('abort')} -- fix the cause and re-run to resume.")
                        return False, subs
                    if cap is not None and billed > reserve + 1e-9:
                        print(f"  STOP after {sysn}/{dom}/{qid}: it billed ${billed:.4f}, more than its "
                              f"worst-case estimate ${reserve:.4f}; the estimate no longer bounds the "
                              f"spend. Raise the bound estimate before continuing.")
                        return False, subs
    return True, subs


# ── preflight ───────────────────────────────────────────────────────────────

class _Usage:
    input_tokens = 10; output_tokens = 5; cache_creation_input_tokens = 0; cache_read_input_tokens = 0


class _Resp:
    def __init__(self):
        self.content = [type("B", (), {"type": "text", "text": '{"final_answer": "preflight"}'})()]
        self.usage = _Usage(); self.stop_reason = "end_turn"; self.id = "preflight"; self.model = MODEL


class _FakeInner:
    """No-API stand-in that records what would be sent."""
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return _Resp()


def preflight(rows: list[dict], texts: dict) -> bool:
    """No-API run of EVERY planned cell through the real harness: each cell is logged once,
    each call sends its group's recorded text at temperature 0 on the model of record with
    the fixed tools, and each provenance records the texts sent."""
    fixes = fixed_code()
    ok = all(fixes["checks"].values())
    for name, hit in fixes["checks"].items():
        print(f"  {'ok ' if hit else 'MISSING'} {name}")
    fakes = {}

    def client_for(current, target):
        fake = _FakeInner()
        sub = SubstitutingClient(fake, current, target)
        fakes[id(sub)] = fake
        return sub

    with tempfile.TemporaryDirectory() as d:
        done, subs = _execute(rows, texts, client_for, Path(d), quiet=True)
        ok &= done
        kinds = Counter(k for sub in subs.values() for k in sub.kinds)
        print(f"  groups {len(subs)}: the current text was swapped for the recorded one in "
              f"{kinds['swapped']}, was already the recorded text in {kinds['already recorded']}")
        for (arm, dom, sysn, sha), sub in subs.items():
            calls = fakes[id(sub)].calls
            want_tools = expected_tools(sysn, dom)
            bad = [c for c in calls if c.get("temperature") != TEMP or c.get("model") != MODEL
                   or _sha(c["system"][-1]["text"]) != sha or c.get("tools") != want_tools]
            g_ok = bool(calls) and not bad and not sub.refused and set(sub.sent) == {sha}
            ok &= g_ok
            if not g_ok:
                print(f"  FAILED group {arm}/{dom}/{sysn}/{sha[:10]}: calls {len(calls)}, bad {len(bad)}, "
                      f"refused {sub.refused[:1]}")
        for (arm, dom), fg in groups(rows).items():
            res, prov = Path(d) / f"{out_name(arm, dom)}.jsonl", Path(d) / f"{out_name(arm, dom)}.provenance.json"
            got = Counter((r["system"], r["qid"], r["seed"]) for r in _load(res))
            want = Counter((s, q, sd) for (s, _), qs in fg.items() for q, sds in qs.items() for sd in sds)
            p = json.loads(prov.read_text())
            keys = prompt_keys(fg)
            sent_ok = (all(p["prompts"].get(keys[g]) == texts[g[1]] for g in fg)
                       and sorted(p["batch2"]["sent_calls_by_sha256"]) == sorted({g[1] for g in fg}))
            b2 = p["batch2"]
            f_ok = (got == want and sent_ok and p["temperature"] == TEMP and p["bounds"] == BOUNDS
                    and set(b2["cell_prompt"]) == {f"{s}/{q}/{sd}" for s, q, sd in want}
                    and {tuple(k) for k in b2["plan_keys"]} == set(want)
                    and b2["fixed_code"]["sqlite"] == sqlite_info()
                    and b2["code_agent_tool_description"] == CODE_AGENT_TOOL_TEXT)
            ok &= f_ok
            print(f"  {out_name(arm, dom):<38} cells {sum(got.values()):>4}/{sum(want.values()):<4} "
                  f"prompts {', '.join(f'{keys[g]}={g[1][:10]}' for g in fg)}  {'ok' if f_ok else 'FAILED'}")
    print("  PREFLIGHT", "PASSED" if ok else "FAILED")
    return ok


# ── run ─────────────────────────────────────────────────────────────────────

def run(rows: list[dict], texts: dict, cap: float, root: Path = ROOT) -> bool:
    fixes = fixed_code()
    if not all(fixes["checks"].values()):
        print(f"  REFUSED: the engine or code-agent fixes are missing: "
              f"{[k for k, v in fixes['checks'].items() if not v]}")
        return False
    refused = guard_rows(root)
    if refused:
        print(f"  REFUSED: rows the prompt guard refused are still logged; delete them first: {refused}")
        return False
    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
    except Exception:                                  # noqa: BLE001 -- .env is optional
        pass
    from anthropic import Anthropic
    api = Anthropic()
    worst = {(r["system"], r["domain"]): r["worst_usd"] for r in rows}
    done, _ = _execute(rows, texts, lambda cur, tgt: SubstitutingClient(api, cur, tgt), root,
                       cap=cap, worst=worst)
    return done


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--cap", type=float, default=10.0)
    ap.add_argument("--react-affected", default=None, help="react cells the fixed engine changes (required)")
    ap.add_argument("--plan-out", default=None, help="write the cell plan CSV here")
    a = ap.parse_args(argv)
    print("=" * 96)
    print("BATCH 2: temperature-0 re-run under the recorded prompts, fixed engine + code-agent harness")
    rows, texts = build_plan(a.react_affected)
    plan_out = a.plan_out or (ROOT / "results_temp0_batch2.cell_plan.csv" if a.run else None)
    if plan_out:
        write_plan(rows, Path(plan_out))
        print(f"  plan written to {plan_out}")
    reasons = Counter(x for r in rows for x in r["reasons"].split(";"))
    print(f"  cells {len(rows)}  by reason {dict(sorted(reasons.items()))}")
    print(f"  {'arm':<15}{'system':<12}{'domain':<10}{'cells':>6}{'realistic':>11}{'worst':>10}")
    for (arm, sysn, dom), n, rl, wc in estimate(rows):
        print(f"  {arm:<15}{sysn:<12}{dom:<10}{n:>6}{rl:>11.2f}{wc:>10.2f}")
    tot_r, tot_w = sum(r["realistic_usd"] for r in rows), sum(r["worst_usd"] for r in rows)
    print(f"  {'total':<37}{len(rows):>6}{tot_r:>11.2f}{tot_w:>10.2f}   cap ${a.cap:.2f}   bounds {BOUNDS}")
    if not all(r["query_matches_recorded"] for r in rows):
        print(f"  NOTE {sum(not r['query_matches_recorded'] for r in rows)} cell(s) whose recorded question "
              f"differs from the current corpus")
    ov = Counter(r["prompt_override"] for r in rows if r["prompt_override"])
    for o in PROMPT_OVERRIDES:
        print(f"  prompt override {o['id']}: {ov[o['id']]} {o['domain']} {o['system']} cells recorded under "
              f"{o['recorded_sha256'][:8]} are sent {o['send_sha256'][:8]} ({o['send_from']})")
    sq = sqlite_info()
    print(f"  SQLite {sq['sqlite_version']} (sqlite3 module {sq['sqlite3_module_version']}), functions "
          f"{sq['functions']}; {SQLITE_VS_RECORDED}")
    print(f"  already billed: ${spent():.2f}")
    print("=" * 96)
    if not preflight(rows, texts):
        return 2
    if not a.run:
        print("\nDry run only (no API calls made). Re-invoke with --run to execute live.")
        return 0
    ok = run(rows, texts, a.cap)
    n = sum(len(_load(ROOT / fn)) for fn in {r["out_file"] for r in rows})
    print(f"\n{'COMPLETE' if ok and n == len(rows) else 'INCOMPLETE'}: {n}/{len(rows)} rows, spend ${spent():.2f}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
