"""scripts/like_for_like_stats.py: the per-database like-for-like comparisons on Spider's out-of-scope tier
(twelve per metric) and Holm across them; the committed outputs are what the script writes. No API."""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "stats" / "like_for_like"


@pytest.mark.skipif(not (ROOT / "results" / "results_spider_bike_1.jsonl").exists(), reason="Spider results absent")
def test_it_rewrites_the_committed_outputs_byte_for_byte(tmp_path):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1"}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "like_for_like_stats.py"), "--out", str(tmp_path)],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    for f in ("per_database.csv", "summary.txt"):
        assert (tmp_path / f).read_bytes() == (OUT / f).read_bytes(), f
    rows = list(csv.DictReader(open(tmp_path / "per_database.csv", encoding="utf-8")))
    assert len(rows) == 24
    for m in ("silent_wrong", "declined"):
        sel = [r for r in rows if r["metric"] == m]
        assert len(sel) == 12 and {r["database"] for r in sel} == {"wta_1", "world_1", "bike_1"}
        ps = sorted(float(r["p_perm"]) for r in sel)
        assert float(min(r["p_holm_across_twelve"] for r in sel)) == pytest.approx(min(1.0, 12 * ps[0]), abs=1e-4)
