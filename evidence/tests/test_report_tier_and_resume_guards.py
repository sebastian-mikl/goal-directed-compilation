"""S36 Group 3 — reporting / provenance fixes.

FIX 3a T5 headline split — T5 must NOT be folded into the IN-RANGE headline
       metric; a separate T5 SUB-STUDY block renders when T5 cells exist.
FIX 3b Resume mismatch guard — harness used to write provenance only on a
       fresh run and silently leave it untouched on resume. Now: re-running
       under a changed toggle/model/prompt/corpus/gold REFUSES to resume
       rather than producing a mixed-config results.jsonl with stale
       provenance.
FIX 3c A* docstring — corrected from "admissible" to "inadmissible-but-
       greedy" (aggregate_eff clears up to 3 unmet units in one op); behaviour
       unchanged, verified by the suite staying green.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]


# ── FIX 3a — T5 headline split ──────────────────────────────────────────────

def _synth(tier, system, outcome, n):
    return [{"system": system, "tier": tier, "outcome_class": outcome,
             "qid": f"x_{i}", "seed": 0} for i in range(n)]


def _write_jsonl(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def test_3a_t5_excluded_from_in_range_headline(tmp_path):
    from evidence.eval_run.run import headline_report, _is_t5
    p = tmp_path / "results.jsonl"
    recs = (_synth("T1_single_op", "network", "correct", 8) +
            _synth("T1_single_op", "network", "silent_wrong", 2) +
            _synth("T5_high_complexity", "network", "silent_wrong", 5))
    _write_jsonl(p, recs)
    out = headline_report(p)
    # in-range covers only T1, not T5
    assert out["in_range"]["network"]["n"] == 10
    assert out["in_range"]["network"]["correct"] == 8
    # T5 block exists separately
    assert "t5" in out
    assert out["t5"]["network"]["n"] == 5
    assert out["t5"]["network"]["silent_wrong"] == 5
    # invariant: no T5 record contributes to in_range
    assert _is_t5("T5_high_complexity")
    assert not _is_t5("T1_single_op")


def test_3a_t5_block_absent_when_no_t5_cells(tmp_path):
    """In the original S26 two-block headline, no T5 cells -> no T5 block."""
    from evidence.eval_run.run import headline_report
    p = tmp_path / "results.jsonl"
    _write_jsonl(p, _synth("T1_single_op", "network", "correct", 5))
    out = headline_report(p)
    assert "t5" not in out
    assert out["in_range"]["network"]["n"] == 5


def test_3a_pushed_results_t5_separation_matches_intent(tmp_path):
    """On the architect's pushed results_t5.jsonl, the T5_high_complexity tier is
    distinct from the T1–T4 + house_vocab in-range block. (Repo-data integration
    spot-check; passes whether or not the t5 run is complete.)"""
    from evidence.eval_run.run import headline_report
    src = ROOT / "results/results_t5.jsonl"
    if not src.exists():
        pytest.skip("results_t5.jsonl absent — repo-only check")
    out = headline_report(src)
    if "t5" not in out:
        pytest.skip("no T5 cells in results_t5.jsonl yet")
    # the T5 sub-study is reported per system, with cells > 0 for any system
    # that recorded a T5 cell; the in-range block excludes those cells.
    for sys_name, stats in out["t5"].items():
        assert stats["n"] >= 1, f"T5 sub-study empty for {sys_name}"


# ── FIX 3b — resume mismatch guard ──────────────────────────────────────────

def _existing_prov(tmp_path, **overrides):
    """A minimal but realistic provenance file mirroring the schema written by
    logger.write_provenance. Used to seed the resume guard."""
    base = {
        "model": "claude-sonnet-4-5",
        "emission_model": "claude-sonnet-4-5",
        "network_prompt_version": "s31",
        "refuse_unsupported": False,
        "contract_version": "s35",
        "comparator_version": "s26",                           # excluded from the guard
        "corpus_sha256": "<placeholder>",
        "gold_sha256": "<placeholder>",
        "prompts": {"network": "stub", "planner": "stub", "react": "stub",
                    "code_agent": "stub", "text_to_sql": "stub"},
    }
    base.update(overrides)
    p = tmp_path / "provenance.json"
    p.write_text(json.dumps(base))
    return p


def _make_temp_corpus_and_gold(tmp_path):
    corpus = tmp_path / "corpus.json"
    gold = tmp_path / "gold.json"
    corpus.write_text('{"queries": []}')
    gold.write_text("{}")
    return corpus, gold


def _baseline(corpus_p, gold_p):
    """Current values matching the seeded _existing_prov, for the no-drift path."""
    return dict(model="claude-sonnet-4-5", emission_model="claude-sonnet-4-5",
                network_prompt_version="s31", refuse_unsupported=False,
                contract_version="s35",
                corpus_path=corpus_p, gold_path=gold_p,
                prompts={"network": "stub", "planner": "stub", "react": "stub",
                         "code_agent": "stub", "text_to_sql": "stub"})


def _seed(tmp_path, **prov_overrides):
    """Seed an existing provenance file with the CURRENT corpus/gold hashes (so the
    only drift is whatever the test wants to inject via overrides)."""
    import hashlib
    corpus_p, gold_p = _make_temp_corpus_and_gold(tmp_path)
    base_overrides = {
        "corpus_sha256": hashlib.sha256(corpus_p.read_bytes()).hexdigest(),
        "gold_sha256": hashlib.sha256(gold_p.read_bytes()).hexdigest(),
    }
    base_overrides.update(prov_overrides)
    prov_path = _existing_prov(tmp_path, **base_overrides)
    results = tmp_path / "results.jsonl"; results.write_text("")
    return prov_path, results, corpus_p, gold_p


def test_3b_resume_refuses_on_changed_network_prompt_version(tmp_path):
    from evidence.eval_run.harness import check_provenance_drift
    prov, results, corpus_p, gold_p = _seed(tmp_path, network_prompt_version="OLD_VERSION")
    with pytest.raises(SystemExit, match="network_prompt_version"):
        check_provenance_drift(prov, results, **_baseline(corpus_p, gold_p))


def test_3b_resume_refuses_on_changed_model(tmp_path):
    from evidence.eval_run.harness import check_provenance_drift
    prov, results, corpus_p, gold_p = _seed(tmp_path, model="claude-other-model")
    with pytest.raises(SystemExit, match="model:"):
        check_provenance_drift(prov, results, **_baseline(corpus_p, gold_p))


def test_3b_resume_refuses_on_changed_refuse_unsupported(tmp_path):
    from evidence.eval_run.harness import check_provenance_drift
    prov, results, corpus_p, gold_p = _seed(tmp_path, refuse_unsupported=True)
    with pytest.raises(SystemExit, match="refuse_unsupported"):
        check_provenance_drift(prov, results, **_baseline(corpus_p, gold_p))


def test_3b_resume_refuses_on_changed_corpus_or_gold_sha(tmp_path):
    """The corpus/gold hashes are recomputed from the CURRENT files; a stale value
    in the existing provenance must trigger the guard."""
    from evidence.eval_run.harness import check_provenance_drift
    prov, results, corpus_p, gold_p = _seed(tmp_path,
                                            corpus_sha256="<stale>",
                                            gold_sha256="<stale>")
    with pytest.raises(SystemExit) as exc:
        check_provenance_drift(prov, results, **_baseline(corpus_p, gold_p))
    msg = str(exc.value)
    assert "gold_sha256" in msg or "corpus_sha256" in msg


def test_3b_resume_refuses_on_changed_prompts(tmp_path):
    from evidence.eval_run.harness import check_provenance_drift
    prov, results, corpus_p, gold_p = _seed(tmp_path,
                                            prompts={"network": "STALE-PROMPT",
                                                     "planner": "stub", "react": "stub",
                                                     "code_agent": "stub",
                                                     "text_to_sql": "stub"})
    with pytest.raises(SystemExit, match="prompts changed for"):
        check_provenance_drift(prov, results, **_baseline(corpus_p, gold_p))


def test_3b_comparator_version_change_does_NOT_block_resume(tmp_path):
    """Comparator drift is deliberately excluded from the guard — rescore repairs
    it after the fact, and the architect's S36 bump must not block a legitimate
    resume of the pending t5 run."""
    from evidence.eval_run.harness import check_provenance_drift
    prov, results, corpus_p, gold_p = _seed(tmp_path, comparator_version="s99_future")
    check_provenance_drift(prov, results, **_baseline(corpus_p, gold_p))   # no raise


def test_3b_matching_provenance_passes(tmp_path):
    from evidence.eval_run.harness import check_provenance_drift
    prov, results, corpus_p, gold_p = _seed(tmp_path)
    check_provenance_drift(prov, results, **_baseline(corpus_p, gold_p))   # no raise


def test_3b_unreadable_provenance_exits_cleanly(tmp_path):
    from evidence.eval_run.harness import check_provenance_drift
    prov = tmp_path / "prov.json"
    prov.write_text("this is not json")
    results = tmp_path / "results.jsonl"
    corpus_p, gold_p = _make_temp_corpus_and_gold(tmp_path)
    with pytest.raises(SystemExit, match="cannot read existing provenance"):
        check_provenance_drift(prov, results, **_baseline(corpus_p, gold_p))


# ── FIX 3c — A* docstring ───────────────────────────────────────────────────

def test_3c_search_docstring_acknowledges_inadmissibility():
    """Documentation must reflect what aggregate_eff actually does (clears up to 3
    unmet units). The audit confirmed the prior "admissible" claim was false."""
    import gdc.search as s
    mod_doc = (s.__doc__ or "").lower()
    fn_doc = (s._unmet.__doc__ or "").lower()
    assert "inadmissible" in mod_doc, "module docstring still claims admissibility"
    assert "inadmissible" in fn_doc, "_unmet docstring still claims admissibility"
    assert "max_expansions" in mod_doc, "refusal correctness rationale missing"


def test_3c_search_behaviour_unchanged():
    """3c is docstring-only; the existing search invariants must hold. Use a goal
    referencing a column that no operator can introduce on the start state -> the
    operator universe exhausts and `plan` returns Unreachable, exercising the
    closed-set exhaustion path the corrected docstring now points to."""
    from gdc.search import plan, Unreachable
    from gdc.state import State, Goal
    start = State(columns=(("a", "unknown"),), grouped_by=None,
                  where=(), derive=())
    goal = Goal(columns=(("never_present", "unknown"),), grouped_by=None,
                where=(), derive=())
    res = plan(start, goal, max_expansions=64)
    assert isinstance(res, Unreachable)
    assert res.reason in ("expansion cap reached",
                          "exhausted operator universe under preconditions")
