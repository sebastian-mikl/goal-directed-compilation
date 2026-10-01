"""S33 — configurable Call-1 emission model for the downsizing probe (network + planner).

A single knob (EMISSION_MODEL env / run.py --emission-model) sets the model used for the
network's and planner's goal/plan emission call. Default (unset) is byte-identical to today
(Sonnet for all five systems). Only the model string changes — no prompt text, no other
call site, no engine/comparator/corpus/gold. Deterministic; no API.
"""
from __future__ import annotations

import json
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

SONNET = "claude-sonnet-4-5"
HAIKU = "claude-haiku-4-5"
BOUNDS = {"react_max_iters": 4, "code_max_retries": 4}


def _register():
    return pd.read_pickle(ROOT / "environment" / "register.pkl")


def _briefing():
    from evidence.eval_run.briefing import load_briefing
    return load_briefing(ROOT / "environment" / "schema.json", ROOT / "environment" / "register.pkl")


class _Client:
    class _Msgs:
        def create(self, **kw):
            return type("R", (), {"content": [type("T", (), {"type": "text", "text": "{}"})()],
                                  "usage": None})()
    def __init__(self): self.messages = self._Msgs()


def _adapters(emission_model=None):
    from evidence.eval_run.harness import build_adapters
    return build_adapters(_Client(), _register(), _briefing(),
                          model=SONNET, temperature=None, bounds=BOUNDS,
                          emission_model=emission_model)


# ── resolution (red-first) ───────────────────────────────────────────────────

def test_S33_unset_knob_resolves_to_default_sonnet(monkeypatch):
    monkeypatch.delenv("EMISSION_MODEL", raising=False)
    from evidence.eval_run.emission_config import resolve_emission_model
    assert resolve_emission_model(SONNET) == SONNET


def test_S33_env_knob_selects_haiku(monkeypatch):
    monkeypatch.setenv("EMISSION_MODEL", HAIKU)
    from evidence.eval_run.emission_config import resolve_emission_model
    assert resolve_emission_model(SONNET) == HAIKU


def test_S33_explicit_override_beats_env(monkeypatch):
    monkeypatch.setenv("EMISSION_MODEL", HAIKU)
    from evidence.eval_run.emission_config import resolve_emission_model
    assert resolve_emission_model(SONNET, override=SONNET) == SONNET


def test_S33_unpriced_emission_model_is_rejected(monkeypatch):
    monkeypatch.delenv("EMISSION_MODEL", raising=False)
    from evidence.eval_run.emission_config import resolve_emission_model
    with pytest.raises(KeyError):
        resolve_emission_model(SONNET, override="gpt-4o-mini")     # never silently free


# ── build_adapters: default intact; Haiku only on emission systems ───────────

def test_S33_default_path_all_systems_use_sonnet(monkeypatch):
    monkeypatch.delenv("EMISSION_MODEL", raising=False)
    ads = _adapters()                                               # no emission_model
    for name, ad in ads.items():
        assert ad.model == SONNET, f"{name} default model changed to {ad.model}"


def test_S33_haiku_applies_only_to_network_and_planner():
    ads = _adapters(emission_model=HAIKU)
    assert ads["network"].model == HAIKU
    assert ads["planner"].model == HAIKU
    for s in ("react", "code_agent", "text_to_sql"):
        assert ads[s].model == SONNET, f"{s} wrongly switched to {ads[s].model}"


def test_S33_emission_systems_constant():
    from evidence.eval_run.emission_config import EMISSION_SYSTEMS
    assert set(EMISSION_SYSTEMS) == {"network", "planner"}


# ── integrity: no prompt text changes, pricing follows the model ─────────────

def test_S33_emission_model_does_not_change_prompt_text():
    s, h = _adapters(emission_model=SONNET), _adapters(emission_model=HAIKU)
    for sys in ("network", "planner"):
        assert s[sys].cache_prefix_text() == h[sys].cache_prefix_text(), \
            f"{sys} prompt text changed with the emission model"


def test_S33_run_cell_prices_by_adapter_model():
    """A cell whose adapter.model is Haiku must be priced at Haiku (cheaper than Sonnet),
    not the run's global model."""
    from evidence.eval_run.harness import run_cell
    from evidence.eval_run.pricing import cost_usd

    class _Fake:
        name, model = "network", HAIKU
        def run(self, query, seed):
            return {"result": 42.0, "status": "completed", "llm_calls": 1,
                    "input_tokens": 2000, "uncached_input_tokens": 2000,
                    "cache_write_tokens": 0, "cache_read_tokens": 0, "output_tokens": 200,
                    "per_call_tokens": [], "wall_time_s": 0.1, "error": None, "artifact": {}}
    gold = {"tier": "T1", "expected_behavior": "answer", "result_kind": "scalar", "value": 42.0}
    rec = run_cell(_Fake(), "t1_x", "q", 0, gold, model=SONNET)     # global says Sonnet...
    assert rec["cost_usd_raw"] == pytest.approx(cost_usd(HAIKU, 2000, 200))   # ...priced Haiku
    assert rec["cost_usd_raw"] != pytest.approx(cost_usd(SONNET, 2000, 200))


def test_S33_provenance_records_resolved_emission_model(tmp_path):
    from evidence.eval_run.logger import write_provenance
    p = tmp_path / "prov.json"
    write_provenance(p, model=SONNET, temperature=None, seeds=[0], bounds=BOUNDS,
                     pricing={SONNET: {"input": 3.0, "output": 15.0}},
                     corpus_path=ROOT / "eval_corpus.json",
                     gold_path=ROOT / "evidence" / "gold" / "gold.json",
                     prompts={}, emission_model=HAIKU)
    prov = json.loads(p.read_text())
    assert prov["emission_model"] == HAIKU
