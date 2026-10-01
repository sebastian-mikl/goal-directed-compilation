"""S21 evidence harness — runs (system, query, seed) cells with streaming +
resumable logging. Used by both the smoke run and (eventually) the full run.

Per-cell flow:
  1. SKIP if already in results.jsonl (resume).
  2. Construct the system's adapter (once per system, reused across queries).
  3. Call adapter.run(query, seed); catch any unhandled exception as crash.
  4. Score with the shared comparator against gold[qid].
  5. Append one JSON line containing EVERYTHING (ids, seed, system, artifact,
     calls, tokens (+per-call), $ cost, wall_time, raw result, gold value,
     verdict, outcome_class, failure_tag).
"""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

import pandas as pd

from .adapters import ADAPTER_CLASSES
from .adapters.base import Adapter, estimate_tokens, is_transport_error
from .briefing import load_briefing
from .comparator import score, COMPARATOR_VERSION
from .emission_config import EMISSION_SYSTEMS, resolve_emission_model
from .refuse_config import refuse_unsupported_enabled, REFUSE_PROMPT_TAG
from gdc.llm import GOAL_PROMPT_VERSION
from gdc.contract import CONTRACT_VERSION
from .logger import CachingClient, JsonlLogger, write_provenance
from .pricing import (PRICING_USD_PER_MTOK, cache_multipliers, cost_usd,
                      cost_usd_cached, min_cacheable_tokens)
from .types import REFUSAL


ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {
    "corpus_path":   ROOT / "eval_corpus.json",
    "gold_path":     ROOT / "evidence" / "gold" / "gold.json",
    "register_path": ROOT / "environment" / "register.pkl",
    "schema_path":   ROOT / "environment" / "schema.json",
    "results_path":  ROOT / "evidence" / "eval_run" / "results.jsonl",
    "provenance_path": ROOT / "evidence" / "eval_run" / "provenance.json",
    "model":         "claude-sonnet-4-5",
    "temperature":   None,            # API default — recorded as "default"
    "use_cache":     True,            # S21a prompt caching (toggle off for V-equivalence)
    "bounds": {"react_max_iters": 15, "code_max_retries": 3, "per_query_timeout_s": 60},
}


def load_corpus(path: Path) -> dict:
    return {q["id"]: q for q in json.loads(Path(path).read_text())["queries"]}


def load_gold(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def check_provenance_drift(provenance_path: Path, results_path: Path, *,
                           model: str, emission_model: str | None,
                           network_prompt_version: str,
                           refuse_unsupported: bool,
                           contract_version: str | None,
                           corpus_path: Path, gold_path: Path,
                           prompts: dict[str, str]) -> None:
    """S36 FIX 3b — refuse to resume an existing results.jsonl whose provenance
    has drifted on integrity-critical fields. The harness used to write provenance
    only on a fresh run and silently leave it untouched on resume, so re-running
    under a changed toggle/model/prompt would produce a results.jsonl mixing cells
    from different configs with stale provenance and no audit trail. Now: read
    the existing provenance and exit cleanly with a diagnosis listing the drifted
    fields. The architect must explicitly start a fresh results file.

    `comparator_version` is DELIBERATELY excluded — rescore repairs comparator
    drift after the fact (the architect just bumped s26→s36), and including it
    would block every otherwise-valid resume after any comparator change."""
    import hashlib
    try:
        old = json.loads(Path(provenance_path).read_text())
    except Exception as exc:                                      # noqa: BLE001
        raise SystemExit(
            f"[resume guard] cannot read existing provenance {provenance_path}: "
            f"{type(exc).__name__}: {exc}")

    def _sha(p):
        try: return hashlib.sha256(Path(p).read_bytes()).hexdigest()
        except Exception: return None                              # noqa: BLE001

    current = {"model": model, "emission_model": emission_model,
               "network_prompt_version": network_prompt_version,
               "refuse_unsupported": refuse_unsupported,
               "contract_version": contract_version,
               "corpus_sha256": _sha(corpus_path),
               "gold_sha256": _sha(gold_path),
               "prompts": prompts}
    diffs = []
    for k, new_v in current.items():
        old_v = old.get(k)
        if k == "prompts":
            moved = sorted(s for s in new_v if old_v and old_v.get(s) != new_v.get(s))
            if moved:
                diffs.append(f"prompts changed for: {moved}")
            continue
        if old_v != new_v:
            diffs.append(f"{k}: old={old_v!r} new={new_v!r}")
    if diffs:
        raise SystemExit(
            "[resume guard] refusing to resume — provenance has drifted on "
            "integrity-critical fields:\n  " + "\n  ".join(diffs) +
            f"\nStart a fresh results file (delete {provenance_path} and "
            f"{results_path}, or pass new --results/--provenance paths).")


def build_adapters(client, register, briefing, *, model, temperature, bounds,
                   emission_model: str | None = None,
                   schema_path: Path | None = None) -> dict[str, Adapter]:
    """`emission_model` (S33): the Call-1 model for the EMISSION_SYSTEMS (network,
    planner) only; all others keep `model`. Default None -> emission_model == model,
    so every adapter uses `model` exactly as before (byte-identical baseline).
    `schema_path`: only meaningful to the "network" adapter, which independently
    re-reads schema.json in alias space (build_bridge/build_briefing); forwarded
    when given, else NetworkAdapter keeps its own default (environment/schema.json).
    The other adapters get their schema/domain context from `briefing` only."""
    emission_model = emission_model or model
    out = {}
    for name, cls in ADAPTER_CLASSES.items():
        extra = {"schema_path": schema_path} if name == "network" and schema_path is not None else {}
        out[name] = cls(
            name=name, client=client, register=register, briefing=briefing,
            model=(emission_model if name in EMISSION_SYSTEMS else model),
            temperature=temperature,
            max_iters=bounds["react_max_iters"],
            max_retries=bounds["code_max_retries"],
            timeout_s=bounds.get("per_query_timeout_s"),
            **extra,
        )
    return out


def collect_prompts(adapters: dict[str, Adapter]) -> dict[str, str]:
    """Record every system's EXACT static cacheable prefix verbatim (the text that
    becomes the cached block), so provenance reflects precisely what is sent. For
    the four non-network adapters this is their full system prompt; for the network
    adapter it is the prefix llm1 actually emits (captured at construction)."""
    from .briefing import render_briefing
    out: dict[str, str] = {}
    for name, ad in adapters.items():
        try:
            out[name] = ad.cache_prefix_text()
        except Exception as exc:                          # noqa: BLE001
            out[name] = f"<unavailable: {exc}>"
    a0 = next(iter(adapters.values()))
    out["_shared_briefing"] = render_briefing(a0.briefing)
    return out


def cache_size_check(adapters: dict[str, Adapter], model: str) -> list[dict]:
    """Estimate each system's static cached-prefix token size (tools + system, the
    actual cache hierarchy) and flag any below the model's minimum cacheable size
    (where caching silently won't engage)."""
    out = []
    for name, ad in adapters.items():
        floor = min_cacheable_tokens(getattr(ad, "model", None) or model)   # per-adapter (S33)
        prefix = ad.cache_prefix_text()
        tools = ad.cache_tools()
        tool_chars = len(json.dumps(tools)) if tools else 0
        est = estimate_tokens(prefix) + (estimate_tokens(json.dumps(tools)) if tools else 0)
        out.append({"system": name, "system_chars": len(prefix), "tool_chars": tool_chars,
                    "est_tokens": est, "min_cacheable": floor, "below_min": est < floor})
    return out


def run_cell(adapter: Adapter, qid: str, query: str, seed: int,
             gold_record: dict, *, model: str) -> dict:
    """One (system, qid, seed) cell. Returns the JSONL record."""
    t0 = time.perf_counter()
    try:
        result = adapter.run(query, seed)
    except Exception as exc:                                  # noqa: BLE001
        if is_transport_error(exc):
            # Transient API/transport failure (e.g. credits exhausted, rate limit):
            # propagate so run_cells aborts WITHOUT persisting this cell — on resume
            # it retries instead of being skipped as a recorded "crashed" cell.
            raise
        result = {
            "result": REFUSAL, "llm_calls": 0, "input_tokens": 0,
            "uncached_input_tokens": 0, "cache_write_tokens": 0,
            "cache_read_tokens": 0, "output_tokens": 0,
            "per_call_tokens": [], "wall_time_s": time.perf_counter() - t0,
            "artifact": {"kind": adapter.name, "exception": traceback.format_exc()},
            "status": "crashed", "error": f"{type(exc).__name__}: {exc}",
        }
    verdict, reason, tag = score(result["result"], gold_record, status=result["status"])
    raw_in = result["input_tokens"]                       # uncached + cache_write + cache_read
    out_tok = result["output_tokens"]
    pm = getattr(adapter, "model", None) or model         # price by the model the adapter USED (S33)
    cost_raw = cost_usd(pm, raw_in, out_tok)             # cache-blind (apples-to-apples)
    cost_cached = cost_usd_cached(pm, result["uncached_input_tokens"],
                                  result["cache_write_tokens"],
                                  result["cache_read_tokens"], out_tok)  # real bill
    return {
        "qid": qid, "seed": int(seed), "system": adapter.name,
        "query": query,
        "tier": gold_record["tier"],
        "expected_behavior": gold_record["expected_behavior"],
        "result_kind": gold_record["result_kind"],
        "complexity_ops": gold_record.get("complexity_ops"),    # S34 — scaling curve axis
        # adapter output
        "raw_result": result["result"],
        "status": result["status"],
        "error": result["error"],
        "artifact": result["artifact"],
        "llm_calls": result["llm_calls"],
        # four-category token ledger (S21a)
        "input_tokens": raw_in,                           # RAW logical = uncached+write+read
        "uncached_input_tokens": result["uncached_input_tokens"],
        "cache_write_tokens": result["cache_write_tokens"],
        "cache_read_tokens": result["cache_read_tokens"],
        "output_tokens": out_tok,
        "per_call_tokens": result["per_call_tokens"],
        "wall_time_s": round(float(result["wall_time_s"]), 4),
        "cost_usd_raw": round(cost_raw, 6),               # cache-blind $
        "cost_usd_cached": round(cost_cached, 6),         # cache-aware $ (real bill)
        "cost_usd": round(cost_cached, 6),                # alias = real bill
        # gold + scoring
        "gold_value": gold_record["value"],
        "comparator_verdict": verdict,
        "comparator_reason":  reason,
        "outcome_class":      verdict,
        "failure_tag":        tag,
    }


def run_cells(*, qids: list[str], seeds: list[int], systems: list[str],
              client_factory, model: str = DEFAULTS["model"],
              emission_model: str | None = None,
              temperature: float | None = DEFAULTS["temperature"],
              use_cache: bool = DEFAULTS["use_cache"],
              bounds: dict | None = None,
              corpus_path: Path = DEFAULTS["corpus_path"],
              gold_path: Path = DEFAULTS["gold_path"],
              register_path: Path = DEFAULTS["register_path"],
              schema_path: Path = DEFAULTS["schema_path"],
              results_path: Path = DEFAULTS["results_path"],
              provenance_path: Path = DEFAULTS["provenance_path"],
              progress=print) -> dict:
    """Run a set of (system, qid, seed) cells. Skips any cell already logged.
    `client_factory()` returns a fresh Anthropic-shaped client, wrapped in the
    CachingClient (which marks the static prefix with cache_control when
    use_cache=True and captures the four-category token ledger).

    LOOP ORDER is SYSTEM-OUTER: all (query, seed) cells for one system run
    consecutively, so that system's cached prefix stays warm across its calls
    (ephemeral cache TTL is short). Resume is order-independent (the skip-completed
    logic keys on (system, qid, seed), not position)."""
    bounds = bounds or DEFAULTS["bounds"]
    emission_model = resolve_emission_model(model, override=emission_model)   # S33 knob
    corpus = load_corpus(corpus_path)
    gold = load_gold(gold_path)
    register = pd.read_pickle(register_path)
    briefing = load_briefing(schema_path, register_path)
    client = CachingClient(client_factory(), cache=use_cache)
    adapters = build_adapters(client, register, briefing,
                              model=model, temperature=temperature, bounds=bounds,
                              emission_model=emission_model, schema_path=schema_path)
    logger = JsonlLogger(results_path)

    # Minimum-cacheable-size check — warn (don't fail) if a static prefix is too
    # small for caching to engage.
    size_check = cache_size_check(adapters, model)
    for s in size_check:
        if use_cache and s["below_min"]:
            progress(f"[cache WARNING] {s['system']} static prefix ~{s['est_tokens']} tok "
                     f"< min cacheable {s['min_cacheable']} — caching may not engage")

    current_network_prompt_version = (GOAL_PROMPT_VERSION +
        ("+" + REFUSE_PROMPT_TAG if refuse_unsupported_enabled() else ""))
    current_prompts = collect_prompts(adapters)
    if not provenance_path.exists():
        write_provenance(provenance_path,
                         model=model, temperature=temperature,
                         seeds=seeds, bounds=bounds,
                         pricing=PRICING_USD_PER_MTOK,
                         corpus_path=corpus_path, gold_path=gold_path,
                         prompts=current_prompts,
                         cache={"enabled": use_cache,
                                "control": "ephemeral on last system block (caches tools+system)",
                                "multipliers": cache_multipliers(),
                                "min_cacheable_tokens": min_cacheable_tokens(model),
                                "prefix_sizes": size_check,
                                "note": ("uniform infra optimization applied identically to all 5 "
                                         "systems; lowers $ but NOT raw token / call counts. Paper "
                                         "cost metrics = calls + RAW tokens (cache-blind); "
                                         "cost_usd_cached is the practical bill.")},
                         comparator_version=COMPARATOR_VERSION,
                         network_prompt_version=current_network_prompt_version,
                         refuse_unsupported=refuse_unsupported_enabled(),
                         emission_model=emission_model,
                         contract_version=CONTRACT_VERSION)
    else:
        check_provenance_drift(provenance_path, results_path,
                               model=model, emission_model=emission_model,
                               network_prompt_version=current_network_prompt_version,
                               refuse_unsupported=refuse_unsupported_enabled(),
                               contract_version=CONTRACT_VERSION,
                               corpus_path=corpus_path, gold_path=gold_path,
                               prompts=current_prompts)

    summary = {"ran": 0, "skipped": 0, "aborted": False, "abort": None,
               "by_system": {s: 0 for s in systems}}
    for sys_name in systems:                       # SYSTEM-OUTER (cache warmth)
        adapter = adapters[sys_name]
        for qid in qids:
            for seed in seeds:
                if logger.has(sys_name, qid, seed):
                    summary["skipped"] += 1
                    continue
                q = corpus[qid]
                try:
                    rec = run_cell(adapter, qid, q["question"], seed, gold[qid], model=model)
                except Exception as exc:                       # noqa: BLE001
                    # Only a transport/API error reaches here (run_cell records every
                    # other failure as a status=crashed cell and returns normally).
                    # Abort the run WITHOUT emitting this cell so resume retries it.
                    summary["aborted"] = True
                    summary["abort"] = {"system": sys_name, "qid": qid, "seed": int(seed),
                                        "error": f"{type(exc).__name__}: {exc}"}
                    progress(f"\n[ABORT] transport/API error on "
                             f"[{sys_name} {qid} s{seed}]: {type(exc).__name__}: {exc}")
                    progress(f"  {summary['ran']} cell(s) completed this run and saved; "
                             f"this cell was NOT recorded.")
                    progress(f"  To resume: fix the cause (e.g. top up credits / wait out the "
                             f"rate limit), then re-run the SAME command — completed cells are "
                             f"skipped and this cell retries.")
                    return summary
                logger.emit(rec)
                summary["ran"] += 1
                summary["by_system"][sys_name] += 1
                progress(f"[{sys_name:11s} {qid:>6s} s{seed}] "
                         f"{rec['outcome_class']:<22s} calls={rec['llm_calls']:>2d} "
                         f"raw_in={rec['input_tokens']:>5d} (u{rec['uncached_input_tokens']}/"
                         f"w{rec['cache_write_tokens']}/r{rec['cache_read_tokens']}) "
                         f"out={rec['output_tokens']:>4d} ${rec['cost_usd']:.4f} "
                         f"{rec['wall_time_s']:5.2f}s -- {rec['comparator_reason'][:50]}")
    return summary
