"""The analyses behind the paper's numbers read the builder's cells
(scripts/paper_inputs.py), not their own copies of the results files.

ablate_checks and uncaught_silent_extract read through paper_inputs.load (the
temperature-0 overlays, the tier of record); measure_search_cost measures the GDC
Goals the builder scores (paper_inputs.network_cells: Spider from the post-fix
Phase R runs, risk T5 from the T5 file only). Spider wta_1 files; no API call.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import paper_inputs as pi

ROOT = Path(__file__).resolve().parents[2]
OFF = "results_spider_wta_1_postfix_s40off.jsonl"


@pytest.fixture
def spider():
    if not (ROOT / "results" / OFF).exists():
        pytest.skip("Spider results files not present")


def test_ablation_reads_through_paper_inputs(spider, monkeypatch):
    import scripts.ablate_checks as A
    seen = []
    real = pi.load
    monkeypatch.setattr(pi, "load", lambda fn, overlay=True: seen.append(fn) or real(fn, overlay))
    monkeypatch.setattr(A, "ABLATION_FILES", [(OFF, "wta_1", None)])
    rows = list(A.network_rows())
    assert seen == [OFF]
    recorded = [json.loads(l) for l in (ROOT / "results" / OFF).read_text().splitlines() if l.strip()]
    assert len(rows) == sum(r["system"] == "network" for r in recorded)
    assert all(fn == OFF and dom == "wta_1" for fn, dom, _ctx, _r in rows)
    gold = pi.GOLD["wta_1"]
    assert all(r["tier"] == gold[r["qid"]]["tier"] for *_, r in rows if r["qid"] in gold)


def test_uncaught_extract_iterates_the_ablation_rows(spider, monkeypatch, capsys):
    import scripts.uncaught_silent_extract as U
    calls = []
    monkeypatch.setattr(U.A, "network_rows", lambda: calls.append(1) or iter(()))
    assert U.main([]) == 0
    assert calls == [1]
    assert "UNCAUGHT silent errors (unique domain,qid): 0" in capsys.readouterr().out


def test_search_cost_measures_the_builders_gdc_goals(spider, monkeypatch, capsys):
    import scripts.measure_search_cost as M
    monkeypatch.setattr(M, "DOMAINS", ["wta_1"])
    rows = M.collect(1)
    cells = pi.network_cells("wta_1")
    with_goal = [r for _, r in cells if isinstance(r.get("artifact"), dict) and r["artifact"].get("goal")]
    assert len(rows) == len(with_goal)
    assert f"scanned {len(cells)} network rows" in capsys.readouterr().out
    assert {fn for fn, _ in cells} == {OFF, OFF.replace("s40off", "s40on")}
