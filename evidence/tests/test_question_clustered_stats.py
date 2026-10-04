"""scripts/question_clustered_stats.py: the paper's question-clustered statistics (Section 5.6),
for the employee and Spider sets, rebuilt from the recorded cells with the builder's own scorers.
It refuses to continue unless every outcome-class count equals the builder's emitted row and the CSV.
The statistical helpers are checked on small cases with a known answer; the whole script is run
and must write the committed reports/stats files, and the numbers CSV, byte for byte. No API.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

from scripts import question_clustered_stats as qs

ROOT = Path(__file__).resolve().parents[2]
STATS = ROOT / "reports" / "stats"
TRACKED = ["comparisons.csv", "per_question.csv", "rates.csv", "results_summary.txt", "run_info.json",
           "seed_agreement.csv", "verification.txt"]


def test_four_discordant_questions_all_one_way_cannot_reach_p_below_one_eighth():
    d = np.array([1.0, 1.0, 1.0, 1.0, 0.0, 0.0])
    npos, nneg, p = qs.sign_test(d)
    assert (npos, nneg) == (4, 0) and p == pytest.approx(0.125)
    assert qs.permutation_p(np.array([1, 1, 1, 1, 0, 0])) == pytest.approx(0.125)


def test_permutation_p_is_one_when_nothing_differs_and_weights_by_magnitude():
    assert qs.permutation_p(np.array([0, 0, 0])) == 1.0
    # one question moves by 3 in one direction, three by 1 the other: |sum| = 0, so every sign flip is as extreme
    assert qs.permutation_p(np.array([3, -1, -1, -1])) == 1.0


def test_holm_adjustment_is_monotone_and_capped():
    assert qs.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert qs.holm([0.6, 0.7]) == [1.0, 1.0]


def test_newcombe_interval_of_equal_rates_is_symmetric_about_zero():
    lo, hi = qs.newcombe(10, 10, 100)
    assert lo == pytest.approx(-hi) and lo < 0 < hi


def test_the_percentile_bootstrap_is_seeded_and_stratified():
    strata = np.array(["a", "a", "b", "b", "b"])
    i1 = qs.boot_index(strata, np.random.default_rng(1))
    i2 = qs.boot_index(strata, np.random.default_rng(1))
    assert (i1 == i2).all() and i1.shape == (qs.N_BOOT, 5)
    assert set(np.unique(i1[:, :2])) <= {0, 1} and set(np.unique(i1[:, 2:])) <= {2, 3, 4}   # resampled inside a stratum


def test_the_script_verifies_and_rewrites_the_committed_stats_byte_for_byte():
    before = {f: (STATS / f).read_bytes() for f in TRACKED}
    csv_files = [ROOT / "reports" / "paper_numbers.csv", ROOT / "reports" / "paper_numbers.engine.json"]
    csv_before = [p.read_bytes() for p in csv_files]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "question_clustered_stats.py")], cwd=ROOT,
                       env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-1500:]
    assert {f: (STATS / f).read_bytes() for f in TRACKED} == before
    assert [q.read_bytes() for q in csv_files] == csv_before
    lines = (STATS / "verification.txt").read_text().splitlines()
    assert sum(l.startswith("OK ") for l in lines) == 60              # 12 sets x 5 systems
    comps = list(csv.DictReader(open(STATS / "comparisons.csv", encoding="utf-8")))
    assert len(comps) == 104 and {c["alt"] for c in comps} == set(qs.ALTS)
    assert {c["set"] for c in comps} == set(qs.ORDER)
    assert not [c for c in comps if "risk" in c["set"]]               # the risk sets are not regenerated here


def test_gdc_told_it_may_decline_has_two_rate_rows_on_the_pooled_sets_resamples():
    # Spider out_of_scope_register pooled, GDC with the table-coverage instruction on: the two rows the
    # paper prints, analysed under decline_spider_oosr_pooled's name (seed 20261001 + its crc32); no comparison.
    rates = list(csv.DictReader(open(STATS / "rates.csv", encoding="utf-8")))
    rows = {r["metric"]: r for r in rates if r["set"] == qs.S40ON}
    assert len([r for r in rates if r["set"] == qs.S40ON]) == 2 and {r["system"] for r in rows.values()} == {qs.GDC}
    got = {m: (int(r["k"]), int(r["n_cells"]), int(r["n_questions"]), round(float(r["rate_pct"]), 1),
               round(float(r["ci_lo_pct"]), 1), round(float(r["ci_hi_pct"]), 1)) for m, r in rows.items()}
    assert got == {"declined": (123, 225, 45, 54.7, 40.0, 68.9), "silent_wrong": (102, 225, 45, 45.3, 31.1, 60.0)}
    assert qs.BASE_SEED + zlib.crc32(qs.S40ON_BASE.encode()) == 3763372903
    comps = list(csv.DictReader(open(STATS / "comparisons.csv", encoding="utf-8")))
    assert not [c for c in comps if c["set"] == qs.S40ON]
    lines = (STATS / "verification.txt").read_text().splitlines()
    chans = [l.split() for l in lines if l.startswith("  ") and "network" in l]
    assert [(c[0], c[-2], c[-1]) for c in chans] == [
        ("wta_1", "declined_explicit=10", "declined_typed=13"), ("world_1", "declined_explicit=20", "declined_typed=30"),
        ("bike_1", "declined_explicit=50", "declined_typed=0"), ("pooled", "declined_explicit=80", "declined_typed=43")]
