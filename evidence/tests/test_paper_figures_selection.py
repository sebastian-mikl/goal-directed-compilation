"""scripts/make_paper_figures.py plots the rows it names, never the first match.

reports/paper_numbers.csv carries three risk in-contract groups (all 465 cells,
the 415 within GDC's vocabulary, the 50 beyond it). The cost frontier (fig5,
table4) and the cross-domain table took the first matching row, the 465-cell
group, under captions quoting the 415. They now read RISK_IN_CONTRACT_GROUP, and
a query matching several rows raises. Risk's boundary row is read from the tier
the builder emits (algebra_boundary), not the retired out_of_range. The risk
rows are withheld from this release's CSV, so the risk test skips here; the
duplicate-row test reads an employee row.
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")
ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "reports" / "paper_numbers.csv"
GROUP = "in_contract_within_vocab"


def _run(tmp_path, rows):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "make_paper_figures.py", tmp_path / "scripts")
    (tmp_path / "reports").mkdir()
    with open(tmp_path / "reports" / "paper_numbers.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    return subprocess.run([sys.executable, str(tmp_path / "scripts" / "make_paper_figures.py")],
                          capture_output=True, text=True, env=dict(os.environ, MPLBACKEND="Agg"))


def _rows():
    if not CSV.exists():
        pytest.skip("reports/paper_numbers.csv not present")
    return list(csv.DictReader(open(CSV, encoding="utf-8")))


def _get(rows, **kw):
    hit = [r for r in rows if all(r[k] == v for k, v in kw.items())]
    assert len(hit) == 1, kw
    return hit[0]["value_pct"]


def test_figures_plot_the_named_risk_group(tmp_path):
    rows = _rows()
    if not any(r["domain"] == "risk" for r in rows):
        pytest.skip("risk rows not in this release's paper_numbers.csv")
    p = _run(tmp_path, rows)
    assert p.returncode == 0, p.stderr[-2000:]
    out = tmp_path / "reports" / "figures"
    fig5 = {r["system"]: r for r in csv.DictReader(open(out / "fig5_cost_frontier.csv"))}
    for s, r in fig5.items():
        assert float(r["accuracy_pct"]) == float(_get(rows, fig_table="CostFrontier", system=s,
                                                      tier_group=GROUP, metric="accuracy"))
        assert float(r["mean_usd_per_q"]) == float(_get(rows, fig_table="CostFrontier", system=s,
                                                        tier_group=GROUP, metric="mean_cost_usd_per_query"))
        assert float(r["silent_wrong_pct"]) == float(_get(rows, fig_table="Table2", domain="risk", system=s,
                                                          tier_group=GROUP, metric="silent_wrong"))
    t2 = (out / "table2_crossdomain.md").read_text()
    risk = next(l for l in t2.splitlines() if l.startswith("| risk"))
    n = next(r["denominator"] for r in rows if r["fig_table"] == "Table2" and r["domain"] == "risk"
             and r["tier_group"] == GROUP and r["system"] == "network" and r["metric"] == "accuracy")
    acc = _get(rows, fig_table="Table2", domain="risk", system="network", tier_group=GROUP, metric="accuracy")
    assert risk.startswith(f"| risk ({n}) | {acc} |")
    t1 = (out / "table1_risk.md").read_text()
    boundary = next(l for l in t1.splitlines() if l.startswith("| algebra-boundary"))
    acc_b = _get(rows, fig_table="Table1", domain="risk", system="network", tier_group="algebra_boundary",
                 metric="accuracy")
    assert boundary.startswith(f"| algebra-boundary | {acc_b} / ")
    assert "out-of-range" not in t1


def test_a_query_matching_two_rows_fails(tmp_path):
    rows = _rows()
    dup = next(r for r in rows if r["fig_table"] == "Table2" and r["domain"] == "employee"
               and r["system"] == "network" and r["tier_group"] == "in_contract_answer"
               and r["metric"] == "accuracy")
    p = _run(tmp_path, rows + [dict(dup)])
    assert p.returncode != 0
    assert "LookupError" in p.stderr and "appears twice" in p.stderr
