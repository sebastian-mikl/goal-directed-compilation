#!/usr/bin/env python
"""The informed planner prompt of the equal-information run (review/planner_info_prereg.md):
the reported planner prompt with the shared briefing replaced by GDC's domain information,
taken line by line from GDC's recorded prompt. System id planner_gdcinfo. Offline; no API.

Sources, per database (the prompts as reported):
  GDC       s31 for employee (results/results_employee.provenance.json prompts.network); s45 for
            Spider (results/results_spider_<db>_postfix_s40off.provenance.json) and the held-out
            databases (heldout/results/heldout_<db>.provenance.json)
  planner   the recorded planner prompt of the same runs (results/results_employee, results_spider_<db>,
            heldout/results/heldout_<db> .provenance.json prompts.planner), which the current code
            renders byte for byte

The split. Every line of GDC's recorded prompt gets exactly one class (split_lines()):
  keep     domain information: the column list with each column's type, match strategy and full
           value list; the advisory context (description and column meanings); the filter-values
           rule (category value lists are closed) and the match rule; business rules
  mixed    a line with domain meaning written in Goal format: only its domain part is kept
           (MIXED_RULE: a business rule's alias and literal filter, without the emitted Goal conjunct
           and the note on GDC's operator)
  out      GDC's output format: the task framing, the JSON instructions, the Goal schema, the
           OrderSpec and the CONTRACT rules, each of which refers to Goal fields
  blank    separators
A line that fits no rule stops the build (SplitError); so does a CONTRACT rule that names no
Goal field.

Column names. GDC's prompt names each column by its schema-bridge identifier
(gdc/schema_bridge.AliasMap: 'Annual Salary' -> annual_salary); the planner's steps address the
register's own columns, which its prompt lists verbatim. In a kept or mixed line every column
identifier is written as the register's column through the bridge's inverse map, quoted as the
planner's prompt quotes columns ('Annual Salary'). The identifiers sit in four places only: the
start of a column line, the start of a column-meaning line, a business rule's column and the match
rule's example. Every such rewrite is listed.

The informed prompt is the recorded planner prompt with its one occurrence of the shared briefing
(briefing.render_briefing of the domain) replaced by the kept lines, in GDC's order, blocks
separated by one blank line. build() checks that the text before and after the briefing, the
planner's own instructions and its column list, is byte for byte the recorded prompt's.

    PYTHON_DOTENV_DISABLED=1 python scripts/planner_info_prompt.py                 # checks, hashes, diffs
    PYTHON_DOTENV_DISABLED=1 python scripts/planner_info_prompt.py --write-split review/planner_info_split.md
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]

SYSTEM_ID = "planner_gdcinfo"

# database -> where its files and its two recorded prompts are. `gdc_variant` pins the
# table-coverage instruction off for Spider (the reported answer-tier prompt).
DOMAINS = {
    "employee": {
        "group": "employee", "dir": "evaluation/domains/employee", "register": "employees.pkl",
        "corpus": "evaluation/domains/employee/eval_corpus_employee.json",
        "gold": "evidence/gold/gold_employee.json",
        "gdc_prov": "results/results_employee.provenance.json", "gdc_version": "s31", "gdc_variant": None,
        "planner_prov": "results/results_employee.provenance.json"},
    "employee_hire_evaluation": {
        "group": "heldout", "dir": "heldout/employee_hire_evaluation", "register": "shop.pkl",
        "corpus": "heldout/eval_corpus_spider_employee_hire_evaluation.json",
        "gold": "heldout/gold/gold_spider_employee_hire_evaluation.json",
        "gdc_prov": "heldout/results/heldout_employee_hire_evaluation.provenance.json", "gdc_version": "s45",
        "gdc_variant": None, "planner_prov": "heldout/results/heldout_employee_hire_evaluation.provenance.json"},
    "train_station": {
        "group": "heldout", "dir": "heldout/train_station", "register": "station.pkl",
        "corpus": "heldout/eval_corpus_spider_train_station.json",
        "gold": "heldout/gold/gold_spider_train_station.json",
        "gdc_prov": "heldout/results/heldout_train_station.provenance.json", "gdc_version": "s45",
        "gdc_variant": None, "planner_prov": "heldout/results/heldout_train_station.provenance.json"},
    "wta_1": {
        "group": "spider", "dir": "evaluation/domains/spider_wta_1", "register": "matches.pkl",
        "corpus": "evaluation/domains/spider_wta_1/eval_corpus_spider_wta_1.json",
        "gold": "evidence/gold/gold_spider_wta_1.json",
        "gdc_prov": "results/results_spider_wta_1_postfix_s40off.provenance.json", "gdc_version": "s45",
        "gdc_variant": "off", "planner_prov": "results/results_spider_wta_1.provenance.json"},
    "world_1": {
        "group": "spider", "dir": "evaluation/domains/spider_world_1", "register": "country.pkl",
        "corpus": "evaluation/domains/spider_world_1/eval_corpus_spider_world_1.json",
        "gold": "evidence/gold/gold_spider_world_1.json",
        "gdc_prov": "results/results_spider_world_1_postfix_s40off.provenance.json", "gdc_version": "s45",
        "gdc_variant": "off", "planner_prov": "results/results_spider_world_1.provenance.json"},
    "bike_1": {
        "group": "spider", "dir": "evaluation/domains/spider_bike_1", "register": "trip.pkl",
        "corpus": "evaluation/domains/spider_bike_1/eval_corpus_spider_bike_1.json",
        "gold": "evidence/gold/gold_spider_bike_1.json",
        "gdc_prov": "results/results_spider_bike_1_postfix_s40off.provenance.json", "gdc_version": "s45",
        "gdc_variant": "off", "planner_prov": "results/results_spider_bike_1.provenance.json"},
}

GOAL_FIELDS = ("goal", "columns", "grain", "grouped_by", "where", "derive", "order", "order_by", "limit")

COLUMN_LINE = re.compile(r"^  - (?P<alias>[0-9a-z_]+): (?P<type>id|text|categorical|numeric|date)"
                         r"(?P<rest>( \[match: [a-z_]+\])?(  \(values: .*\))?)$")
MEANING_LINE = re.compile(r"^    - (?P<alias>[0-9a-z_]+): (?P<rest>.+)$")
MATCH_RULE = re.compile(r'^  - Respect each column\'s \[match: \.\.\.\]: use op "contains" for contains-matched '
                        r'columns(?P<eg> \(e\.g\. (?P<alias>[0-9a-z_]+)\))?, and an exact listed value '
                        r'\("==" or "in"\) for categoricals\.$')
MIXED_RULE = re.compile(r'^(?P<head>  - When the request refers to .+ rows, filter )(?P<alias>[0-9a-z_]+)'
                        r'(?P<mid> to one of those values: )emit \{"col": "(?P<alias2>[0-9a-z_]+)", "op": "in", '
                        r'"val": (?P<vals>\[[^\]]*\])\} \(use the supported `in` op — do NOT invent a new '
                        r'operator\)\.$')
FILTER_VALUES = (
    "FILTER VALUES: when a column lists `(values: ...)`, a filter on it must use one of",
    'those EXACT values (op "==" or "in"), or op "contains" for a [match: contains] column.',
    "Do NOT invent a value that is not in the list.",
)
CONTEXT_HEAD = "CONTEXT (advisory — background for interpreting the request; NOT enforced rules):"
RULES_HEAD = "RULES (authoritative — these definitions are enforced):"
JSON_LAST = "Return JSON only — no prose, no code fences."


class SplitError(Exception):
    pass


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _sha_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def paths(root: Path, dom: str) -> dict:
    c = DOMAINS[dom]
    d = root / c["dir"]
    return {"register": d / c["register"], "schema": d / "schema.json", "corpus": root / c["corpus"],
            "gold": root / c["gold"], "gdc_prov": root / c["gdc_prov"], "planner_prov": root / c["planner_prov"]}


def recorded_prompts(root: Path, dom: str) -> tuple[str, str]:
    """(GDC's recorded prompt, the planner's recorded prompt), with the version pins checked."""
    c, p = DOMAINS[dom], paths(root, dom)
    g = json.loads(p["gdc_prov"].read_text())
    if g.get("network_prompt_version") != c["gdc_version"]:
        raise SplitError(f"{dom}: {p['gdc_prov'].name} network_prompt_version {g.get('network_prompt_version')!r}, "
                         f"expected {c['gdc_version']}")
    if c["gdc_variant"] is not None and g.get("s40_variant") != c["gdc_variant"]:
        raise SplitError(f"{dom}: {p['gdc_prov'].name} s40_variant {g.get('s40_variant')!r}, expected {c['gdc_variant']}")
    if g.get("model") != "claude-sonnet-4-5":
        raise SplitError(f"{dom}: {p['gdc_prov'].name} model {g.get('model')!r}")
    planner = json.loads(p["planner_prov"].read_text())["prompts"]["planner"]
    return g["prompts"]["network"], planner


def split_lines(text: str, amap) -> list[dict]:
    """Every line of GDC's prompt with its class, reason and (kept, mixed) the line as sent."""
    lines = text.split("\n")
    out: list[dict] = []
    section = "preamble"
    contract_rule = None

    def put(cls, reason, line, sent=None, rename=None):
        out.append({"n": len(out) + 1, "cls": cls, "reason": reason, "text": line, "sent": sent,
                    "rename": rename})

    def real(alias: str) -> str:
        try:
            return amap.to_real(alias)
        except KeyError:
            raise SplitError(f"identifier {alias!r} is no register column of the bridge") from None

    for i, line in enumerate(lines):
        if line == "":
            put("blank", "separator", line)
            if section in ("columns", "context"):
                section = "after_context"
            continue
        if section == "preamble":
            if line == "Base columns:":
                section = "columns"
                put("keep", "column list header", line, line)
            else:
                put("out", "GDC's task framing: the request becomes a Goal and an OrderSpec", line)
            continue
        if section == "columns":
            m = COLUMN_LINE.match(line)
            if m:
                r = real(m["alias"])
                put("keep", "column: type" + (", match strategy" if "[match:" in line else "")
                    + (", full value list" if "(values:" in line else ""),
                    line, f"  - {r!r}: {m['type']}{m['rest']}", (m["alias"], r))
                continue
            if line == CONTEXT_HEAD:
                section = "context"
                put("keep", "advisory context header", line, line)
                continue
            raise SplitError(f"unclassified line in the column list: {line!r}")
        if section == "context":
            m = MEANING_LINE.match(line)
            if m:
                r = real(m["alias"])
                put("keep", "column meaning", line, f"    - {r!r}: {m['rest']}", (m["alias"], r))
            elif line.startswith("  "):
                put("keep", "domain description" if line.strip() != "Column meanings:" else "column meanings header",
                    line, line)
            else:
                raise SplitError(f"unclassified line in the context: {line!r}")
            continue
        if section == "after_context":
            if line.startswith("Return ONLY a JSON object"):
                section = "format"
                put("out", "JSON instruction (output format)", line)
                continue
            raise SplitError(f"unclassified line after the context: {line!r}")
        if section in ("format", "goal", "order", "contract") and line == FILTER_VALUES[0]:
            section = "filter_values"
        if section in ("format", "goal", "order", "contract"):
            if line == '"goal":':
                section = "goal"
            elif line.startswith('"order" ('):
                section = "order"
            elif line.startswith("CONTRACT ("):
                section = "contract"
                put("out", "rule on Goal fields: header of the CONTRACT block (validity of the emitted Goal)", line)
                continue
            if section == "goal":
                why = "Goal schema"
                if "count_distinct" in line or "plain count(col)" in line:
                    why = "Goal schema (derive functions); count_distinct is a Goal function the planner's aggregate step lacks"
                put("out", why, line)
                continue
            if section == "order":
                put("out", "OrderSpec", line)
                continue
            if section == "contract":
                if line.startswith("  - "):            # a rule: this line and its indented continuation
                    j = i + 1
                    while j < len(lines) and lines[j].startswith("    "):
                        j += 1
                    rule = " ".join(lines[i:j])
                    contract_rule = [f for f in GOAL_FIELDS
                                     if re.search(rf'(?<![a-z_])"?{f}"?(?![a-z_])', rule, re.I)]
                    if not contract_rule:
                        raise SplitError(f"a CONTRACT rule names no Goal field: {rule!r}")
                    put("out", "rule on Goal fields (" + ", ".join(contract_rule) + ")", line)
                elif line.startswith("    ") and contract_rule:
                    put("out", "rule on Goal fields; continues the rule above", line)
                else:
                    raise SplitError(f"unclassified line in the CONTRACT block: {line!r}")
                continue
            # format section, before the Goal schema
            put("out", "JSON instruction (output format)", line)
            continue
        if section == "filter_values":
            i = sum(1 for x in out if x["reason"].startswith("filter-values rule"))
            if i >= len(FILTER_VALUES) or line != FILTER_VALUES[i]:
                raise SplitError(f"unexpected filter-values line: {line!r}")
            put("keep", "filter-values rule: category value lists are closed (its ops ==, in, contains are the "
                        "planner's filter ops)", line, line)
            if i == len(FILTER_VALUES) - 1:
                section = "after_filter_values"
            continue
        if section == "after_filter_values":
            if line == RULES_HEAD:
                section = "rules"
                put("keep", "rules header", line, line)
                continue
            raise SplitError(f"unclassified line after the filter-values rule: {line!r}")
        if section == "rules":
            if line == JSON_LAST:
                section = "end"
                put("out", "JSON instruction (output format)", line)
                continue
            m = MATCH_RULE.match(line)
            if m:
                if m["alias"]:
                    r = real(m["alias"])
                    put("keep", "match rule (how each match strategy filters)", line,
                        line.replace(f" (e.g. {m['alias']})", f" (e.g. {r!r})", 1), (m["alias"], r))
                else:
                    put("keep", "match rule (how each match strategy filters)", line, line)
                continue
            m = MIXED_RULE.match(line)
            if m:
                if m["alias"] != m["alias2"]:
                    raise SplitError(f"business rule names two columns: {line!r}")
                r = real(m["alias"])
                put("mixed", "business rule with its alias and literal filter; dropped: the emitted Goal "
                             "conjunct (emit {\"col\": …, \"op\": \"in\", \"val\": …}) and the note on GDC's `in` op",
                    line, f"{m['head']}{r!r}{m['mid']}{m['vals']}.", (m["alias"], r))
                continue
            raise SplitError(f"unclassified rule: {line!r}")
        if section == "end":
            raise SplitError(f"text after the last JSON instruction: {line!r}")
    return out


def gdc_block(split: list[dict]) -> str:
    """The kept and mixed lines as sent, in GDC's order, blocks separated by one blank line."""
    blocks, cur = [], []
    for x in split:
        if x["cls"] in ("keep", "mixed"):
            cur.append(x["sent"])
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return "\n\n".join("\n".join(b) for b in blocks)


def planner_parts(recorded: str, briefing: str, columns: str) -> tuple[str, str]:
    """(text before the briefing, text after it) of the planner prompt, checked against the
    planner's own template: PLAN_INSTRUCTION with no refuse or decline suffix."""
    from evidence.eval_run.adapters.planner_upfront import PLAN_INSTRUCTION
    from evidence.eval_run.refuse_config import decline_affordance, refuse_suffix
    if refuse_suffix("planner") or decline_affordance("planner"):
        raise SplitError("a refuse/decline toggle is on: the planner prompt would carry a suffix")
    head, rest = PLAN_INSTRUCTION.split("{briefing}")
    mid, tail = rest.split("{columns}")
    head, mid, tail = head.format(), mid.format(), tail.format()
    if recorded != head + briefing + mid + columns + tail:
        raise SplitError("the recorded planner prompt is not PLAN_INSTRUCTION around this briefing")
    if recorded.count(briefing) != 1:
        raise SplitError(f"the briefing occurs {recorded.count(briefing)} times in the planner prompt")
    return head, mid + columns + tail


def build(root: Path = ROOT, dom: str = "employee") -> dict:
    sys.path.insert(0, str(root))
    import pandas as pd
    from evidence.eval_run.briefing import load_briefing, render_briefing
    from gdc.schema_bridge import AliasMap
    p = paths(root, dom)
    gdc, planner = recorded_prompts(root, dom)
    df = pd.read_pickle(p["register"])
    amap = AliasMap(list(df.columns))
    split = split_lines(gdc, amap)
    if "\n".join(x["text"] for x in split) != gdc:
        raise SplitError(f"{dom}: the split does not reproduce GDC's prompt")
    block = gdc_block(split)
    bf = load_briefing(p["schema"], p["register"])
    briefing = render_briefing(bf)
    cols = ", ".join(repr(c) for c in bf["columns"])
    rec_brief = json.loads(p["planner_prov"].read_text())["prompts"].get("_shared_briefing")
    if rec_brief is not None and rec_brief != briefing:
        raise SplitError(f"{dom}: the recorded _shared_briefing differs from the current render")
    head, tail = planner_parts(planner, briefing, cols)
    informed = head + block + tail
    if not (informed.startswith(head) and informed.endswith(tail)) or informed.replace(block, briefing, 1) != planner:
        raise SplitError(f"{dom}: the informed prompt differs from the recorded one outside the briefing")
    # the column list the planner sees is the register's, and every column line of the block names one of them
    named = [x["rename"][1] for x in split if x["rename"] and COLUMN_LINE.match(x["text"])]
    if sorted(named) != sorted(map(str, df.columns)):
        raise SplitError(f"{dom}: GDC's column list {sorted(named)} is not the register's columns")
    # no bridge identifier that differs from its register column is left in a kept line: anywhere in a
    # structured line, and in prose (descriptions, column meanings) wherever it holds an underscore
    # (a plain word such as "status" in a description is English, not an identifier)
    prose = ("domain description", "column meaning")

    def left(a: str, x: dict) -> bool:
        if a == amap.to_real(a) or (x["reason"] in prose and "_" not in a):
            return False
        text = x["sent"] if x["reason"] != "column meaning" else x["sent"].split(": ", 1)[1]
        return bool(re.search(rf"(?<![0-9A-Za-z_']){re.escape(a)}(?![0-9A-Za-z_'])", text))
    stale = sorted({a for x in split if x["cls"] in ("keep", "mixed") for a in amap.aliases if left(a, x)})
    if stale:
        raise SplitError(f"{dom}: bridge identifiers left in kept lines: {stale}")
    return {"domain": dom, "gdc_prompt": gdc, "gdc_sha256": _sha(gdc), "planner_prompt": planner,
            "planner_sha256": _sha(planner), "briefing": briefing, "block": block, "informed": informed,
            "informed_sha256": _sha(informed), "split": split, "head": head, "tail": tail,
            "gdc_version": DOMAINS[dom]["gdc_version"],
            "gdc_source": DOMAINS[dom]["gdc_prov"], "planner_source": DOMAINS[dom]["planner_prov"]}


def build_all(root: Path = ROOT) -> dict:
    return {d: build(root, d) for d in DOMAINS}


def prompt_diff(b: dict) -> str:
    return "".join(difflib.unified_diff(
        b["planner_prompt"].splitlines(True), b["informed"].splitlines(True),
        f"planner, reported ({b['planner_source']} prompts.planner, sha256 {b['planner_sha256'][:12]})",
        f"{SYSTEM_ID} (sha256 {b['informed_sha256'][:12]})", n=1))


def split_markdown(builds: dict) -> str:
    L = ["# The informed planner prompt: what was taken from GDC's prompt, line by line",
         "",
         "Generated by `scripts/planner_info_prompt.py --write-split` for the equal-information run "
         "(`review/planner_info_prereg.md`). For each database, every line of GDC's recorded prompt is listed "
         "with its class and a one-line reason:",
         "",
         "- **keep:** domain information, sent as written, except that column identifiers are renamed (see "
         "below).",
         "- **mixed:** domain meaning written in Goal format. Only the domain part is sent, and the line is "
         "shown both ways.",
         "- **out:** GDC's output format: task framing, JSON instructions, the Goal schema, the OrderSpec and "
         "the CONTRACT rules on Goal fields.",
         "",
         "**Column names.** GDC's prompt names each column by its schema-bridge identifier "
         "(`gdc/schema_bridge.AliasMap`, for example `annual_salary`). The planner's steps address the "
         "register's own columns, which its prompt lists verbatim. So in every kept or mixed line, a column "
         "identifier is written as the register's column, quoted as the planner's prompt quotes columns "
         "(`'Annual Salary'`), through the bridge's inverse map. Lines marked `→` were renamed this way. "
         "Nothing else in a kept line changes.",
         "",
         "**Excluded on purpose.** The CONTRACT block includes rules on how to read a request, such as the "
         "scalar-aggregate, row-selection and implicit-ranking rules. Each of them is stated in Goal fields "
         "(columns, grain, derive, order, limit), and none states what a term or a value of the domain means, "
         "so all of them are out.",
         "",
         "**Assembly.** The informed prompt is the reported planner prompt with its one occurrence of the "
         "shared briefing replaced by the kept and mixed lines, in GDC's order, with blocks separated by one "
         "blank line. The text before and after the briefing is checked byte for byte against the reported "
         "prompt: that is the planner's own instructions and its verbatim column list.",
         ""]
    L += ["| Database | GDC prompt (sha256) | Reported planner prompt (sha256) | Informed prompt (sha256, chars) |",
          "|---|---|---|---|"]
    for d, b in builds.items():
        L.append(f"| {d} | {b['gdc_version']}, `{b['gdc_sha256'][:12]}` | `{b['planner_sha256'][:12]}` | "
                 f"`{b['informed_sha256'][:12]}`, {len(b['informed'])} |")
    L.append("")
    for d, b in builds.items():
        s = b["split"]
        cnt = {k: sum(1 for x in s if x["cls"] == k) for k in ("keep", "mixed", "out", "blank")}
        L += [f"## {d}", "",
              f"- **Sources:** GDC `{b['gdc_source']}` prompts.network ({b['gdc_version']}, sha256 "
              f"`{b['gdc_sha256']}`); planner `{b['planner_source']}` prompts.planner (sha256 `{b['planner_sha256']}`).",
              f"- **Lines:** {len(s)}: {cnt['keep']} kept, {cnt['mixed']} mixed, {cnt['out']} out, {cnt['blank']} blank.",
              f"- **Informed prompt:** sha256 `{b['informed_sha256']}`, {len(b['informed'])} characters "
              f"(reported {len(b['planner_prompt'])}).",
              f"- **Planner's own instructions:** unchanged; the text before and after the briefing is byte for "
              f"byte the reported prompt's.", ""]
        mixed = [x for x in s if x["cls"] == "mixed"]
        if mixed:
            L += ["**Mixed lines** (the domain part is sent):", ""]
            for x in mixed:
                L += [f"- Line {x['n']}. {x['reason']}.", "", "  ```", f"  GDC:  {x['text'].strip()}",
                      f"  sent: {x['sent'].strip()}", "  ```", ""]
        L += ["**Every line of GDC's prompt:**", "", "```"]
        for x in s:
            if x["cls"] == "blank":
                L.append(f"{x['n']:>3}  ·")
                continue
            tag = {"keep": "KEEP ", "mixed": "MIXED", "out": "out  "}[x["cls"]]
            L.append(f"{x['n']:>3}  {tag}  {x['reason']}")
            L.append(f"       | {x['text']}")
            if x["sent"] is not None and x["sent"] != x["text"]:
                L.append(f"       → {x['sent']}")
        L += ["```", "", "**Diff, reported planner prompt → informed prompt:**", "", "```diff",
              prompt_diff(b).rstrip("\n"), "```", ""]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write-split", default=None, help="write the split as markdown to this path")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    try:
        builds = build_all(ROOT)
    except SplitError as exc:
        print(f"STOP: {exc}")
        return 2
    for d, b in builds.items():
        s = b["split"]
        print(f"{d:<26} GDC {b['gdc_version']} {b['gdc_sha256'][:12]}  planner {b['planner_sha256'][:12]} "
              f"({len(b['planner_prompt'])} chars) -> {SYSTEM_ID} {b['informed_sha256']} ({len(b['informed'])} chars); "
              f"lines keep {sum(x['cls'] == 'keep' for x in s)} mixed {sum(x['cls'] == 'mixed' for x in s)} "
              f"out {sum(x['cls'] == 'out' for x in s)}; planner's own instructions unchanged")
        if not a.quiet:
            print(prompt_diff(b))
    if a.write_split:
        Path(a.write_split).write_text(split_markdown(builds), encoding="utf-8")
        print(f"wrote {a.write_split}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
