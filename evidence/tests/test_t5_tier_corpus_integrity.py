"""S34 — T5 high-complexity tier.

Three deterministic guards over the corpus + gold + reference plans:

(B) Reference-plan validation: each T5 query has a hand-authored primitive plan whose
    length equals the query's `complexity_ops`. Executed through the SHARED executor
    (tools_decl._apply_one), each plan must produce a result the comparator scores as
    `correct` against gold. Also asserts every reference step is NON-REDUNDANT (it
    materially transforms the frame), so complexity_ops can't be padded with no-ops.

(C) `complexity_ops` is present + correct on T1/T2/T3 (1/2/3) and T5 (5-8, spread
    ~3-4 each, all 4 in-range primitives only). Authored to the corpus as a STATIC
    structural property — never derived from any system's runtime behaviour.

(D) Self-test extension: the gold-vs-itself battery already covers every gold entry;
    this asserts a non-empty T5 subset is present so the curve has a high-complexity
    data point.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]

# The risk domain's source data is withheld from the public release, so the tests
# in this module — which exercise the engine against the withheld domain's register/corpus/gold —
# skip cleanly rather than fail when those files are absent.
_RISK_DATA = [ROOT / "environment" / "register.pkl",
              ROOT / "eval_corpus.json",
              ROOT / "evidence" / "gold" / "gold.json"]
pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in _RISK_DATA),
    reason="risk domain data not included in this release")



def _corpus():
    return json.loads((ROOT / "eval_corpus.json").read_text())


def _gold():
    return json.loads((ROOT / "evidence" / "gold" / "gold.json").read_text())


def _register():
    return pd.read_pickle(ROOT / "environment" / "register.pkl")


def _t5_qs():
    return [q for q in _corpus()["queries"] if q["tier"] == "T5_high_complexity"]


# ── Part C: complexity_ops field + propagation ───────────────────────────────

def test_S34_complexity_ops_present_on_T1_T2_T3():
    c = _corpus()["queries"]
    expected = {"T1_single_op": 1, "T2_two_ops": 2, "T3_three_ops": 3}
    for q in c:
        if q["tier"] in expected:
            assert q.get("complexity_ops") == expected[q["tier"]], (
                f"{q['id']} (tier={q['tier']}) complexity_ops={q.get('complexity_ops')} "
                f"!= {expected[q['tier']]}")


def test_S34_T5_size_and_in_range_primitives_only():
    t5 = _t5_qs()
    assert len(t5) >= 15, f"T5 has {len(t5)} queries, expected ~15"
    allowed = {"filter", "select", "compute", "aggregate", "order"}
    for q in t5:
        assert q["id"].startswith("t5_"), q["id"]
        ops = set(q["ops"])
        bad = ops - allowed
        assert not bad, f"{q['id']}: out-of-range ops {bad} (allowed: {allowed})"


def test_S34_T5_complexity_ops_spread_5_to_8():
    t5 = _t5_qs()
    spread = Counter(q["complexity_ops"] for q in t5)
    assert set(spread).issubset({5, 6, 7, 8}), f"unexpected complexity_ops: {spread}"
    for c in (5, 6, 7, 8):
        assert spread[c] >= 3, f"complexity_ops={c} has only {spread[c]} (target ~3-4)"
    assert max(spread.values()) <= 5, f"clustered: {spread}"            # not all one bucket


def test_S34_corpus_meta_lists_T5_in_tiers():
    meta = _corpus()["meta"]
    assert "T5_high_complexity" in meta["tiers"]


# ── Part B: reference-plan validation (executable through shared executor) ───

def _run_plan(plan: list[dict], register) -> tuple[pd.DataFrame, list[pd.DataFrame]]:
    """Execute a reference plan through tools_decl._apply_one (same executor used by
    every primitive system). Returns (final_frame, per_step_frames_in_order_including_register)."""
    from evidence.eval_run.tools_decl import _apply_one
    frames = {"register": register}
    snapshots = [register.copy()]
    last_name = "register"
    for i, step in enumerate(plan):
        op = step["op"]
        args = {k: v for k, v in step.items() if k != "op"}
        if not args.get("input"):
            args["input"] = last_name
        summary, out_name = _apply_one(op, args, frames)
        assert out_name is not None, f"step {i} ({op}) failed: {summary}"
        snapshots.append(frames[out_name].copy())
        last_name = out_name
    return frames[last_name], snapshots


def _frame_fingerprint(df: pd.DataFrame) -> tuple:
    """Order-sensitive fingerprint: (shape, columns, row-values-in-order). Two consecutive
    steps with the same fingerprint = the step did nothing (redundant)."""
    return (df.shape, tuple(map(str, df.columns)),
            tuple(tuple(r) for r in df.itertuples(index=False, name=None)))


def test_S34_T5_reference_plans_match_gold_exactly():
    from evidence.eval_run.comparator import score
    from evidence.gold.t5_reference_plans import T5_REFERENCE_PLANS
    from evidence.eval_run.adapters.base import df_to_result
    gold = _gold(); reg = _register()
    failures = []
    for qid, plan in T5_REFERENCE_PLANS.items():
        g = gold.get(qid)
        if g is None:
            failures.append(f"{qid}: no gold entry"); continue
        final, _ = _run_plan(plan, reg)
        produced = df_to_result(final)
        cls, reason, _tag = score(produced, g)
        if cls != "correct":
            failures.append(f"{qid}: comparator={cls} — {reason}")
    assert not failures, "T5 reference plans do not reproduce gold:\n  " + "\n  ".join(failures)


def test_S34_T5_reference_plan_length_equals_complexity_ops():
    from evidence.gold.t5_reference_plans import T5_REFERENCE_PLANS
    corpus_by_id = {q["id"]: q for q in _corpus()["queries"]}
    for qid, plan in T5_REFERENCE_PLANS.items():
        c = corpus_by_id[qid]["complexity_ops"]
        assert len(plan) == c, f"{qid}: plan length {len(plan)} != complexity_ops {c}"


def test_S34_T5_reference_plan_steps_are_non_redundant():
    """Each step must MATERIALLY transform the frame (no-op padding forbidden).
    Order-sensitive fingerprint catches filter-removing-0-rows, no-column-changing
    selects, sorts that change nothing, limits that drop nothing."""
    from evidence.gold.t5_reference_plans import T5_REFERENCE_PLANS
    reg = _register()
    failures = []
    for qid, plan in T5_REFERENCE_PLANS.items():
        _, snapshots = _run_plan(plan, reg)
        for i in range(1, len(snapshots)):
            before, after = snapshots[i - 1], snapshots[i]
            if _frame_fingerprint(before) == _frame_fingerprint(after):
                failures.append(f"{qid} step {i-1} ({plan[i-1]['op']}): no-op (redundant)")
    assert not failures, "redundant steps detected:\n  " + "\n  ".join(failures)


def test_S34_T5_reference_plans_one_per_query():
    """Each T5 query in the corpus has a reference plan; no orphans either side."""
    from evidence.gold.t5_reference_plans import T5_REFERENCE_PLANS
    corpus_ids = {q["id"] for q in _t5_qs()}
    plan_ids = set(T5_REFERENCE_PLANS)
    assert corpus_ids == plan_ids, f"mismatch — only-corpus={corpus_ids-plan_ids}  only-plans={plan_ids-corpus_ids}"


# ── Part C cont.: complexity_ops propagates from gold into result records ────

def test_S34_complexity_ops_flows_into_results_record():
    """run_cell must stamp complexity_ops on every record (recoverable from results.jsonl
    for the scaling curve). Default None is acceptable for tiers without a stamp."""
    from evidence.eval_run.harness import run_cell

    class _Fake:
        name, model = "network", "claude-sonnet-4-5"
        def run(self, q, s):
            return {"result": 1.0, "status": "completed", "llm_calls": 1,
                    "input_tokens": 100, "uncached_input_tokens": 100,
                    "cache_write_tokens": 0, "cache_read_tokens": 0, "output_tokens": 10,
                    "per_call_tokens": [], "wall_time_s": 0.0, "error": None, "artifact": {}}
    g = {"id": "t1_06", "tier": "T1_single_op", "expected_behavior": "answer",
         "result_kind": "scalar", "value": 1.0, "complexity_ops": 1}
    rec = run_cell(_Fake(), "t1_06", "q?", 0, g, model="claude-sonnet-4-5")
    assert rec["complexity_ops"] == 1


# ── Part D: self-test extension (T5 covered by the existing gold-vs-itself loop) ──

def test_S34_self_consistency_includes_T5():
    """Gold-vs-itself for every T5 entry must score correct — the existing battery
    test_self_consistency_every_gold_entry already runs on all gold; this asserts a
    T5 subset is now part of that gold."""
    import copy
    from evidence.eval_run.comparator import score
    gold = _gold()
    t5_ids = [qid for qid, g in gold.items() if g.get("tier") == "T5_high_complexity"]
    assert len(t5_ids) >= 15, f"only {len(t5_ids)} T5 gold entries"
    failures = []
    for qid in t5_ids:
        g = gold[qid]
        cls, reason, _ = score(copy.deepcopy(g["value"]), g, status="completed")
        if cls != "correct":
            failures.append(f"{qid}: {cls} — {reason}")
    assert not failures, "T5 gold-vs-itself failed:\n  " + "\n  ".join(failures)
