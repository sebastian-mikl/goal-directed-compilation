"""The Spider gold builders execute Spider's own gold SQL against Spider's SQLite databases,
which this repository does not ship. Without the database a builder used to print
[FAIL] ids_present and [FAIL] answer_count, exit 0 and overwrite the committed gold with
every answer-expected query removed; a build of the numbers from that gold then reported 0/0 for the
Spider in-contract rows. Each builder now stops before it writes anything and exits non-zero.
Deterministic; no API. Skips when the database is present (a developer who has fetched Spider).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "evidence" / "gold"
BUILDERS = {
    "wta_1": ROOT / "spider" / "wta_1.sqlite",
    "world_1": ROOT / "spider" / "world_1.sqlite",
    "bike_1": ROOT / "database" / "bike_1" / "bike_1.sqlite",
}


@pytest.mark.parametrize("db", sorted(BUILDERS))
def test_builder_stops_and_leaves_the_committed_gold_alone(db):
    if BUILDERS[db].exists():
        pytest.skip(f"{BUILDERS[db].relative_to(ROOT)} is present: the builder would run")
    outputs = [GOLD / f"gold_spider_{db}.json", GOLD / f"gold_spider_{db}_digest.txt"]
    before = [p.read_bytes() for p in outputs]
    p = subprocess.run([sys.executable, str(GOLD / f"build_gold_spider_{db}.py")], capture_output=True, text=True,
                       env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": ""})
    assert p.returncode != 0
    assert "not found" in (p.stdout + p.stderr) and "Nothing was written" in (p.stdout + p.stderr)
    assert [q.read_bytes() for q in outputs] == before
