"""The error classification (review/error_classification_prereg.md): scripts/error_classification_stats.py
writes nothing without review/error_classification.csv; with it, the labelled cells must be exactly the
in-scope strict silent-error cells and the CSV must agree with the raw passes, and the outcome rule must
follow the pre-registered thresholds. No API.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import error_classification_stats as ecs

ROOT = Path(__file__).resolve().parents[2]
CSV_IN = ROOT / "review" / "error_classification.csv"


def _rows():
    with open(CSV_IN, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_without_its_csv_it_writes_nothing(tmp_path, capsys):
    assert ecs.main(["--csv", str(tmp_path / "error_classification.csv"), "--out", str(tmp_path / "out")]) == 0
    assert "not present, nothing written" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(not CSV_IN.exists(), reason="no classification")
def test_the_committed_classification_checks_and_rewrites_its_summary_byte_for_byte(tmp_path):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1"}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "error_classification_stats.py"), "--out",
                        str(tmp_path / "out")], cwd=ROOT, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    committed = ROOT / "reports" / "stats" / "error_classification"
    for f in ("by_system.csv", "headline.csv", "summary.txt"):
        assert (tmp_path / "out" / f).read_bytes() == (committed / f).read_bytes(), f


@pytest.mark.skipif(not CSV_IN.exists(), reason="no classification")
def test_the_pair_rows_and_cell_rows_agree():
    rows = _rows()
    pairs = {r["pair_id"]: r for r in rows if r["row"] == "pair"}
    cells = [r for r in rows if r["row"] == "cell"]
    assert {c["pair_id"] for c in cells} == set(pairs)
    for pid, p in pairs.items():
        labs = [c["final_label"] for c in cells if c["pair_id"] == pid]
        assert (len(set(labs)) > 1) == (p["cells_differ"] == "True")
        if len(set(labs)) == 1:
            assert labs[0] == p["final_label"]


def _pair(system, label, agree=True, also=False):
    return {"row": "pair", "pair_id": f"{system}__d__{label}", "system": system, "final_label": label,
            "pass1_label": label, "pass2_label": label, "passes_agree": str(agree), "also_procedure": str(also),
            "cells_differ": "False", "qid": label.lower()}


@pytest.mark.parametrize("labels,outcome", [
    (["SPECIFICATION"] * 9 + ["PROCEDURE"], "A"),            # S = 90%
    (["SPECIFICATION"] * 3 + ["PROCEDURE"], "B"),            # S = 75%
    (["SPECIFICATION"] * 4 + ["PROCEDURE"], "M"),            # S = 80%
    (["SPECIFICATION"] * 7 + ["UNCLEAR"] * 3, "INCONCLUSIVE"),  # UNCLEAR 30% > 20%
])
def test_the_outcome_follows_the_preregistered_thresholds(tmp_path, labels, outcome):
    rows = [_pair("planner", l) for l in labels] + [_pair("network", "SCORING")]
    r = ecs.compute(rows, tmp_path / "out", echo=lambda *a: None)
    assert r["outcome"] == outcome and r["gdc_ok"]
