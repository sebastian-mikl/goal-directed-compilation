"""scripts/calls_and_refusals.py: mean LLM calls per query and refusal rates of the five designs on
claude-sonnet-4-5 and the open-weights model, on the same 162 questions (108 in scope, 54 out of
scope). The script checks its claude-sonnet-4-5 counts against reports/paper_numbers.csv and every
open-weights cell against local_model/analysis/per_question.csv, and exits non-zero otherwise. Run
here into a scratch file, it must write the committed CSV byte for byte; every design covers all
162 questions on both models, and GDC, planner and text-to-SQL make exactly one call per query.
No API.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "reports" / "calls_and_refusals.csv"
SYSTEMS = ["network", "planner", "react", "code_agent", "text_to_sql"]
MODELS = ["claude-sonnet-4-5", "qwen3-coder-16k:30b-a3b-q4_K_M"]


def test_calls_and_refusals_cover_162_questions_and_one_call_designs(tmp_path):
    out = tmp_path / "calls_and_refusals.csv"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PARITY_TEMP0", None)
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "calls_and_refusals.py"), "--out", str(out)],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "NOTE:" not in p.stdout, p.stdout                       # no question missing from any cell set
    assert out.read_bytes() == COMMITTED.read_bytes()
    rows = list(csv.DictReader(open(out, newline="", encoding="utf-8")))
    assert [(r["model"], r["system"]) for r in rows] == [(m, s) for m in MODELS for s in SYSTEMS]
    for r in rows:
        assert (r["questions"], r["questions_in_scope"], r["questions_out_of_scope"]) == ("162", "108", "54"), r
        seeds = {"employee": 3, "spider": 5} if r["model"] == MODELS[0] else {"employee": 1, "spider": 1}
        assert int(r["in_scope_cells"]) == 81 * seeds["employee"] + 27 * seeds["spider"], r
        assert int(r["out_of_scope_cells"]) == 9 * seeds["employee"] + 45 * seeds["spider"], r
        assert int(r["cells"]) == int(r["in_scope_cells"]) + int(r["out_of_scope_cells"]), r
        if r["system"] in ("network", "planner", "text_to_sql"):
            assert (r["min_llm_calls"], r["max_llm_calls"], r["mean_llm_calls_per_query"]) == ("1", "1", "1.00"), r
        assert "tokens" not in ",".join(r)                            # no token figure, on either model
