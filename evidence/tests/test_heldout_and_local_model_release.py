"""The held-out run and the open-weights run as released.

heldout/: the analysis runs offline on the scrubbed results and writes the committed analysis files
byte for byte; heldout/check_frozen_files.py finds every file under heldout/ at its FROZEN.json hash
(freeze.py --check cannot run in this layout and is not called); the provenance files carry no absolute
path and the public quoting example.
local_model/: the shipped drivers and patch are what each provenance file hashes, the cell files are
what results_sha256.txt lists, and the statistics lines of analysis/summary.txt are what
analyse_o2.py --from-shipped computes from the shipped per-question file. No API call.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELD = ROOT / "heldout"
LOCAL = ROOT / "local_model"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
ANALYSIS = ["comparisons.csv", "gdc_out_of_contract.csv", "per_question.csv", "rates.csv", "summary.txt"]
# an absolute home or scratch path, or the hyphenated form a scratch directory name takes (built from
# pieces so that this file does not itself contain what it looks for)
ABS_PATH = re.compile("|".join(["/" + "Users/", "/" + "home/[A-Za-z]", "/private/" + "tmp", "-" + "Users-"]))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_heldout_analysis_reproduces_the_committed_outputs_byte_for_byte():
    out = HELD / "results" / "analysis"
    before = {f: (out / f).read_bytes() for f in ANALYSIS}
    p = subprocess.run([sys.executable, str(HELD / "analyse_heldout.py")], cwd=ROOT, env=ENV,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-1500:]
    assert {f: (out / f).read_bytes() for f in ANALYSIS} == before


def test_every_heldout_file_matches_frozen_json():
    p = subprocess.run([sys.executable, str(HELD / "check_frozen_files.py")], cwd=ROOT, env=ENV,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:]
    assert "heldout/ entries: all match." in p.stdout


def test_heldout_and_local_model_provenance_is_scrubbed():
    files = sorted((HELD / "results").glob("*.provenance.json")) + sorted((LOCAL / "results").glob("*.provenance.json"))
    assert len(files) == 6
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert not ABS_PATH.search(text), f.name
        prompt = json.loads(text)["prompts"]["text_to_sql"]
        assert 'parens like "Unit Price (Net)"' in prompt, f.name


def test_local_model_drivers_and_patch_are_what_the_provenance_hashes():
    patch_sha = _sha(LOCAL / "local_model.patch")
    for f in sorted((LOCAL / "results").glob("*.provenance.json")):
        blocks = json.loads(f.read_text(encoding="utf-8"))["o2_local_model"]
        assert blocks
        for b in blocks:
            assert b["local_model_patch_sha256"] == patch_sha, f.name
            for name, h in b["driver_sha256"].items():
                assert _sha(LOCAL / name) == h, (f.name, name)


def test_local_model_cell_files_match_their_listed_hashes():
    listed = dict(reversed(l.split(None, 1)) for l in (LOCAL / "results" / "results_sha256.txt").read_text().splitlines())
    assert len(listed) == 4
    for name, h in listed.items():
        assert _sha(LOCAL / "results" / name.strip()) == h, name


def test_local_model_summary_statistics_follow_from_the_shipped_files():
    # rates.csv and comparisons.csv recomputed byte for byte from per_question.csv, and every
    # statistics and status-count line of summary.txt rounded once from the unrounded value
    p = subprocess.run([sys.executable, str(LOCAL / "analyse_o2.py"), "--from-shipped", "--check"], cwd=ROOT,
                       env=ENV, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-1500:]
    assert "0 line(s) of summary.txt differ" in p.stdout
    react = [l for l in (LOCAL / "analysis" / "summary.txt").read_text().splitlines()
             if l.split()[:3] == ["o2_spider_out_of_contract_aggregate", "silent_error", "react"]]
    assert len(react) == 1 and "[  7.7,  53.8]" in react[0], react      # 7/13 = 53.846%
