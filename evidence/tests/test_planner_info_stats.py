"""The post hoc equal-information run (review/planner_info_prereg.md): scripts/planner_info_prompt.py
builds the informed planner prompts the runner pins, from GDC's recorded prompts, with the planner's
own instructions byte for byte and the committed split (review/planner_info_split.md) as generated;
scripts/planner_info_stats.py writes nothing without its results file, and on a stand-in built from
the reported planner cells (relabelled planner_gdcinfo) it must reproduce the reported planner:
OUTCOME B (GDC's employee lead), the reported Spider difference, and the reported planner's cells
per database. Its comparisons are appended to comparisons.csv under new set names, every other byte
kept. The builder runs once, in a subprocess (the overlay flag is read when parity_overlay is first
imported). No API.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import planner_info_prompt as pip_
from scripts import planner_info_stats as pis

ROOT = Path(__file__).resolve().parents[2]
COMPARISONS = ROOT / "reports" / "stats" / "comparisons.csv"
NEW_SETS = set(pis.SETS.values())


def _have_inputs() -> bool:
    return all((ROOT / c["gdc_prov"]).exists() and (ROOT / c["planner_prov"]).exists() for c in pip_.DOMAINS.values())


@pytest.fixture(scope="module")
def builds():
    if not _have_inputs():
        pytest.skip("recorded provenance files not present")
    return pip_.build_all(ROOT)


def test_the_builder_gives_the_pinned_prompts_and_keeps_the_planners_own_instructions(builds):
    from scripts.run_planner_info import PROMPTS
    assert set(builds) == set(PROMPTS)
    for d, b in builds.items():
        assert (b["planner_sha256"], b["informed_sha256"]) == PROMPTS[d]
        assert b["informed"] == b["head"] + b["block"] + b["tail"]
        assert b["planner_prompt"] == b["head"] + b["briefing"] + b["tail"]
        assert b["head"].endswith("REGISTER SCHEMA + DOMAIN CONTEXT:\n")
        assert b["tail"].startswith("\n\nREGISTER COLUMNS (verbatim):\n")
        assert "\n".join(x["text"] for x in b["split"]) == b["gdc_prompt"]
        assert {x["cls"] for x in b["split"]} <= {"keep", "mixed", "out", "blank"}
        # nothing of GDC's output format reaches the planner
        for marker in ('"goal"', "OrderSpec", "CONTRACT", "Return ONLY a JSON", "grain", "derive", "emit {"):
            assert marker not in b["block"], (d, marker)


def test_the_one_mixed_line_keeps_the_alias_and_the_literal_filter(builds):
    mixed = [(d, x) for d, b in builds.items() for x in b["split"] if x["cls"] == "mixed"]
    assert [d for d, _ in mixed] == ["employee"]
    sent = mixed[0][1]["sent"]
    assert sent == ('  - When the request refers to "current" or "active employees" rows, filter \'Status\' to one '
                    'of those values: ["Active", "On Leave", "Sabbatical"].')


def test_the_committed_split_is_the_generated_one(builds):
    assert (ROOT / "review" / "planner_info_split.md").read_text(encoding="utf-8") == pip_.split_markdown(builds)


def standin(d: Path) -> Path:
    """The 398 planned cells as a planner_gdcinfo run whose replies are the reported planner's."""
    from scripts.run_planner_info import MAX_TOKENS, MODEL, PROMPTS, SYSTEM_ID, Ctx
    ctx = Ctx(ROOT)
    rows = [dict(ctx.reported[k], system=SYSTEM_ID, domain=k[0]) for k in ctx.cells]
    d.mkdir(parents=True)
    p = d / "results_temp0_planner_gdcinfo.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    p.with_name(p.name.replace(".jsonl", ".provenance.json")).write_text(json.dumps(
        {"system": SYSTEM_ID, "model": MODEL, "temperature": 0, "max_tokens": MAX_TOKENS, "complete": True,
         "domains": {dm: {"informed_prompt_sha256": inf} for dm, (_, inf) in PROMPTS.items()},
         "plan_keys": ctx.plan_keys}))
    return p


RUN = r"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.getcwd())
import scripts.planner_info_stats as pis
d = Path(sys.argv[1])
rows, _ = pis.check_input(d / "results_temp0_planner_gdcinfo.jsonl")
G, cells = pis.builder_cells()
for i in (1, 2):                                       # twice: the second run must leave the same bytes
    r = pis.compute(G, cells, rows, d / "out", d / "comparisons.csv")
    (d / f"comparisons_run{i}.csv").write_bytes((d / "comparisons.csv").read_bytes())
(d / "result.json").write_text(json.dumps({"outcome": r["outcome"], "asrun_bad": len(r["asrun_bad"])}))
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    if not _have_inputs() or not (ROOT / "results" / "results_employee.jsonl").exists():
        pytest.skip("recorded results not present")
    d = tmp_path_factory.mktemp("planner_info") / "same"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PARITY_TEMP0": "1", "PYTHON_DOTENV_DISABLED": "1"}
    p = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, '.'); from evidence.tests."
                        f"test_planner_info_stats import standin; from pathlib import Path; standin(Path({str(d)!r}))"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    (d / "comparisons.csv").write_bytes(COMPARISONS.read_bytes())
    p = subprocess.run([sys.executable, "-c", RUN, str(d)], cwd=ROOT, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    return d, json.loads((d / "result.json").read_text())


def test_without_its_results_file_it_writes_nothing(tmp_path, capsys):
    comps = tmp_path / "comparisons.csv"
    comps.write_bytes(COMPARISONS.read_bytes())
    assert pis.main(["--results", str(tmp_path / "results_temp0_planner_gdcinfo.jsonl"),
                     "--out", str(tmp_path / "out"), "--comparisons", str(comps)]) == 0
    assert "not present, nothing written" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["comparisons.csv"]
    assert comps.read_bytes() == COMPARISONS.read_bytes()


def test_the_reported_planners_replies_give_outcome_b_and_its_reported_numbers(run):
    d, r = run
    assert r == {"outcome": {"strict": "B", "tolerant": "B"}, "asrun_bad": 0}
    perdb = [x for x in csv.DictReader(open(d / "out" / "per_database.csv", encoding="utf-8"))]
    for x in perdb:                                     # the stand-in is the reported planner, cell for cell
        if x["system"] == pis.NEW:
            twin = next(y for y in perdb if (y["scoring"], y["scope"], y["system"]) == (x["scoring"], x["scope"], "planner"))
            assert (x["correct"], x["silent_wrong"], x["n_cells"]) == (twin["correct"], twin["silent_wrong"], twin["n_cells"])
    n = {(x["scope"], x["system"]): int(x["n_cells"]) for x in perdb if x["scoring"] == "tolerant"}
    assert (n[("primary_pooled", "network")], n[("spider_pooled", "network")], n[("employee", "network")]) == (263, 135, 243)
    # on Spider, GDC minus the reported planner is the reported spider_answer difference
    rows = list(csv.DictReader(open(d / "comparisons.csv", encoding="utf-8")))
    rep = {(c["set"], c["metric"]): c for c in rows if c["alt"] == "planner"}
    new = {(c["set"], c["metric"]): c for c in rows if c["set"] in NEW_SETS}
    for sc, base in (("tolerant", "spider_answer_tolerant"), ("strict", "spider_answer_strict")):
        for m in ("accuracy", "silent_wrong"):
            a, b = new[(f"planner_info_spider_{sc}", m)], rep[(base, m)]
            assert (a["gdc_pct"], a["alt_pct"], a["diff_pp"], a["n_discordant_q"], a["p_perm"]) == \
                (b["gdc_pct"], b["alt_pct"], b["diff_pp"], b["n_discordant_q"], b["p_perm"])
    assert new[("planner_info_primary_tolerant", "accuracy")]["verdict"] == "resolved"


def test_comparisons_csv_only_gains_appended_rows_under_new_set_names(run):
    d, _ = run
    committed = COMPARISONS.read_bytes().decode("utf-8")
    kept = "".join(l for l in committed.splitlines(keepends=True) if not l.startswith(pis.SET_PREFIX))
    after = (d / "comparisons.csv").read_bytes().decode("utf-8")
    assert after.startswith(kept)
    added = list(csv.reader(after[len(kept):].splitlines()))
    assert len(added) == 8 and {a[0] for a in added} == NEW_SETS
    assert (d / "comparisons_run1.csv").read_bytes() == (d / "comparisons_run2.csv").read_bytes()
