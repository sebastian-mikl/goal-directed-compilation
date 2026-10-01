"""Temperature-0 overlays of the paper pipeline (scripts/parity_overlay.py).

The parity overlay replaces baseline rows only; a re-run file (the second batch's
results_temp0_batch2_<domain>.jsonl) replaces rows of any system.
A batch file that is absent changes nothing. A key held by two overlay files is a
hard error unless SUPERSEDES says which wins. A batch2 file is used only whole (its
provenance's plan keys, exactly), clean (no guard, billing or harness-crash row) and
from the engine the build runs, as PORTED_ENGINE pins it (PARTIAL_BATCH2 admits a
strict subset of the plan and says so). Synthetic files only (no eval data).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import parity_overlay as po

SRC = "results_x.jsonl"
PARITY_FN = "results_temp0_parity_x.jsonl"
RERUN_FN = "results_temp0_rerun_x.jsonl"
BATCH_FN = "results_temp0_batch2_x.jsonl"


def _row(system, qid, seed, tag):
    return {"system": system, "qid": qid, "seed": seed, "raw_result": tag}


def _write(root: Path, fn: str, rows, temperature=0, provenance=True):
    (root / fn).write_text("".join(json.dumps(r) + "\n" for r in rows))
    if provenance:
        po.provenance_path(fn, root).write_text(json.dumps({"temperature": temperature}))


RECORDED = [_row("network", "q1", 0, "rec"), _row("planner", "q1", 0, "rec"),
            _row("react", "q1", 0, "rec"), _row("code_agent", "q1", 0, "rec"),
            _row("code_agent", "q2", 0, "rec")]


def _overlay(root, batch=True, supersedes=True, read_systems=None):
    rerun = (RERUN_FN, BATCH_FN) if batch else (RERUN_FN,)
    sup = {BATCH_FN: (PARITY_FN, RERUN_FN)} if supersedes else {}
    return po.Overlay(root=root, enabled=True, overlay_map={SRC: PARITY_FN},
                      rerun_map={SRC: rerun}, supersedes=sup, read_systems=read_systems or {})


def _tags(rows):
    return {(r["system"], r["qid"]): r["raw_result"] for r in rows}


@pytest.fixture
def root(tmp_path):
    # parity: two baseline cells (and a network row it must ignore); re-run: GDC + planner
    _write(tmp_path, PARITY_FN, [_row("react", "q1", 0, "parity"), _row("code_agent", "q1", 0, "parity"),
                                 _row("network", "q2", 0, "parity")])
    _write(tmp_path, RERUN_FN, [_row("network", "q1", 0, "rerun"), _row("planner", "q1", 0, "rerun")])
    return tmp_path


def test_disabled_returns_rows_unchanged(root):
    ov = po.Overlay(root=root, enabled=False, overlay_map={SRC: PARITY_FN}, rerun_map={SRC: (RERUN_FN,)})
    assert ov.apply(SRC, RECORDED) is RECORDED


def test_parity_replaces_baselines_and_rerun_replaces_any_system(root):
    out = _overlay(root).apply(SRC, RECORDED)
    assert _tags(out) == {("network", "q1"): "rerun", ("planner", "q1"): "rerun",
                          ("react", "q1"): "parity", ("code_agent", "q1"): "parity",
                          ("code_agent", "q2"): "rec"}
    assert all(r["overlay_source"] in (PARITY_FN, RERUN_FN) for r in out if r["raw_result"] != "rec")


def test_absent_batch_file_changes_nothing(root):
    with_slot = _overlay(root, batch=True)
    without = _overlay(root, batch=False)
    assert with_slot.apply(SRC, RECORDED) == without.apply(SRC, RECORDED)
    assert with_slot.provenance_rows() == without.provenance_rows()
    with_slot.check()                                       # absent file: nothing unplaced
    assert with_slot.rerun_note(with_slot.apply(SRC, RECORDED)) == f" [temp-0 re-run: {RERUN_FN}]"
    assert f"not present (no effect): {BATCH_FN}" in with_slot.coverage_report()


def test_batch_file_slots_in_and_supersedes_by_declaration(root):
    _write(root, BATCH_FN, [_row("code_agent", "q1", 0, "batch"), _row("network", "q1", 0, "batch"),
                            _row("code_agent", "q2", 0, "batch")])
    ov = _overlay(root)
    out = ov.apply(SRC, RECORDED)
    assert _tags(out) == {("network", "q1"): "batch", ("planner", "q1"): "rerun",
                          ("react", "q1"): "parity", ("code_agent", "q1"): "batch",
                          ("code_agent", "q2"): "batch"}
    assert (SRC, BATCH_FN, "code_agent", "q1", 0) in ov.provenance_rows()
    assert ov._superseded[PARITY_FN] == {("code_agent", "q1", 0)}
    assert ov._superseded[RERUN_FN] == {("network", "q1", 0)}
    ov.check()
    rep = ov.coverage_report()
    assert "superseded by a re-run: 1" in rep and f"{BATCH_FN:<34} [present] replaced=   3" in rep
    assert ov.rerun_note(out) == f" [temp-0 re-run: {BATCH_FN}, {RERUN_FN}]"


def test_shared_key_without_precedence_is_an_error(root):
    _write(root, BATCH_FN, [_row("code_agent", "q1", 0, "batch")])
    with pytest.raises(RuntimeError, match="no declared precedence"):
        _overlay(root, supersedes=False).apply(SRC, RECORDED)


def test_parity_and_rerun_sharing_a_key_is_an_error(root):
    _write(root, RERUN_FN, [_row("react", "q1", 0, "rerun")])
    with pytest.raises(RuntimeError, match="two temperature-0 sources"):
        _overlay(root, batch=False).apply(SRC, RECORDED)


def test_overlay_needs_provenance_at_temperature_zero(root):
    _write(root, BATCH_FN, [_row("code_agent", "q2", 0, "batch")], provenance=False)
    with pytest.raises(RuntimeError, match="provenance"):
        _overlay(root).apply(SRC, RECORDED)
    _write(root, BATCH_FN, [_row("code_agent", "q2", 0, "batch")], temperature=1.0)
    with pytest.raises(RuntimeError, match="temperature"):
        _overlay(root).apply(SRC, RECORDED)


def test_rerun_cell_that_replaces_nothing_fails_the_check(root):
    _write(root, BATCH_FN, [_row("code_agent", "q9", 0, "batch")])
    ov = _overlay(root)
    ov.apply(SRC, RECORDED)
    with pytest.raises(RuntimeError, match="match no recorded cell"):
        ov.check()


def test_rerun_cell_of_a_system_the_builder_does_not_read_there_fails_the_check(root):
    # e.g. a GDC row keyed onto a Spider five-system file, whose GDC rows no number reads
    _write(root, RERUN_FN, [_row("planner", "q1", 0, "rerun")])
    _write(root, BATCH_FN, [_row("network", "q1", 0, "batch")])
    ov = _overlay(root, read_systems={SRC: {"planner", "react", "code_agent", "text_to_sql"}})
    out = ov.apply(SRC, RECORDED)
    assert _tags(out)[("network", "q1")] == "rec" and _tags(out)[("planner", "q1")] == "rerun"
    with pytest.raises(RuntimeError, match=f"{BATCH_FN}: 1 re-run cells match no recorded cell"):
        ov.check()


def test_module_maps_a_batch_file_per_domain_onto_the_builders_sources():
    for dom, srcs in po.DOMAIN_SOURCES.items():
        fn = po.batch2_file(dom)
        assert fn == f"results_temp0_batch2_{dom}.jsonl"
        for src in srcs:
            assert fn in po.RERUN_MAP[src]
        assert po.PARITY_FILE[dom] in po.SUPERSEDES[fn]
        assert po.SUPERSEDES[fn] == (po.PARITY_FILE[dom],)
    # the withheld risk domain has no batch file here
    assert "risk" not in po.DOMAIN_SOURCES and not hasattr(po, "RISK_RERUN_S31")
    assert set(po.BATCH2_FILES) == {po.batch2_file(d) for d in ("employee", "wta_1", "world_1", "bike_1")}
    # Spider GDC cells live in the postfix files and are never re-run
    assert not any("postfix" in src for src in po.RERUN_MAP)
    for d in ("wta_1", "world_1", "bike_1"):
        assert "network" not in po.READ_SYSTEMS[f"results_spider_{d}.jsonl"]


# ── a batch2 file is used only whole, clean and from this engine ─────────────

PLAN = [("code_agent", "q1", 0), ("code_agent", "q2", 0), ("network", "q1", 0)]
# The engine a batch records is the development tree's (other paths, other bytes);
# PORTED_ENGINE pairs it with this tree's.
RECORDED_ENGINE = {"gdc/primitives.py": "a" * 64, "planner/contract.py": "b" * 64}


def _pin(recorded=RECORDED_ENGINE, current=None):
    return {"recorded_engine_sha256": po.engine_sha(recorded),
            "engine_sha256": po.engine_sha(po.engine_hashes() if current is None else current)}


def _batch(root, rows, plan=PLAN, engine=None, plan_keys=True):
    (root / BATCH_FN).write_text("".join(json.dumps(r) + "\n" for r in rows))
    b2 = {"fixed_code": {"sha256": RECORDED_ENGINE if engine is None else engine}}
    if plan_keys:
        b2["plan_keys"] = [list(k) for k in plan]
    po.provenance_path(BATCH_FN, root).write_text(json.dumps({"temperature": 0, "batch2": b2}))


def _checked(root, partial_ok=False, pin=None):
    sup = {BATCH_FN: (PARITY_FN, RERUN_FN)}
    return po.Overlay(root=root, enabled=True, overlay_map={SRC: PARITY_FN},
                      rerun_map={SRC: (RERUN_FN, BATCH_FN)}, supersedes=sup, read_systems={},
                      partial_ok=partial_ok, batch2_files={BATCH_FN},
                      ported_engine=_pin() if pin is None else pin)


WHOLE = [_row(s, q, sd, "batch") for s, q, sd in PLAN]


def test_a_whole_clean_batch_on_this_engine_is_used(root):
    _batch(root, WHOLE)
    ov = _checked(root)
    out = ov.apply(SRC, RECORDED)
    assert _tags(out)[("code_agent", "q2")] == "batch"
    ov.check()
    assert "PARTIAL" not in ov.rerun_note(out)


def test_a_partial_batch_is_refused(root):
    _batch(root, WHOLE[:2])
    with pytest.raises(RuntimeError, match=r"2 of 3 planned cells.*PARTIAL_BATCH2=1"):
        _checked(root).apply(SRC, RECORDED)
    with pytest.raises(RuntimeError, match="2 of 3 planned cells"):
        _checked(root).check()                       # check_overlays() refuses it too


def test_partial_batch2_override_builds_and_says_so_in_the_source(root):
    _batch(root, WHOLE[:2])
    ov = _checked(root, partial_ok=True)
    out = ov.apply(SRC, RECORDED)
    ov.check()
    assert ov.rerun_note(out).endswith(f"[PARTIAL_BATCH2=1: {BATCH_FN} 2/3 planned cells]")
    assert f"PARTIAL_BATCH2=1: {BATCH_FN} holds 2 of its 3 planned cells" in ov.coverage_report()


def test_a_cell_outside_the_plan_is_refused_even_with_the_override(root):
    _batch(root, WHOLE + [_row("code_agent", "q1", 1, "batch")])
    with pytest.raises(RuntimeError, match="1 cells the batch did not plan"):
        _checked(root, partial_ok=True).apply(SRC, RECORDED)


def test_a_batch_without_plan_keys_is_refused(root):
    _batch(root, WHOLE, plan_keys=False)
    with pytest.raises(RuntimeError, match="records no batch2.plan_keys"):
        _checked(root).apply(SRC, RECORDED)


@pytest.mark.parametrize("bad, why", [
    ({"status": "crashed", "error": "RuntimeError: refusing to send: unrecognised system prompt"},
     "prompt-guard refusal"),
    ({"status": "crashed", "error": "BadRequestError: Your credit balance is too low"},
     "credit/billing error"),
    ({"status": "crashed", "error": "KeyError: 'x'", "artifact": {"kind": "code_agent",
                                                                  "exception": "Traceback ..."}},
     "harness-logged crash"),
])
def test_a_row_the_run_did_not_complete_is_refused(root, bad, why):
    _batch(root, [dict(WHOLE[0], **bad)] + WHOLE[1:])
    with pytest.raises(RuntimeError, match=f"1 rows are not completed cells.*{why}"):
        _checked(root, partial_ok=True).apply(SRC, RECORDED)


def test_an_adapter_crash_is_a_cell_outcome_not_an_incomplete_row():
    r = {"status": "crashed", "error": "step 2 ('order'): error applying order: ...",
         "artifact": {"kind": "planner_upfront", "plan": []}}
    assert po.incomplete_row(r) is None


def test_a_batch_from_other_engine_code_is_refused(root):
    other = dict(RECORDED_ENGINE, **{"gdc/primitives.py": "0" * 64})
    _batch(root, WHOLE, engine=other)
    with pytest.raises(RuntimeError, match=r"not the engine this tree is a port of"):
        _checked(root, partial_ok=True).apply(SRC, RECORDED)


def test_a_batch_is_refused_once_this_trees_engine_moves(root):
    _batch(root, WHOLE)
    moved = dict(po.engine_hashes(), **{"gdc/primitives.py": "0" * 64})
    with pytest.raises(RuntimeError, match=r"an engine file changed since the batch ran"):
        _checked(root, partial_ok=True, pin=_pin(current=moved)).apply(SRC, RECORDED)


def test_the_ported_engine_pin_is_this_trees_engine():
    """Fails whenever an engine file changes. Renew PORTED_ENGINE only for a port of the
    engine the batch ran on; otherwise its files no longer match the replayed engine."""
    assert po.PORTED_ENGINE["engine_sha256"] == po.engine_sha(po.engine_hashes())


def test_the_engine_is_the_shared_executor_planner_tools_harness_and_adapters():
    files = po.engine_files()
    for f in ("gdc/primitives.py", "gdc/contract.py", "gdc/search.py",
              "evidence/eval_run/tools_decl.py", "evidence/eval_run/tools_code.py",
              "evidence/eval_run/adapters/react.py", "evidence/eval_run/adapters/network.py"):
        assert f in files
    h = po.engine_hashes()
    assert po.engine_sha(h) == po.engine_sha(dict(reversed(list(h.items()))))   # order-free
    assert po.engine_sha(h) != po.engine_sha(dict(h, **{"gdc/order.py": "0" * 64}))
