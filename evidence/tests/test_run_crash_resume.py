"""S24 — full-run entry point + crash-safe resume.

Deterministic (no API key). Confirms:
  - is_transport_error classifies anthropic API/transport errors vs logic bugs;
  - a transport/API error mid-run ABORTS without persisting the in-flight cell,
    and resume retries exactly that cell (the "run out of credits halfway" path);
  - a NON-transport error is still recorded as a crashed cell and the run
    continues (one buggy cell never blocks the whole run);
  - the full-run qid selection (all / by-tier / explicit).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

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



# ── minimal Anthropic-shaped fakes ──────────────────────────────────────────

@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class _ToolUseBlock:
    name: str
    input: dict
    id: str = "tu"
    type: str = "tool_use"


@dataclass
class _Resp:
    content: list
    usage: _Usage = field(default_factory=_Usage)


def _anthropic_error(name="RateLimitError", msg="credits exhausted"):
    """A synthetic exception that mimics the anthropic SDK's error surface
    (module 'anthropic.*'), so is_transport_error recognises it without
    constructing a real SDK exception (whose ctor needs request/response args)."""
    cls = type(name, (Exception,), {"__module__": "anthropic._exceptions"})
    return cls(msg)


_FINAL = [_ToolUseBlock(name="final_answer", input={"code": 'result = df["Cost"].sum()'})]


# ── (1) classifier ──────────────────────────────────────────────────────────

def test_is_transport_error_classifies():
    from evidence.eval_run.adapters.base import is_transport_error
    assert is_transport_error(_anthropic_error("RateLimitError")) is True
    assert is_transport_error(_anthropic_error("APIConnectionError")) is True
    assert is_transport_error(_anthropic_error("AuthenticationError")) is True
    # logic bugs / fake-client exhaustion are NOT transport errors
    assert is_transport_error(ValueError("a logic bug")) is False
    assert is_transport_error(RuntimeError("out of canned responses")) is False
    assert is_transport_error(KeyError("col")) is False


# ── (2) crash-safe resume: abort without persisting, then resume retries ────

def test_transport_error_aborts_without_persisting_then_resumes(tmp_path):
    from evidence.eval_run import harness

    class _AbortMessages:
        """Succeeds on call 1 (first cell), raises a transport error on call 2."""
        def __init__(self): self.calls = 0
        def create(self, **kw):
            self.calls += 1
            if self.calls == 2:
                raise _anthropic_error("RateLimitError", "credits exhausted mid-run")
            return _Resp(content=_FINAL)

    class _AbortClient:
        def __init__(self): self.messages = _AbortMessages()

    results = tmp_path / "results.jsonl"; prov = tmp_path / "prov.json"
    summary = harness.run_cells(
        qids=["t1_07", "t1_06"], seeds=[0], systems=["code_agent"],
        client_factory=_AbortClient, results_path=results, provenance_path=prov,
        progress=lambda *_: None,
    )
    # aborted on the SECOND cell; first cell completed + saved
    assert summary["aborted"] is True
    assert summary["abort"]["qid"] == "t1_06"
    assert summary["ran"] == 1
    lines = [json.loads(l) for l in results.read_text().splitlines()]
    assert len(lines) == 1 and lines[0]["qid"] == "t1_07", \
        "the aborted cell must NOT be persisted (else resume would skip it)"

    # RESUME with a healthy client: t1_07 skipped (already done), t1_06 now runs
    class _Messages:
        def __init__(self): self.calls = 0
        def create(self, **kw):
            self.calls += 1
            return _Resp(content=_FINAL)

    class _OkClient:
        def __init__(self): self.messages = _Messages()

    summary2 = harness.run_cells(
        qids=["t1_07", "t1_06"], seeds=[0], systems=["code_agent"],
        client_factory=_OkClient, results_path=results, provenance_path=prov,
        progress=lambda *_: None,
    )
    assert summary2["aborted"] is False
    assert summary2["skipped"] == 1 and summary2["ran"] == 1
    lines2 = [json.loads(l) for l in results.read_text().splitlines()]
    assert {l["qid"] for l in lines2} == {"t1_07", "t1_06"}      # the gap is now filled


# ── (3) a non-transport error is recorded as crashed; the run CONTINUES ─────

def test_non_transport_error_recorded_as_crashed_and_continues(tmp_path):
    from evidence.eval_run import harness

    class _BugMessages:
        def create(self, **kw):
            raise ValueError("not an API error — a logic bug")

    class _BugClient:
        def __init__(self): self.messages = _BugMessages()

    results = tmp_path / "results.jsonl"; prov = tmp_path / "prov.json"
    summary = harness.run_cells(
        qids=["t1_07"], seeds=[0], systems=["planner"],
        client_factory=_BugClient, results_path=results, provenance_path=prov,
        progress=lambda *_: None,
    )
    assert summary["aborted"] is False           # NOT a transport error -> no abort
    assert summary["ran"] == 1
    line = json.loads(results.read_text().splitlines()[0])
    assert line["status"] == "crashed"           # recorded as a crashed cell


# ── (4) full-run qid selection ──────────────────────────────────────────────

def test_select_qids_all_by_tier_and_explicit():
    from evidence.eval_run.run import select_qids
    from evidence.eval_run.harness import load_corpus, DEFAULTS
    corpus = load_corpus(DEFAULTS["corpus_path"])
    assert len(select_qids(corpus)) == len(corpus)              # all (S26: 78 base + S34: 15 T5 = 93)
    t1 = select_qids(corpus, tiers=["T1_single_op"])
    assert len(t1) == 15 and all(corpus[q]["tier"] == "T1_single_op" for q in t1)
    alg = select_qids(corpus, tiers=["algebra_boundary"])      # risk's former out_of_range (2026-09-30)
    assert len(alg) == 10
    assert select_qids(corpus, queries=["t1_07", "t4_06"]) == ["t1_07", "t4_06"]
    with pytest.raises(SystemExit):
        select_qids(corpus, queries=["nope_99"])
