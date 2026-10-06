#!/usr/bin/env python
"""Error classification of the step-writing designs' silent errors (review/error_classification_prereg.md).
Analysis of recorded outputs only: no evaluated system is run. Offline; no API.

Scope. Every silent-error cell under strict scoring (s48), claude-sonnet-4-5, in scope and within the
vocabulary, for the planner, react and the code agent, and for GDC (`network`) as the rubric's check:
  employee   the cells of question_clustered_stats' employee_in_contract set (as run), class silent_wrong
  Spider     the cells of spider_answer_strict, class silent_wrong
  held-out   heldout/analyse_heldout.load_cells, answer tier, strict silent_wrong (as run)
scope() takes them from the builder in process (as run_all.sh step 6 does) and checks the counts
against reports/stats/per_question.csv; it refuses to continue on any difference.

Packets. One per (system, question) pair: the labeller instructions and rubric, a neutral description
of the system's design (the system is not named), the question, the gold answer (with its
data-verified tied alternatives, where the tie lists hold any), the schema briefing the alternatives
saw (briefing.render_briefing and the register's column list), and, for every silent-error cell,
the recorded trace and the output that was scored. Cells whose trace and output are identical are
shown once, as one cell group, with their seeds. The output scored is the replay through the fixed
engine for the planner and GDC on employee and Spider (paper_inputs.scored_input), and the recorded
result everywhere else.

    PYTHON_DOTENV_DISABLED=1 python scripts/error_classification.py packets          # writes the packets
    PYTHON_DOTENV_DISABLED=1 python scripts/error_classification.py labeller-copies DIR   # pass 1 and 2 copies
    PYTHON_DOTENV_DISABLED=1 python scripts/error_classification.py collect PASS TRANSCRIPTS_DIR LABELLER_DIR
    PYTHON_DOTENV_DISABLED=1 python scripts/error_classification.py resolver-copies DIR
    PYTHON_DOTENV_DISABLED=1 python scripts/error_classification.py assemble

Files, under review/error_classification/: packets/<pair id>.md, items.csv (opaque item id per
pair, the order labellers see), cells.csv (every in-scope cell with its pair and cell group),
instructions.md (the labeller and resolver instructions), pass1.jsonl, pass2.jsonl, pass3.jsonl
(each labeller's parsed JSON and full final text), disagreements.md, spotcheck.md; and
review/error_classification.csv (per cell and per pair).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

OUT = ROOT / "review" / "error_classification"
CSV_OUT = ROOT / "review" / "error_classification.csv"
SYSTEMS = ("planner", "react", "code_agent")
CHECK = "network"
ALL = SYSTEMS + (CHECK,)
LABELS = ("SPECIFICATION", "PROCEDURE", "SCORING", "UNCLEAR")
ITEM_SEED = 20261006
SPOT_SEED = 20261006
SPOT_PER_LABEL = 10
HELDOUT = ("employee_hire_evaluation", "train_station")
MAX_ROWS = 30

DESIGN = {
    "planner": (
        "A step planner. In one model call, the model writes a list of steps over the table, each one "
        "of: filter (predicates on columns), select (keep columns), compute (a new column from an "
        "expression over two columns), aggregate (group_by columns, a value column and one of sum, "
        "mean, count, min, max) and order (sort keys and an optional row limit). Each step reads a "
        "named frame ('register' is the table) and writes the next one ('f0', 'f1', ...). The steps are "
        "then executed mechanically, and the frame named as final is the answer. TRACE is the model's "
        "reply; OUTPUT is what executing it returned."),
    "react": (
        "A tool-use loop. The model calls table tools one at a time: filter (predicates on columns), "
        "select (keep columns), compute (a new column from an expression over two columns), aggregate "
        "(group_by columns, a value column and one of sum, mean, count, min, max) and order (sort keys "
        "and an optional row limit). Each tool reads a named frame ('register' is the table) and writes "
        "the next one ('f0', 'f1', ...). After each call it sees a short summary of the result, and it "
        "ends by naming a final frame. TRACE lists each tool call with the summary the model saw; OUTPUT "
        "is the final frame."),
    "code_agent": (
        "A code agent. The model writes Python (pandas) code against the table, loaded as the DataFrame "
        "`df`, sees the output of each run, may run more code, and ends by giving its final answer. "
        "TRACE lists each code run with the output the model saw, and its last message; OUTPUT is the "
        "answer it returned."),
    "network": (
        "A declarative-specification system. In one model call, the model writes a specification of the "
        "result: its output columns, its grain (one row per input row, or grouped by columns), its "
        "filters ('where'), its derived values ('derive': an aggregate such as sum(col) or count(col), or "
        "a computed column such as colA - colB), and, separately, an ordering and row limit. A "
        "deterministic engine then derives the steps from that specification and executes them. TRACE "
        "shows the specification as written and the steps the engine derived; OUTPUT is what executing "
        "those steps returned."),
}

INSTRUCTIONS = """\
You are labelling one wrong answer of an automated data-query system. The system answered the
question below over one table; its answer was checked against the gold answer and found wrong, and
the system gave no sign that anything had gone wrong. Decide where the error came from, using the
rubric below and nothing but what is in this file.

RUBRIC. For each error, first write down the specification the system evidently intended: its
output columns, filters, grouping and derived values, as shown by its own choices, comments or
reasoning. Then:
- SPECIFICATION: correctly executing that intended specification would reproduce the wrong answer.
  The system misread the question, the schema or a term (for example: wrong column, wrong status
  set, literal reading of "active", substring instead of exact match, wrong sort direction).
- PROCEDURE: correctly executing that intended specification would give the gold answer. The steps
  failed to implement the intent (for example: wrong order of operations, a dropped or duplicated
  step, a filter applied after aggregation, a variable mix-up, a pandas or type slip).
- SCORING: the answer is right in substance but was scored wrong (shape, dict or list form, an
  identifier the question did not ask for, a tie).
- UNCLEAR: the intent cannot be determined from the trace.
Quote the evidence for every label from the trace.

Two further rules:
- If the intended specification is itself wrong AND the steps also fail to implement it, correct
  steps could still not give the gold answer: label it SPECIFICATION and set "also_procedure": true.
- The wrong cells are repetitions of the same question. Cells with an identical trace and output
  are shown once, as one cell group with their seeds. Label every cell group.
"""

OUTPUT_SPEC = """\
Write your reasoning first. Then end your answer with one JSON object, alone in a ```json fence,
in exactly this form (one entry in "cells" per cell group, in the order shown):

```json
{
  "item": "<the item id at the top of this file>",
  "intended_specification": "<output columns, filters, grouping, derived values, order/limit>",
  "cells": [
    {"group": 1, "label": "SPECIFICATION | PROCEDURE | SCORING | UNCLEAR", "also_procedure": false,
     "evidence": "<a quotation from the trace, in quotation marks, and why it shows the label>"}
  ],
  "notes": "<anything else; may be empty>"
}
```
"""

RESOLVER = """\
Two labellers labelled this wrong answer independently, with the rubric below, and disagreed. You
decide. Read the packet, judge it yourself under the rubric, and weigh both labellers' readings;
you may take either label, or another one if the trace supports it. Quote your own evidence from the
trace.
"""


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def pair_id(system: str, dom: str, qid: str) -> str:
    return f"{system}__{dom}__{qid}"


# ── scope ───────────────────────────────────────────────────────────────────

def builder():
    os.environ["PARITY_TEMP0"] = "1"
    import scripts.question_clustered_stats as Q
    G = Q.run_builder()
    cells, spec = Q.derive(G)
    Q.verify(G, cells, spec)
    return G, cells


def domain_files(dom: str) -> dict:
    if dom in HELDOUT:
        sys.path.insert(0, str(ROOT / "heldout"))
        from run_heldout import DOMAINS
        c = DOMAINS[dom]
        return {"schema": Path(c["schema"]), "register": Path(c["register"]), "gold": Path(c["gold"]),
                "corpus": Path(c["corpus"])}
    from scripts.replay_goals import DOMAIN_CONFIGS
    import scripts.planner_info_prompt as PIP
    c = DOMAIN_CONFIGS[dom]
    return {"schema": Path(c["schema"]), "register": Path(c["register"]), "gold": Path(c["gold"]),
            "corpus": PIP.paths(ROOT, dom)["corpus"]}


def scope(G, cells) -> list[dict]:
    """Every in-scope silent-error cell: {system, group, dom, qid, seed, tier, row, res, status}."""
    pi = G["pi"]
    out = []
    keep = dict(G["VOCAB_SPLIT"])["in_contract_answer"]
    for s in ALL:
        for tier in [t for t in G["IN_CONTRACT_SYN"] if keep(t)]:
            rows, gold, _ = G["syn_rows"]("employee", s, tier)
            for r in rows:
                g = gold.get(r["qid"])
                if g is None:
                    continue
                if G["outcome"]("employee", r, g) == "silent_wrong":
                    res, st = G["scored_input"]("employee", r)
                    out.append({"system": s, "group": "employee", "dom": "employee", "qid": r["qid"],
                                "seed": int(r["seed"]), "tier": g["tier"], "row": r, "res": res, "status": st,
                                "gold": g})
        for d in G["SPIDER"]:
            for r in G["_load"](pi.spider_file(d, s)):
                if r.get("system") != s:
                    continue
                g = G["GOLD_SPIDER"][d].get(r["qid"])
                if g is None or g["tier"] in G["SPIDER_BOUNDARY"]:
                    continue
                res, st = G["scored_input"](d, r)
                if G["score"](res, g, status=st)[0] == "silent_wrong":
                    out.append({"system": s, "group": "spider", "dom": d, "qid": r["qid"], "seed": int(r["seed"]),
                                "tier": g["tier"], "row": r, "res": res, "status": st, "gold": g})
    sys.path.insert(0, str(ROOT / "heldout"))
    import analyse_heldout as AH
    sel = {(c["dom"], c["system"], c["qid"], c["seed"]) for c in AH.load_cells(ROOT / "heldout" / "results")
           if c["tier"] == "answer" and c["strict"] == "silent_wrong" and c["system"] in ALL}
    for d in HELDOUT:
        f = domain_files(d)
        gold = json.loads(f["gold"].read_text())
        for line in (ROOT / "heldout" / "results" / f"heldout_{d}.jsonl").read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if (d, r["system"], r["qid"], int(r["seed"])) in sel:
                out.append({"system": r["system"], "group": "heldout", "dom": d, "qid": r["qid"],
                            "seed": int(r["seed"]), "tier": gold[r["qid"]]["tier"], "row": r,
                            "res": r.get("raw_result"), "status": r.get("status"), "gold": gold[r["qid"]]})
    # the employee and Spider cells must be exactly the builder's silent cells of the two sets
    for set_, grp in (("employee_in_contract", "employee"), ("spider_answer_strict", "spider")):
        want = sorted((c["system"], c["dom"], c["qid"], int(c["seed"])) for c in cells[set_]
                      if c["system"] in ALL and c["cls"] == "silent_wrong")
        got = sorted((c["system"], c["dom"], c["qid"], c["seed"]) for c in out if c["group"] == grp)
        if want != got:
            raise SystemExit(f"{set_}: the scope's silent cells differ from question_clustered_stats'")
    with open(ROOT / "reports" / "stats" / "per_question.csv", newline="", encoding="utf-8") as fh:
        pq = Counter()
        for r in csv.DictReader(fh):
            if r["set"] in ("employee_in_contract", "spider_answer_strict") and r["system"] in ALL:
                pq[(r["set"], r["system"])] += int(r["silent_wrong"])
    got = Counter(("employee_in_contract" if c["group"] == "employee" else "spider_answer_strict", c["system"])
                  for c in out if c["group"] != "heldout")
    if +pq != +got:
        raise SystemExit(f"the scope's silent counts {dict(got)} differ from reports/stats/per_question.csv {dict(pq)}")
    if len({(c["system"], c["dom"], c["qid"], c["seed"]) for c in out}) != len(out):
        raise SystemExit("a cell occurs twice in the scope")
    return sorted(out, key=lambda c: (c["system"], c["group"], c["dom"], c["qid"], c["seed"]))


# ── rendering ───────────────────────────────────────────────────────────────

def show_value(v) -> str:
    try:
        import pandas as pd
        if isinstance(v, pd.DataFrame):
            v = json.loads(v.to_json(orient="records"))
        elif isinstance(v, pd.Series):
            v = json.loads(v.to_json())
    except Exception:                                  # noqa: BLE001
        pass
    if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
        lines = [json.dumps(x, default=str, ensure_ascii=False) for x in v[:MAX_ROWS]]
        more = f"\n  ... ({len(v)} rows in all; the first {MAX_ROWS} are shown)" if len(v) > MAX_ROWS else ""
        return f"{len(v)} row(s):\n  " + "\n  ".join(lines) + more
    if isinstance(v, list) and len(v) > MAX_ROWS:
        return (json.dumps(v[:MAX_ROWS], default=str, ensure_ascii=False)
                + f" ... ({len(v)} items in all; the first {MAX_ROWS} are shown)")
    return json.dumps(v, default=str, ensure_ascii=False)


def gdc_steps(dom: str, art: dict) -> str:
    """The steps the engine derives from the recorded Goal (gdc.search.plan on the register's aliased
    view, as replay_goals.DomainContext builds it), then the recorded ordering and limit."""
    import pandas as pd
    from gdc.search import plan
    from gdc.state import State
    from scripts.replay_goals import aliased_view, rebuild_goal
    _, _, base = aliased_view(pd.read_pickle(domain_files(dom)["register"]))
    try:
        p = plan(State.from_base(base), rebuild_goal(art["goal"]))
    except Exception as exc:                           # noqa: BLE001
        return f"(the engine could not derive steps: {type(exc).__name__}: {exc})"
    steps = "\n".join(f"  {i + 1}. {op!r}" for i, op in enumerate(p)) if isinstance(p, list) else f"  {p!r}"
    return steps + "\n  then the ordering and limit as written are applied to the result"


def show_trace(c: dict) -> str:
    r, s = c["row"], c["system"]
    art = r.get("artifact") if isinstance(r.get("artifact"), dict) else {}
    if s == "planner":
        return art.get("raw_text") or "(no reply recorded)"
    if s == "react":
        L = []
        for i, t in enumerate(art.get("trace") or []):
            L.append(f"{i + 1}. {t.get('tool')}({json.dumps(t.get('args'), ensure_ascii=False)})")
            L.append(f"   -> {t.get('summary')}")
        L.append(f"final frame: {art.get('final_frame')!r}")
        if art.get("last_text"):
            L.append(f"last message: {art.get('last_text')}")
        return "\n".join(L)
    if s == "code_agent":
        L = []
        for i, t in enumerate(art.get("trace") or []):
            L.append(f"--- run {i + 1} ({t.get('tool')}) ---")
            L.append(str(t.get("code") or json.dumps({k: v for k, v in t.items() if k not in ("tool", "summary")},
                                                       ensure_ascii=False)).strip("\n"))
            L.append("--- output the model saw ---")
            L.append(str(t.get("summary")))
        if art.get("last_text"):
            L.append(f"--- last message ---\n{art.get('last_text')}")
        return "\n".join(L)
    # GDC
    return ("specification as written:\n  " + json.dumps(art.get("goal"), ensure_ascii=False)
            + "\nordering and limit as written:\n  " + json.dumps(art.get("order"), ensure_ascii=False)
            + "\nsteps the engine derived:\n" + gdc_steps(c["dom"], art))


def tie_alternatives(c: dict) -> list:
    if c["group"] == "employee":
        from scripts.score_tie_tolerant import tie_alt_values_syn
        return tie_alt_values_syn("employee", c["qid"])
    if c["group"] == "spider":
        from scripts.score_tie_tolerant import tie_alt_values
        return list(tie_alt_values(c["qid"]))
    ties = json.loads((ROOT / "heldout" / "tie_alternatives_heldout.json").read_text())
    return list((ties.get(c["qid"]) or {}).get("alt_values", []))


def other_answers(c: dict) -> list:
    """The tied alternatives that differ from the gold value itself."""
    g = json.dumps(c["gold"].get("value"), sort_keys=True, default=str)
    return [a for a in tie_alternatives(c) if json.dumps(a, sort_keys=True, default=str) != g]


def briefing(dom: str) -> str:
    import pandas as pd
    from evidence.eval_run.briefing import load_briefing, render_briefing
    f = domain_files(dom)
    b = load_briefing(f["schema"], f["register"])
    cols = ", ".join(repr(c) for c in pd.read_pickle(f["register"]).columns)
    return render_briefing(b) + f"\n\nTABLE COLUMNS (verbatim):\n{cols}"


def alias_note(dom: str) -> str:
    import pandas as pd
    from gdc.schema_bridge import AliasMap
    amap = AliasMap(list(pd.read_pickle(domain_files(dom)["register"]).columns))
    diff = [(a, amap.to_real(a)) for a in amap.aliases if a != amap.to_real(a)]
    if not diff:
        return ""
    return ("The specification names the table's columns by lower-case identifiers: "
            + ", ".join(f"{a} = {r!r}" for a, r in diff) + ".")


def build_packets(scope_cells: list[dict]) -> tuple[dict, list[dict]]:
    """{pair id: packet text without the item header} and the cell rows (with their cell group)."""
    import evidence.eval_run.harness as H
    by_pair = defaultdict(list)
    for c in scope_cells:
        by_pair[pair_id(c["system"], c["dom"], c["qid"])].append(c)
    packets, cell_rows = {}, []
    corpora = {}
    for pid, cs in sorted(by_pair.items()):
        c0 = cs[0]
        if c0["dom"] not in corpora:
            corpora[c0["dom"]] = H.load_corpus(domain_files(c0["dom"])["corpus"])
        question = corpora[c0["dom"]][c0["qid"]]["question"]
        groups: dict = {}
        for c in sorted(cs, key=lambda c: c["seed"]):
            key = (show_trace(c), f"status {c['status']}; {show_value(c['res'])}")
            groups.setdefault(key, []).append(c)
        P = [f"SYSTEM DESIGN\n{DESIGN[c0['system']]}", f"QUESTION\n{question}",
             f"GOLD ANSWER\n{show_value(c0['gold'].get('value'))}"]
        alts = other_answers(c0)
        if alts:
            P.append("ALSO CORRECT UNDER THE TIE RULE (other answers that tie with gold on the ranking key, "
                     "checked against the data)\n" + "\n".join(f"- {show_value(a)}" for a in alts))
        P.append(f"SCHEMA BRIEFING (what the system was told about the table)\n{briefing(c0['dom'])}")
        if c0["system"] == CHECK and alias_note(c0["dom"]):
            P.append(alias_note(c0["dom"]))
        for i, ((trace, output), gcs) in enumerate(groups.items(), start=1):
            seeds = ", ".join(str(c["seed"]) for c in gcs)
            P.append(f"CELL GROUP {i} (seeds {seeds}; {len(gcs)} wrong cell(s) with this trace and output)\n"
                     f"TRACE:\n{trace}\nOUTPUT (scored wrong):\n{output}")
            for c in gcs:
                cell_rows.append({"pair_id": pid, "system": c["system"], "group": c["group"], "domain": c["dom"],
                                  "qid": c["qid"], "seed": c["seed"], "tier": c["tier"], "cell_group": i})
        packets[pid] = "\n\n".join(P) + "\n"
    return packets, cell_rows


def items(pids: list[str]) -> dict:
    """pair id -> opaque item id, in a fixed shuffled order."""
    order = sorted(pids)
    random.Random(ITEM_SEED).shuffle(order)
    return {p: f"item_{i + 1:02d}" for i, p in enumerate(order)}


def labeller_file(item: str, packet: str) -> str:
    return f"ITEM {item}\n\n{INSTRUCTIONS}\n{packet}\n{OUTPUT_SPEC.replace('<the item id at the top of this file>', item)}"


def write_packets() -> None:
    G, cells = builder()
    sc = scope(G, cells)
    packets, cell_rows = build_packets(sc)
    it = items(list(packets))
    (OUT / "packets").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "packets").glob("*.md"):
        old.unlink()
    for pid, text in packets.items():
        (OUT / "packets" / f"{pid}.md").write_text(labeller_file(it[pid], text), encoding="utf-8")
    with open(OUT / "items.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "pair_id", "system", "packet_sha256"])
        for pid, item in sorted(it.items(), key=lambda x: x[1]):
            w.writerow([item, pid, pid.split("__")[0],
                        _sha((OUT / "packets" / f"{pid}.md").read_text(encoding="utf-8"))])
    with open(OUT / "cells.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cell_rows[0]))
        w.writeheader()
        for r in cell_rows:
            w.writerow(r)
    (OUT / "instructions.md").write_text(
        "# Labeller and resolver instructions\n\nEach packet file is `ITEM <id>`, the labeller instructions, "
        "the packet and the output form, as below. A pass-3 file adds the resolver note and the two labels "
        "before the output form.\n\n## Labeller instructions\n\n```\n" + INSTRUCTIONS + "```\n\n## Output form\n\n"
        + OUTPUT_SPEC.replace("```json", "~~~json").replace("```\n", "~~~\n") + "\n## Resolver note (pass 3)\n\n```\n"
        + RESOLVER + "```\n\n## The prompt each sub-agent received\n\n```\n" + agent_prompt("<item>", "<path>")
        + "\n```\n", encoding="utf-8")
    per = Counter(pid.split("__")[0] for pid in packets)
    print(f"{len(packets)} packets ({dict(per)}), {len(cell_rows)} cells; written to {OUT.relative_to(ROOT)}/")


def agent_prompt(item: str, path: str) -> str:
    return (f"Labelling task {item}. Read exactly one file, {path}, with the Read tool. It holds everything "
            f"you need: the instructions, the rubric, the system's design, the question, the gold answer, the "
            f"schema briefing, and the recorded trace and output of each wrong cell. Do not read, list or search "
            f"any other file, and use no other tool. Follow the instructions in the file and end your answer "
            f"with the JSON object it asks for.")


def load_items() -> dict:
    with open(OUT / "items.csv", newline="", encoding="utf-8") as fh:
        return {r["item"]: r for r in csv.DictReader(fh)}


def labeller_copies(d: Path) -> None:
    """Pass 1 and pass 2 copies of each packet under neutral names: <d>/pass1/<item>.md, <d>/pass2/<item>.md."""
    its = load_items()
    for p in ("pass1", "pass2"):
        (d / p).mkdir(parents=True, exist_ok=True)
        for item, r in its.items():
            text = (OUT / "packets" / f"{r['pair_id']}.md").read_text(encoding="utf-8")
            if _sha(text) != r["packet_sha256"]:
                raise SystemExit(f"{r['pair_id']}: packet changed since items.csv was written")
            (d / p / f"{item}.md").write_text(text, encoding="utf-8")
    print(f"{len(its)} items x 2 passes under {d}")


# ── collecting labels from the sub-agents' transcripts ──────────────────────

def _final_text(rows: list[dict]) -> str:
    for r in rows[::-1]:
        if r.get("type") == "assistant":
            c = r["message"].get("content") or []
            t = "".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            if t.strip():
                return t
    return ""


def _handback_text(rows: list[dict]) -> str:
    for r in rows[::-1]:
        if r.get("type") == "assistant":
            for x in r["message"].get("content") or []:
                if isinstance(x, dict) and x.get("type") == "tool_use" and x.get("name") == "SubagentHandback":
                    inp = x.get("input") or {}
                    return str(inp.get("report") or inp.get("message") or inp.get("text") or json.dumps(inp))
    return ""


def _tool_calls(rows: list[dict]) -> list[tuple[str, dict]]:
    out = []
    for r in rows:
        if r.get("type") == "assistant":
            for x in r["message"].get("content") or []:
                if isinstance(x, dict) and x.get("type") == "tool_use":
                    out.append((x.get("name"), x.get("input") or {}))
    return out


def parse_json(text: str) -> dict | None:
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    for b in blocks[::-1]:
        try:
            return json.loads(b)
        except json.JSONDecodeError:
            continue
    return None


def collect(pass_no: int, transcripts: Path, labeller_dir: Path) -> None:
    """Parse every sub-agent transcript whose prompt names a file of this pass under labeller_dir:
    its JSON, its full final text, its agent id and its tool calls (which must be one Read of that file)."""
    its = load_items()
    sub = labeller_dir / f"pass{pass_no}"
    found = {}
    for f in sorted(transcripts.glob("agent-*.jsonl")):
        rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        first = next((r for r in rows if r.get("type") == "user"), None)
        prompt = first["message"]["content"] if first else ""
        if isinstance(prompt, list):
            prompt = "".join(x.get("text", "") for x in prompt if isinstance(x, dict))
        m = re.search(re.escape(str(sub)) + r"/(item_\d+)\.md", prompt or "")
        if not m:
            continue
        item = m.group(1)
        text = _final_text(rows) + "\n" + _handback_text(rows)
        calls = _tool_calls(rows)
        reads = [c for c in calls if c[0] == "Read"]
        others = [c[0] for c in calls if c[0] not in ("Read", "SubagentHandback")]
        own = (sub / f"{item}.md").resolve()
        isolated = bool(reads) and all(Path(c[1].get("file_path", "")).resolve() == own for c in reads) and not others
        rec = {"pass": pass_no, "item": item, "pair_id": its[item]["pair_id"], "agent_id": f.stem.replace("agent-", ""),
               "parsed": parse_json(text), "isolated": isolated,
               "tool_calls": [c[0] for c in calls], "final_text": text.strip()}
        if item in found:
            raise SystemExit(f"pass {pass_no}: two transcripts for {item}")
        found[item] = rec
    missing = sorted(set(its) - set(found))
    bad = sorted(i for i, r in found.items() if not r["parsed"])
    with open(OUT / f"pass{pass_no}.jsonl", "w", encoding="utf-8") as fh:
        for item in sorted(found):
            fh.write(json.dumps(found[item], ensure_ascii=False) + "\n")
    print(f"pass {pass_no}: {len(found)} of {len(its)} items collected; missing {missing}; unparsed {bad}; "
          f"not isolated {sorted(i for i, r in found.items() if not r['isolated'])}")


# ── pair labels, agreement, resolution ─────────────────────────────────────

def pair_label(cell_labels: list[str]) -> tuple[str, bool]:
    """(the pair's label, whether its cells' labels differ): the shared label; else the most
    frequent, a tie going to UNCLEAR."""
    cnt = Counter(cell_labels)
    if len(cnt) == 1:
        return cell_labels[0], False
    top = cnt.most_common()
    if top[0][1] == top[1][1]:
        return "UNCLEAR", True
    return top[0][0], True


def read_pass(n: int) -> dict:
    p = OUT / f"pass{n}.jsonl"
    return {r["pair_id"]: r for r in (json.loads(l) for l in p.read_text().splitlines() if l.strip())} if p.exists() else {}


def group_counts() -> dict:
    with open(OUT / "cells.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    g = defaultdict(lambda: defaultdict(list))
    for r in rows:
        g[r["pair_id"]][int(r["cell_group"])].append(int(r["seed"]))
    return {p: dict(v) for p, v in g.items()}, rows


def labels_of(rec: dict, groups: dict) -> dict:
    """{group: (label, also_procedure, evidence)} from one labeller's JSON; checks every group is labelled once."""
    js = rec["parsed"] or {}
    out = {}
    for c in js.get("cells") or []:
        g, lab = int(c.get("group")), str(c.get("label", "")).strip().upper()
        if lab not in LABELS or g in out:
            raise SystemExit(f"{rec['pair_id']} pass {rec['pass']}: bad or repeated label for group {g}: {lab!r}")
        out[g] = (lab, bool(c.get("also_procedure")), str(c.get("evidence", "")))
    if sorted(out) != sorted(groups):
        raise SystemExit(f"{rec['pair_id']} pass {rec['pass']}: labelled groups {sorted(out)} != {sorted(groups)}")
    return out


def disagreements(groups: dict, p1: dict, p2: dict) -> list[str]:
    out = []
    for pid, gs in groups.items():
        a, b = labels_of(p1[pid], gs), labels_of(p2[pid], gs)
        la = pair_label([a[g][0] for g in gs for _ in gs[g]])[0]
        lb = pair_label([b[g][0] for g in gs for _ in gs[g]])[0]
        if la != lb or any(a[g][0] != b[g][0] for g in gs):
            out.append(pid)
    return sorted(out)


def resolver_copies(d: Path) -> None:
    groups, _ = group_counts()
    p1, p2 = read_pass(1), read_pass(2)
    its = {r["pair_id"]: i for i, r in load_items().items()}
    dis = disagreements(groups, p1, p2)
    (d / "pass3").mkdir(parents=True, exist_ok=True)
    for pid in dis:
        text = (OUT / "packets" / f"{pid}.md").read_text(encoding="utf-8")
        head, spec = text.split(OUTPUT_SPEC.replace("<the item id at the top of this file>", its[pid]))[0], \
            OUTPUT_SPEC.replace("<the item id at the top of this file>", its[pid])
        L = [RESOLVER]
        for k, rec in (("LABELLER 1", p1[pid]), ("LABELLER 2", p2[pid])):
            js = rec["parsed"]
            L.append(f"{k}\nintended specification: {js.get('intended_specification')}\n"
                     + "\n".join(f"cell group {c.get('group')}: {c.get('label')} (also_procedure "
                                 f"{bool(c.get('also_procedure'))}); evidence: {c.get('evidence')}"
                                 for c in js.get("cells") or [])
                     + (f"\nnotes: {js.get('notes')}" if js.get("notes") else ""))
        (d / "pass3" / f"{its[pid]}.md").write_text(head + "\n".join(L) + "\n\n" + spec, encoding="utf-8")
    print(f"{len(dis)} disagreements -> {d / 'pass3'}: {[its[p] for p in dis]}")


def kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def assemble() -> None:
    groups, cell_rows = group_counts()
    p1, p2, p3 = read_pass(1), read_pass(2), read_pass(3)
    its = {r["pair_id"]: i for i, r in load_items().items()}
    if set(p1) != set(groups) or set(p2) != set(groups):
        raise SystemExit("passes 1 and 2 must label every pair")
    dis = disagreements(groups, p1, p2)
    if set(p3) != set(dis):
        raise SystemExit(f"pass 3 must label exactly the disagreements: {sorted(set(dis) ^ set(p3))}")
    pairs, cells_out = [], []
    for pid, gs in sorted(groups.items()):
        a, b = labels_of(p1[pid], gs), labels_of(p2[pid], gs)
        c = labels_of(p3[pid], gs) if pid in p3 else None
        final = {g: (c[g] if c else a[g]) for g in gs}
        lab = {k: pair_label([v[g][0] for g in gs for _ in gs[g]]) for k, v in (("p1", a), ("p2", b), ("final", final))}
        system, dom, qid = pid.split("__")
        for g, seeds in gs.items():
            for s in seeds:
                cells_out.append({"row": "cell", "pair_id": pid, "item": its[pid], "system": system, "domain": dom,
                                  "qid": qid, "seed": s, "cell_group": g,
                                  "pass1_label": a[g][0], "pass2_label": b[g][0], "pass3_label": c[g][0] if c else "",
                                  "final_label": final[g][0], "also_procedure": final[g][1],
                                  "passes_agree": a[g][0] == b[g][0], "evidence": final[g][2],
                                  "intended_specification": ""})
        src = p3[pid] if c else p1[pid]
        pairs.append({"row": "pair", "pair_id": pid, "item": its[pid], "system": system, "domain": dom, "qid": qid,
                      "seed": "", "cell_group": "", "pass1_label": lab["p1"][0], "pass2_label": lab["p2"][0],
                      "pass3_label": pair_label([c[g][0] for g in gs for _ in gs[g]])[0] if c else "",
                      "final_label": lab["final"][0], "also_procedure": any(final[g][1] for g in gs),
                      "passes_agree": pid not in dis, "evidence": " | ".join(f"group {g}: {final[g][2]}" for g in gs),
                      "intended_specification": (src["parsed"] or {}).get("intended_specification", ""),
                      "cells_differ": lab["final"][1]})
    fields = ["row", "pair_id", "item", "system", "domain", "qid", "seed", "cell_group", "pass1_label", "pass2_label",
              "pass3_label", "final_label", "also_procedure", "passes_agree", "cells_differ", "intended_specification",
              "evidence"]
    with open(CSV_OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in pairs + cells_out:
            w.writerow({k: r.get(k, "") for k in fields})
    # disagreement list
    L = ["# Disagreements between passes 1 and 2, and their resolution", "",
         f"{len(dis)} of {len(groups)} pairs. A disagreement is a pair on which the passes differ, in the pair "
         "label or in any cell's label (review/error_classification_prereg.md). Pass 3 is a fresh sub-agent "
         "that saw the packet and both labels.", ""]
    for pid in dis:
        a, b, c = labels_of(p1[pid], groups[pid]), labels_of(p2[pid], groups[pid]), labels_of(p3[pid], groups[pid])
        L += [f"## {pid} ({its[pid]})", ""]
        for g in groups[pid]:
            L.append(f"- Cell group {g} (seeds {', '.join(map(str, groups[pid][g]))}): pass 1 **{a[g][0]}**, "
                     f"pass 2 **{b[g][0]}** → resolved **{c[g][0]}**.")
        L += ["", f"- Pass 1: {(p1[pid]['parsed'] or {}).get('intended_specification')}",
              f"  - Evidence: {' / '.join(a[g][2] for g in groups[pid])}",
              f"- Pass 2: {(p2[pid]['parsed'] or {}).get('intended_specification')}",
              f"  - Evidence: {' / '.join(b[g][2] for g in groups[pid])}",
              f"- Resolution: {(p3[pid]['parsed'] or {}).get('intended_specification')}",
              f"  - Evidence: {' / '.join(c[g][2] for g in groups[pid])}", ""]
    (OUT / "disagreements.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    # spot-check sample
    rng = random.Random(SPOT_SEED)
    by_label = defaultdict(list)
    for p in pairs:
        by_label[p["final_label"]].append(p["pair_id"])
    L = ["# Spot-check sample", "",
         f"For each final label, up to {SPOT_PER_LABEL} pairs drawn at random over all labelled pairs "
         f"(random.Random({SPOT_SEED}) on the sorted pair ids, labels in the order {', '.join(LABELS)}), "
         "plus every disagreement. Each entry gives the final label, the intended specification and the "
         "evidence; the packet is `review/error_classification/packets/<pair id>.md`.", ""]
    chosen = []
    for lab in LABELS:
        ids = sorted(by_label.get(lab, []))
        pick = sorted(rng.sample(ids, min(SPOT_PER_LABEL, len(ids)))) if ids else []
        chosen += [(lab, p, "sample") for p in pick]
    chosen += [(next(x["final_label"] for x in pairs if x["pair_id"] == p), p, "disagreement")
               for p in dis if p not in {c[1] for c in chosen}]
    pmap = {p["pair_id"]: p for p in pairs}
    for lab, pid, why in chosen:
        p = pmap[pid]
        L += [f"## {pid}: {lab} ({why}{'; also a disagreement' if why == 'sample' and pid in dis else ''})", "",
              f"- Intended specification: {p['intended_specification']}",
              f"- Evidence: {p['evidence']}",
              f"- Passes: 1 {p['pass1_label']}, 2 {p['pass2_label']}" + (f", 3 {p['pass3_label']}" if p["pass3_label"] else ""),
              ""]
    (OUT / "spotcheck.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    # agreement, printed (the pipeline's stats script recomputes and writes it)
    pool = [p for p in pairs if p["system"] in SYSTEMS]
    print(f"pairs {len(pairs)}; disagreements {len(dis)}; pooled pair agreement "
          f"{100 * sum(p['passes_agree'] for p in pool) / len(pool):.1f}%, kappa "
          f"{kappa([p['pass1_label'] for p in pool], [p['pass2_label'] for p in pool]):.3f}; "
          f"final {dict(Counter(p['final_label'] for p in pool))}; wrote {CSV_OUT.relative_to(ROOT)}, "
          f"disagreements.md, spotcheck.md")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("packets")
    a1 = sp.add_parser("labeller-copies"); a1.add_argument("dir")
    a2 = sp.add_parser("collect"); a2.add_argument("pass_no", type=int); a2.add_argument("transcripts"); a2.add_argument("dir")
    a3 = sp.add_parser("resolver-copies"); a3.add_argument("dir")
    sp.add_parser("assemble")
    a = ap.parse_args(argv)
    if a.cmd == "packets":
        write_packets()
    elif a.cmd == "labeller-copies":
        labeller_copies(Path(a.dir))
    elif a.cmd == "collect":
        collect(a.pass_no, Path(a.transcripts), Path(a.dir))
    elif a.cmd == "resolver-copies":
        resolver_copies(Path(a.dir))
    else:
        assemble()
    return 0


if __name__ == "__main__":
    sys.exit(main())
