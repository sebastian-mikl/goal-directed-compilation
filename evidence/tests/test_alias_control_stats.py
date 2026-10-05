"""scripts/alias_control_stats.py: the statistics of the post hoc alias control
(review/alias_control_prereg.md). Without its results file it writes nothing. On stand-ins built
from the reported GDC employee cells (relabelled network_noalias) it must give the pre-registered
outcome the stand-in implies: the reported emissions themselves give OUTCOME B with no regression,
and the same emissions with the eight 'active' questions filtered to status == Active give
OUTCOME A. Its comparisons are appended to comparisons.csv under new set names, every other byte
kept. The builder runs once, in a subprocess (the overlay flag is read when parity_overlay is
first imported). No API.
"""
from __future__ import annotations

import copy
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import alias_control_stats as acs

ROOT = Path(__file__).resolve().parents[2]
COMPARISONS = ROOT / "reports" / "stats" / "comparisons.csv"
EIGHT = ("t4_01", "t4_02", "t4_05", "t4_07", "t4_08", "t4_10", "t4_12", "t4_14")
NEW_SETS = {"alias_control_employee_in_contract", "alias_control_noalias_minus_gdc_in_contract",
            "alias_control_noalias_minus_gdc_in_contract_tolerant", "alias_control_noalias_minus_gdc_active8",
            "alias_control_noalias_minus_gdc_active8_tolerant"}


def standin(d: Path, plant: str) -> Path:
    """The 243 reported GDC in-scope cells as a network_noalias run. `active_only`: the eight
    questions' status filter becomes == Active; `stop`: also seven cells of t1_01/t2_01/t2_07."""
    gold = json.loads((ROOT / "evidence/gold/gold_employee.json").read_text())
    rows = [json.loads(l) for l in (ROOT / "results/results_employee.jsonl").read_text().splitlines() if l.strip()]
    net = [dict(copy.deepcopy(r), system="network_noalias") for r in rows
           if r["system"] == "network" and gold.get(r["qid"], {}).get("tier", "out_of_range") != "out_of_range"]
    moved = 0
    for r in net:
        g = r["artifact"]["goal"]
        hit = plant in ("active_only", "stop") and r["qid"] in EIGHT
        if plant == "stop" and r["qid"] in ("t1_01", "t2_01", "t2_07") and moved < 7:
            hit, moved = True, moved + 1
        if hit:
            g["where"] = [["status", "==", "Active"] if w[0] == "status" else w for w in g["where"]]
    d.mkdir(parents=True)
    p = d / "results_temp0_employee_gdc_noalias.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in net))
    from scripts.run_alias_control import CONTROL_SHA256
    p.with_name(p.name.replace(".jsonl", ".provenance.json")).write_text(json.dumps(
        {"system": "network_noalias", "model": "claude-sonnet-4-5", "temperature": 0, "complete": True,
         "prompt_sha256": {"sent_control": CONTROL_SHA256},
         "plan_keys": sorted(["network_noalias", r["qid"], r["seed"]] for r in net)}))
    return p


RUN = r"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.getcwd())
import scripts.alias_control_stats as acs
G, cells = acs.builder_cells()
gold = G["pi"].GOLD["employee"]
for d in sys.argv[1:]:
    d = Path(d)
    rows = acs.check_input(d / "results_temp0_employee_gdc_noalias.jsonl", gold)
    for i in (1, 2):                                   # twice: the second run must leave the same bytes
        r = acs.compute(G, cells, rows, d / "out", d / "comparisons.csv")
        (d / f"comparisons_run{i}.csv").write_bytes((d / "comparisons.csv").read_bytes())
    (d / "result.json").write_text(json.dumps({k: r[k] for k in ("outcome", "failed", "clean")}
                                              | {"moved": {k: len(v) for k, v in r["moved"].items()},
                                                 "goal_moved": len(r["goal_moved"])}))
"""


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    if not (ROOT / "results" / "results_employee.jsonl").exists():
        pytest.skip("employee results not present")
    base = tmp_path_factory.mktemp("alias_control")
    dirs = {}
    for plant in ("same", "active_only", "stop"):
        d = base / plant
        standin(d, plant)
        (d / "comparisons.csv").write_bytes(COMPARISONS.read_bytes())
        dirs[plant] = d
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PARITY_TEMP0": "1"}
    p = subprocess.run([sys.executable, "-c", RUN, *map(str, dirs.values())], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    return {k: (d, json.loads((d / "result.json").read_text())) for k, d in dirs.items()}, p.stdout


def test_without_its_results_file_it_writes_nothing(tmp_path, capsys):
    comps = tmp_path / "comparisons.csv"
    comps.write_bytes(COMPARISONS.read_bytes())
    assert acs.main(["--results", str(tmp_path / "results_temp0_employee_gdc_noalias.jsonl"),
                     "--out", str(tmp_path / "out"), "--comparisons", str(comps)]) == 0
    assert "not present, nothing written" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["comparisons.csv"]
    assert comps.read_bytes() == COMPARISONS.read_bytes()


def test_the_reported_emissions_give_outcome_b_and_no_regression(runs):
    d, r = runs[0]["same"]
    assert r == {"outcome": {"strict": "B", "tolerant": "B"}, "failed": {"strict": [], "tolerant": []},
                 "clean": True, "moved": {"strict": 0, "tolerant": 0}, "goal_moved": 0}
    head = {h["key"]: h for h in csv.DictReader(open(d / "out" / "headline.csv", encoding="utf-8"))}
    assert head["outcome_strict"]["value"] == "B" and head["noalias_active8_failed_strict"]["value"] == "0"
    assert (head["noalias_in_scope_silent_wrong_pct_strict"]["numerator"],
            head["noalias_in_scope_silent_wrong_pct_strict"]["denominator"]) == ("0", "243")
    assert head["regression_clean"]["value"] == "1"
    # (c) reuses analyse() on employee_in_contract's resamples: with GDC's own cells it is that set's rows
    rows = list(csv.DictReader(open(d / "comparisons.csv", encoding="utf-8")))
    strip = lambda s: {(c["metric"], c["alt"]): {k: v for k, v in c.items() if k != "set"} for c in rows if c["set"] == s}
    assert strip("alias_control_employee_in_contract") == strip("employee_in_contract")
    assert len(strip("employee_in_contract")) == 8
    act = list(csv.DictReader(open(d / "out" / "active_questions.csv", encoding="utf-8")))
    assert [a["qid"] for a in act] == list(EIGHT)
    assert {a[f"s{s}_status_noalias"] for a in act for s in (0, 1, 2)} == {"in Active|On Leave|Sabbatical"}


def test_status_active_only_on_the_eight_gives_outcome_a(runs):
    d, r = runs[0]["active_only"]
    assert r["outcome"] == {"strict": "A", "tolerant": "A"} and r["failed"]["strict"] == list(EIGHT)
    assert r["clean"] and r["moved"]["strict"] == 0 and r["goal_moved"] == 0
    act = {a["qid"]: a for a in csv.DictReader(open(d / "out" / "active_questions.csv", encoding="utf-8"))}
    assert all(a["failed_strict"] == "True" and a["s0_status_noalias"] == "== Active"
               and a["s0_class_noalias"] == "silent_wrong" and a["s0_class_with_alias"] == "correct"
               for a in act.values())
    comps = {(c["set"], c["metric"]): c for c in csv.DictReader(open(d / "comparisons.csv", encoding="utf-8"))
             if c["set"].startswith("alias_control_noalias_minus_gdc")}
    c = comps[("alias_control_noalias_minus_gdc_active8", "silent_wrong")]
    assert (c["alt"], c["diff_pp"], c["n_discordant_q"], c["q_gdc_higher"], c["p_perm"]) == \
        ("network", "100.0000", "8", "8", "0.0078")                     # 2 / 2**8: all eight one way
    head = {h["key"]: h for h in csv.DictReader(open(d / "out" / "headline.csv", encoding="utf-8"))}
    assert head["noalias_active24_silent_count_strict"]["value"] == "24"
    assert "pre-registered rule (>= 6 discordant, all one way): resolved" in \
        head["diff_silent_wrong_noalias_minus_gdc_active8_strict"]["note"]


def test_more_than_six_regressions_print_stop_and_still_write(runs):
    (d, r), out = runs[0]["stop"], runs[1]
    assert not r["clean"] and r["moved"]["strict"] == 7 and r["goal_moved"] == 7
    assert "STOP: the alias control is NOT CLEAN: 7 of 219 cells" in out
    assert "STOP: the alias control is NOT CLEAN" in (d / "out" / "summary.txt").read_text()
    reg = list(csv.DictReader(open(d / "out" / "regression.csv", encoding="utf-8")))
    assert len(reg) == 7 and all(x["class_changed_strict"] == "True" for x in reg)
    assert {p.name for p in (d / "out").iterdir()} == {"active_questions.csv", "headline.csv", "rates.csv",
                                                       "regression.csv", "summary.txt"}


def test_comparisons_csv_only_gains_appended_rows_under_new_set_names(runs):
    committed = COMPARISONS.read_bytes().decode("utf-8")
    kept = "".join(l for l in committed.splitlines(keepends=True) if not l.startswith("alias_control_"))
    for plant in ("same", "active_only", "stop"):
        d = runs[0][plant][0]
        after = (d / "comparisons.csv").read_bytes().decode("utf-8")
        assert after.startswith(kept)
        added = list(csv.reader(after[len(kept):].splitlines()))
        assert len(added) == 16 and {a[0] for a in added} == NEW_SETS
        assert (d / "comparisons_run1.csv").read_bytes() == (d / "comparisons_run2.csv").read_bytes()
