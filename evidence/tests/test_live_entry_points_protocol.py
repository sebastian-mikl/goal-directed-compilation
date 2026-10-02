"""The live entry points run the reported protocol: temperature 0 and 12 code-agent retries
(13 calls), where the harness's own defaults are the API default and 3 retries. The employee
entry point (evidence.eval_run.run) takes its corpus and gold by flag; its defaults point at the
withheld risk domain. run_cells is replaced by a recorder, so no client is built and no call is made.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMP = ROOT / "evaluation" / "domains" / "employee"


def _run_main(monkeypatch, tmp_path, extra):
    from evidence.eval_run import run
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-key")
    monkeypatch.setattr(run, "run_cells", lambda **kw: seen.update(kw) or {"ran": 0, "skipped": 0})
    monkeypatch.setattr(run, "report", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["run", "--results", str(tmp_path / "r.jsonl"),
                                      "--provenance", str(tmp_path / "p.json"), *extra])
    run.main()
    return seen


def test_employee_run_takes_its_files_by_flag_and_runs_the_reported_protocol(monkeypatch, tmp_path):
    seen = _run_main(monkeypatch, tmp_path, [
        "--corpus", str(EMP / "eval_corpus_employee.json"),
        "--gold", str(ROOT / "evidence" / "gold" / "gold_employee.json"),
        "--register", str(EMP / "employees.pkl"), "--schema", str(EMP / "schema.json"),
        "--seeds", "0", "1", "2", "--queries", "t1_01"])
    assert seen["corpus_path"] == EMP / "eval_corpus_employee.json"
    assert seen["gold_path"] == ROOT / "evidence" / "gold" / "gold_employee.json"
    assert seen["register_path"] == EMP / "employees.pkl" and seen["schema_path"] == EMP / "schema.json"
    assert seen["temperature"] == 0
    assert seen["bounds"] == {"react_max_iters": 15, "code_max_retries": 12, "per_query_timeout_s": 60}
    assert seen["seeds"] == [0, 1, 2] and seen["qids"] == ["t1_01"]


def test_flags_can_restore_the_harness_defaults(monkeypatch, tmp_path):
    seen = _run_main(monkeypatch, tmp_path, [
        "--corpus", str(EMP / "eval_corpus_employee.json"), "--queries", "t1_01",
        "--temperature", "0.7", "--code-max-retries", "3"])
    assert seen["temperature"] == 0.7 and seen["bounds"]["code_max_retries"] == 3


def test_spider_run_uses_the_reported_protocol(monkeypatch):
    from evidence.eval_run import harness
    from scripts import run_full_spider_eval as spider
    seen = {}
    monkeypatch.setattr(harness, "run_cells", lambda **kw: seen.update(kw) or {"ran": 0, "skipped": 0})
    cfg = {k: ROOT / "x" for k in ("corpus", "gold", "register", "schema", "results", "provenance")}
    spider.run_domain("wta_1", cfg, ["wta_01"])
    assert seen["temperature"] == 0
    assert seen["bounds"] == {"react_max_iters": 15, "code_max_retries": 12, "per_query_timeout_s": 60}
