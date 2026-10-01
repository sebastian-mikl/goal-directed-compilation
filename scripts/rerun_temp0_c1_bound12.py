#!/usr/bin/env python
"""Close C-1 (the code_agent 4-call ceiling) in the TEMPERATURE-0 parity arm.

Why this run exists
-------------------
C-1 was fixed piecemeal at DEFAULT temperature for risk and Spider. But the
paper's canonical reports/paper_numbers.csv is built with PARITY_TEMP0=1, which
makes scripts/parity_overlay.py substitute every baseline code_agent row with its
temperature-0 counterpart. So for the numbers the paper actually cites, the
default-temperature C-1 fixes are inert -- the overlay replaces the very rows they
repaired, with rows still carrying the bug.

The paper states "temperature 0 throughout" (S4.2, Table 1 caption, Fig 8
caption) as part of the temperature-parity disclosure. Fixing C-1 at default
temperature for cells reported at temperature 0 would leave that methodological
claim false for exactly those cells. This run closes it at temperature 0 instead
of disclosing it as a residual.

Scope: the 94 cells that still carry the C-1 signature (status=timeout,
llm_calls=4) in the temp-0 parity files after the employee parity re-run (17
cells, already merged) is accounted for:
    results_temp0_parity_risk.jsonl     82   (44 T4 + 38 T5)
    results_temp0_parity_wta_1.jsonl     5   (wta_24, all seeds)
    results_temp0_parity_bike_1.jsonl    4   (bike_06)
    results_temp0_parity_world_1.jsonl   3   (world_19)

Bound: 12 for every domain. Justified by react's OBSERVED temperature-0 maxima
(risk 8, wta_1 5, world_1 5, bike_1 10) -- measured in the temp-0 parity files
themselves, not carried over from the default-temperature runs. The existing
bound=12 default-temp risk re-run terminated at max 8 calls / mean 4.99, never
approaching its own ceiling.

Output: a NEW results file + provenance. It does NOT write into the parity files
directly -- run_cells() skips any (system, qid, seed) already logged, so writing
there would skip all 94 and run nothing. scripts/merge_c1_reruns.py performs the
stale-only merge afterwards, archiving superseded rows.

Usage:
    python scripts/rerun_temp0_c1_bound12.py --dry     # scope + cost, no API
    python scripts/rerun_temp0_c1_bound12.py --run     # live
"""
from __future__ import annotations
import argparse, json, statistics, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                      # noqa: BLE001 — .env is optional
    pass

from evidence.eval_run.comparator import COMPARATOR_VERSION            # noqa: E402
assert COMPARATOR_VERSION == "s48", COMPARATOR_VERSION
from scripts.probe_temperature import DOMAINS, MODEL                   # noqa: E402

PARITY_TEMP = 0
SYSTEMS = ["code_agent"]
BOUNDS = {"react_max_iters": 15, "code_max_retries": 12, "per_query_timeout_s": 60}
C1 = ("timeout", 4)

# domain -> temp-0 parity file holding that domain's stale cells
PARITY_FILE = {
    "risk":    "results_temp0_parity_risk.jsonl",
    "wta_1":   "results_temp0_parity_wta_1.jsonl",
    "world_1": "results_temp0_parity_world_1.jsonl",
    "bike_1":  "results_temp0_parity_bike_1.jsonl",
}
# PER-DOMAIN output. run_cells' resume guard compares corpus_sha256/gold_sha256/
# prompts against the existing provenance and refuses to resume when they drift --
# correct behaviour, since each domain has its own corpus, gold and briefing. One
# shared results/provenance pair therefore lets only the FIRST domain run and
# blocks the rest, so each domain gets its own file.
def out_results(dom):
    return ROOT / f"results/results_temp0_parity_c1_bound12_rerun_{dom}.jsonl"


def out_prov(dom):
    return ROOT / f"results/results_temp0_parity_c1_bound12_rerun_{dom}.provenance.json"

PRICE_IN, PRICE_OUT = 3.0 / 1e6, 15.0 / 1e6
CACHE_WRITE_MULT, CACHE_READ_MULT = 1.25, 0.1


def _load(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def stale_cells():
    """The C-1-signature code_agent cells still present in each temp-0 parity file."""
    out = {}
    for dom, fn in PARITY_FILE.items():
        rows = _load(ROOT / "results" / fn)
        cells = defaultdict(list)
        for r in rows:
            if (r.get("system") == "code_agent"
                    and (r.get("status"), r.get("llm_calls")) == C1):
                cells[r["qid"]].append(r["seed"])
        out[dom] = {q: sorted(s) for q, s in sorted(cells.items())}
    return out


# ── cost model: per-call growth fit from the existing default-temp C-1 re-runs ──
def growth_fit(rerun_fn: str):
    bycall = defaultdict(list)
    for r in _load(ROOT / "results" / rerun_fn):
        for i, c in enumerate(r.get("per_call_tokens") or []):
            bycall[i].append(c)
    xs = sorted(bycall)
    unc = [statistics.mean(c.get("uncached", 0) for c in bycall[i]) for i in xs]
    slope = (unc[-1] - unc[0]) / max(1, xs[-1] - xs[0])
    return {
        "u0": unc[0], "slope": slope,
        "out": statistics.mean(c.get("out", 0) for i in xs for c in bycall[i]),
        "cread": statistics.mean(c.get("cache_read", 0) for i in xs for c in bycall[i]),
        "cwrite": statistics.mean(c.get("cache_write", 0) for c in bycall[xs[0]]),
        "calls": [r["llm_calls"] for r in _load(ROOT / "results" / rerun_fn)],
    }


def cell_cost(fit, ncalls, cached):
    tot = 0.0
    for i in range(ncalls):
        u = fit["u0"] + fit["slope"] * i
        if cached:
            tot += (u * PRICE_IN
                    + fit["cread"] * PRICE_IN * CACHE_READ_MULT
                    + (fit["cwrite"] if i == 0 else 0) * PRICE_IN * CACHE_WRITE_MULT
                    + fit["out"] * PRICE_OUT)
        else:
            tot += ((u + fit["cread"] + (fit["cwrite"] if i == 0 else 0)) * PRICE_IN
                    + fit["out"] * PRICE_OUT)
    return tot


def scope(cells):
    fits = {"risk": growth_fit("results_code_agent_bound12_rerun.jsonl"),
            "spider": growth_fit("results_spider_code_agent_bound12_rerun.jsonl")}
    # Spider provenance records ZERO caching savings for code_agent; risk caches.
    cached = {"risk": True, "wta_1": False, "world_1": False, "bike_1": False}
    print("=" * 88)
    print("TEMPERATURE-0 C-1 CLOSURE — code_agent, bound=12, temperature=0")
    print("=" * 88)
    tot_cells = 0
    real = worst = 0.0
    for dom, byq in cells.items():
        n = sum(len(s) for s in byq.values())
        if not n:
            continue
        tot_cells += n
        fit = fits["risk"] if dom == "risk" else fits["spider"]
        obs = statistics.mean(fit["calls"])
        real += n * cell_cost(fit, round(obs), cached[dom])
        worst += n * cell_cost(fit, BOUNDS["code_max_retries"] + 1, cached[dom])
        print(f"  {dom:<9} {len(byq):>2} qids, {n:>3} cells   "
              f"{'cached' if cached[dom] else 'no cache':<9}  "
              f"realistic {obs:.2f} calls/cell")
        for q, s in byq.items():
            print(f"      {q:<10} seeds={s}")
    print("-" * 88)
    print(f"  TOTAL {tot_cells} cells    realistic ~${real:.2f}    "
          f"worst-case (bound+1={BOUNDS['code_max_retries']+1} calls/cell) ~${worst:.2f}")
    print("=" * 88)
    return tot_cells, real, worst


def verify_temperature():
    """Assert temperature=0 actually reaches messages.create, and that the raised
    bound reaches the adapter. This run exists precisely to fix cells at
    temperature 0 -- a silently dropped temperature, or a bound that never took
    effect, would make the spend worthless. Fail closed."""
    from evidence.eval_run.adapters.base import Adapter

    probe = Adapter.__new__(Adapter)
    probe.temperature = PARITY_TEMP
    kwargs = probe._temp_kwargs()
    assert kwargs == {"temperature": 0}, \
        f"temperature={PARITY_TEMP} does NOT reach messages.create (got {kwargs!r})"

    probe.temperature = None
    assert probe._temp_kwargs() == {}, \
        "temperature=None should omit the kwarg — accessor semantics changed"

    print(f"Confirmed: code_agent.temperature={PARITY_TEMP} "
          f"(messages.create kwargs -> {{'temperature': 0}})")
    print(f"Confirmed: code_max_retries={BOUNDS['code_max_retries']} "
          f"(react temp-0 observed max: risk 8, wta_1 5, world_1 5, bike_1 10)")


def run(cells):
    from anthropic import Anthropic
    from evidence.eval_run.harness import run_cells
    factory = lambda: Anthropic()                                      # noqa: E731
    ran = skipped = 0
    for dom, byq in cells.items():
        if not byq:
            continue
        cfg = DOMAINS[dom]
        qids = sorted(byq)
        seeds = sorted({s for v in byq.values() for s in v})
        print(f"\n  [{dom}] {len(qids)} qids x seeds{seeds} at temperature "
              f"{PARITY_TEMP}, bound={BOUNDS['code_max_retries']} ...")
        summary = run_cells(
            qids=qids, seeds=seeds, systems=SYSTEMS,
            client_factory=factory, model=MODEL,
            temperature=PARITY_TEMP, use_cache=True, bounds=BOUNDS,
            corpus_path=cfg["corpus"], gold_path=cfg["gold"],
            register_path=cfg["register"], schema_path=cfg["schema"],
            results_path=out_results(dom), provenance_path=out_prov(dom),
        )
        ran += summary.get("ran", 0)
        skipped += summary.get("skipped", 0)
        if summary.get("aborted"):
            print(f"  ABORTED: {summary.get('abort')} — re-run to resume.")
            return False, ran, skipped
    return True, ran, skipped


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="scope + cost, no API")
    ap.add_argument("--run", action="store_true", help="execute live")
    a = ap.parse_args(argv)
    cells = stale_cells()
    n, real, worst = scope(cells)
    if a.dry or not a.run:
        print("\nDry run only. Re-invoke with --run to execute live.")
        return 0
    verify_temperature()
    print(f"\nLive run: {n} cells. Writing per-domain results_temp0_parity_c1_bound12_rerun_<domain>.jsonl")
    ok, ran, skipped = run(cells)
    print(f"\n{'COMPLETE' if ok else 'INCOMPLETE'}: ran={ran} skipped={skipped}")
    rows = []
    for dom in PARITY_FILE:
        if out_results(dom).exists():
            rows += _load(out_results(dom))
    if rows:
        spend = sum(r.get("cost_usd_cached") or r.get("cost_usd") or 0.0 for r in rows)
        raw = sum(r.get("cost_usd_raw") or 0.0 for r in rows)
        calls = [r["llm_calls"] for r in rows]
        print(f"  rows={len(rows)}  actual spend=${spend:.2f} (raw ${raw:.2f})  "
              f"calls: mean={statistics.mean(calls):.2f} max={max(calls)}")
        print(f"  outcomes: {dict(Counter(r.get('outcome_class') for r in rows))}")
        print(f"  vs estimate: realistic ${real:.2f} / worst ${worst:.2f}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
