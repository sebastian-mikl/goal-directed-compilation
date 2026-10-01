#!/usr/bin/env python
"""Phase R — final post-fix, NETWORK-ONLY Spider re-run (the last live phase).

Measures the network system's post-fix Spider performance live, network ONLY —
every prompt/engine fix touches only the network adapter, the GDC engine, or
the shared comparator, so the four baselines' recorded results
(results_spider_{domain}.jsonl) stay valid and are NOT re-run.

TWO REFUSE_OUT_OF_SCOPE_DATA VARIANTS:
  * OFF  — baseline-comparable. The out_of_scope_register queries wta_11/12/13
           and their kin answer confidently via proxied real columns
           (silent_wrong): only the toggle can refuse these, and it is off here.
           Runs ALL tiers.
  * ON   — the toggle's designed behavior: the same out_of_scope_register
           queries are expected to DECLINE correctly. Runs the
           out_of_scope_register tier ONLY, since the toggle changes nothing on
           the answer / out_of_contract tiers — those come from the OFF run.
           Over-refusal on the answer tier is therefore not re-checked here; set
           S40_ON_QIDS="all" in the env to run the on-variant over everything.

OUTPUT (never overwrites; distinct files per variant):
  results_spider_{domain}_postfix_s40off.jsonl / .provenance.json   (all tiers)
  results_spider_{domain}_postfix_s40on.jsonl  / .provenance.json   (oos tier)

PROVENANCE records (augmented onto what run_cells writes): the toggle state per
file, temperature (0), repetitions, plus run_cells' own model / prompt /
contract / comparator / corpus+gold sha fields.

RESUMABLE: run_cells' logger skips any (system, qid, rep) cell already in the
target file. Re-run the SAME command to resume — completed cells are skipped,
nothing is double-paid-for. The two variants write different files, so they
resume independently.

Needs ANTHROPIC_API_KEY:

    python scripts/run_phase_r.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:                      # noqa: BLE001 — .env optional
    pass

MODEL = "claude-sonnet-4-5"
REPETITIONS = [0, 1, 2, 3, 4]          # 5 reps
TEMPERATURE = 0                        # explicit; network hardcodes 0 at its call site anyway
SYSTEMS = ["network"]                  # network ONLY — baselines untouched by every fix

BOUNDARY_TIERS = ("out_of_scope_register", "out_of_contract_aggregate")
REFUSE_TIER = "out_of_scope_register"

DOMAINS = {
    "wta_1": {
        "corpus":   ROOT / "evaluation/domains/spider_wta_1/eval_corpus_spider_wta_1.json",
        "gold":     ROOT / "evidence" / "gold" / "gold_spider_wta_1.json",
        "register": ROOT / "evaluation/domains/spider_wta_1" / "matches.pkl",
        "schema":   ROOT / "evaluation/domains/spider_wta_1" / "schema.json",
        "tokens":   (1426, 177),       # empirical network Call-1 mean (this repo's own results)
    },
    "world_1": {
        "corpus":   ROOT / "evaluation/domains/spider_world_1/eval_corpus_spider_world_1.json",
        "gold":     ROOT / "evidence" / "gold" / "gold_spider_world_1.json",
        "register": ROOT / "evaluation/domains/spider_world_1" / "country.pkl",
        "schema":   ROOT / "evaluation/domains/spider_world_1" / "schema.json",
        "tokens":   (1426, 171),
    },
    "bike_1": {
        "corpus":   ROOT / "evaluation/domains/spider_bike_1/eval_corpus_spider_bike_1.json",
        "gold":     ROOT / "evidence" / "gold" / "gold_spider_bike_1.json",
        "register": ROOT / "evaluation/domains/spider_bike_1" / "trip.pkl",
        "schema":   ROOT / "evaluation/domains/spider_bike_1" / "schema.json",
        "tokens":   (1072, 191),
    },
}

# The fix arc included in this run (immutable, already-pushed commits). git HEAD
# is the definitive "all fixes present" pointer; this curated map is the readable
# per-fix breakdown, recorded into provenance.
FIX_COMMITS = {
    "phase0_replay_harness":            "d006353",
    "fix3_s42_satisfies_exact_columns": "99d74b3",
    "fix1_s43_order_closure_prompt":    "a0c4928",
    "finding7_s44_implicit_ranking":    "e9bda00",
    "fix2_s45_count_distinct":          "35f2436",
    "s46_comparator_scalar_orientation":"765d5c9",
    "s47_comparator_namebased_casefold":"6f7b581",
}


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                       text=True).strip()
    except Exception:                  # noqa: BLE001
        return "<unknown>"


def out_paths(domain: str, s40_on: bool) -> tuple[Path, Path]:
    tag = "s40on" if s40_on else "s40off"
    base = ROOT / f"results/results_spider_{domain}_postfix_{tag}"
    return base.with_suffix(".jsonl"), Path(str(base) + ".provenance.json")


def load_domain(domain: str):
    cfg = DOMAINS[domain]
    missing = [p for p in (cfg["corpus"], cfg["gold"], cfg["register"], cfg["schema"])
               if not p.exists()]
    if missing:
        raise SystemExit(f"ERROR: {domain}: missing file(s):\n  " +
                         "\n  ".join(str(p) for p in missing))
    corpus = json.loads(cfg["corpus"].read_text())
    gold = json.loads(cfg["gold"].read_text())
    qids = [q["id"] for q in corpus["queries"]]
    missing_gold = [q for q in qids if q not in gold]
    if missing_gold:
        raise SystemExit(f"ERROR: {domain}: corpus ids missing from gold: {missing_gold[:5]}")
    return corpus, gold, qids


def qids_for(domain: str, gold: dict, qids: list[str], s40_on: bool) -> list[str]:
    """OFF → all qids (every tier); ON → the out_of_scope_register tier only,
    UNLESS env S40_ON_QIDS=all requests the full set (answer-tier over-refusal
    guard). Tier is read from CURRENT gold by qid (never a stale corpus field)."""
    if not s40_on or os.getenv("S40_ON_QIDS", "").strip().lower() == "all":
        return list(qids)
    return [q for q in qids if gold[q]["tier"] == REFUSE_TIER]


def check_wiring() -> None:
    for domain in DOMAINS:
        load_domain(domain)             # raises on any wiring problem
    print(f"Wiring OK: {len(DOMAINS)} domains, network-only, {len(REPETITIONS)} reps, "
          f"model={MODEL}, temperature={TEMPERATURE}.\n")


def print_cost_estimate() -> None:
    from evidence.eval_run.pricing import cost_usd, min_cacheable_tokens
    print("=" * 90)
    print("PHASE R COST ESTIMATE (network only; before any live call)")
    print("=" * 90)
    grand = 0.0
    total_cells = 0
    for s40_on in (False, True):
        variant = "S40 ON (out_of_scope_register tier only)" if s40_on else "S40 OFF (ALL tiers)"
        print(f"\n  --- {variant} ---")
        vtotal = 0.0
        for domain, cfg in DOMAINS.items():
            corpus, gold, qids = load_domain(domain)
            n_q = len(qids_for(domain, gold, qids, s40_on))
            cells = n_q * len(REPETITIONS)
            total_cells += cells
            tin, tout = cfg["tokens"]
            per = cost_usd(MODEL, tin, tout)
            dcost = per * cells
            vtotal += dcost
            print(f"    {domain:<8s} {n_q:>2d} q x {len(REPETITIONS)} reps = {cells:>3d} cells "
                  f"x ~${per:.4f} = ~${dcost:6.3f}")
        grand += vtotal
        print(f"    variant subtotal: ~${vtotal:.3f}")
    floor = min_cacheable_tokens(MODEL)
    print(f"\n  TOTAL: {total_cells} network cells, ~${grand:.2f} cache-blind CEILING.")
    print(f"  The system prefix is byte-identical within a (domain, S40-state) block and well")
    print(f"  over the {floor}-tok cache floor, so with caching the real bill comes in below this.")
    print("=" * 90 + "\n")


def augment_provenance(prov_path: Path, s40_on: bool) -> None:
    """run_cells wrote the base provenance; add the Phase-R-specific fields the
    task requires and that write_provenance does not record (S40 state, fix
    commits). Idempotent — safe to re-run on resume."""
    try:
        prov = json.loads(prov_path.read_text())
    except Exception:                  # noqa: BLE001
        prov = {}
    prov["phase"] = "R"
    prov["systems"] = SYSTEMS
    prov["refuse_out_of_scope_data"] = bool(s40_on)       # S40 toggle state (the gap write_provenance leaves)
    prov["s40_variant"] = "on" if s40_on else "off"
    prov["fix_commits"] = FIX_COMMITS
    prov["fix_arc_head_commit"] = _git_head()
    prov_path.write_text(json.dumps(prov, indent=2))


def run_variant(s40_on: bool) -> bool:
    """Run one S40 variant across all domains. Returns True if fully complete,
    False if aborted (resume by re-running the same command)."""
    from anthropic import Anthropic
    from evidence.eval_run.harness import run_cells

    os.environ["REFUSE_OUT_OF_SCOPE_DATA"] = "1" if s40_on else "0"
    os.environ.pop("REFUSE_UNSUPPORTED", None)            # S28 stays OFF for Phase R
    tag = "ON" if s40_on else "OFF"
    print("#" * 90)
    print(f"# VARIANT: REFUSE_OUT_OF_SCOPE_DATA = {tag}")
    print("#" * 90)

    for domain in DOMAINS:
        cfg = DOMAINS[domain]
        corpus, gold, all_qids = load_domain(domain)
        qids = qids_for(domain, gold, all_qids, s40_on)
        results_path, prov_path = out_paths(domain, s40_on)
        print(f"\n[{domain} / S40 {tag}] {len(qids)} qids x {len(REPETITIONS)} reps "
              f"-> {results_path.name}")
        summary = run_cells(
            qids=qids, seeds=REPETITIONS, systems=SYSTEMS,
            client_factory=lambda: Anthropic(), model=MODEL,
            temperature=TEMPERATURE, use_cache=True,
            corpus_path=cfg["corpus"], gold_path=cfg["gold"],
            register_path=cfg["register"], schema_path=cfg["schema"],
            results_path=results_path, provenance_path=prov_path,
        )
        augment_provenance(prov_path, s40_on)
        print(f"  {summary['ran']} ran, {summary['skipped']} skipped"
              f"{' — ABORTED' if summary.get('aborted') else ''}")
        if summary.get("aborted"):
            print(f"  [ABORT] transport/API error — fix the cause and re-run the SAME "
                  f"command to resume (completed cells skipped).")
            return False
    return True


# ── reporting ────────────────────────────────────────────────────────────────

def _load(results_path: Path) -> list[dict]:
    if not results_path.exists():
        return []
    return [json.loads(l) for l in results_path.read_text().splitlines() if l.strip()]


def _tier_of(gold: dict, qid: str) -> str:
    return gold[qid]["tier"]                              # CURRENT gold (bike_09 retier respected)


def _rate(rows, cls):
    n = len(rows)
    return (0.0 if n == 0 else 100.0 * sum(1 for r in rows if r["outcome_class"] == cls) / n)


def print_report() -> None:
    print("\n" + "=" * 90)
    print("PHASE R REPORT — network, post-fix (tier-split; boundary tiers never blended in)")
    print("=" * 90)

    # gather S40-off (all tiers) + S40-on (oos tier) per domain, tier from current gold
    off, on = {}, {}
    golds = {}
    for domain in DOMAINS:
        _c, gold, _q = load_domain(domain)
        golds[domain] = gold
        off[domain] = _load(out_paths(domain, False)[0])
        on[domain] = _load(out_paths(domain, True)[0])
    if not any(off.values()):
        print("  (no Phase R results yet — run the script live first, then re-run for this report)")
        return

    # ── 1. IN-CONTRACT ANSWER TIER (the fair thesis comparison) ──────────────
    print("\n1. IN-CONTRACT ANSWER TIER (excl. out_of_scope_register + out_of_contract_aggregate)")
    print("   — the number that tests the thesis vs baselines; NOT blended with boundary tiers.")
    comb_c = comb_n = 0
    for domain in DOMAINS:
        rows = [r for r in off[domain] if _tier_of(golds[domain], r["qid"]) not in BOUNDARY_TIERS]
        n = len(rows); c = sum(1 for r in rows if r["outcome_class"] == "correct")
        comb_c += c; comb_n += n
        print(f"     {domain:<8s} n={n:<4d} correct={c:<3d} ({_rate(rows,'correct'):5.1f}%)  "
              f"silent_wrong={_rate(rows,'silent_wrong'):4.1f}%  "
              f"refused_should_answer={_rate(rows,'refused_should_answer'):4.1f}%")
    if comb_n:
        print(f"     {'COMBINED':<8s} n={comb_n:<4d} correct={comb_c:<3d} "
              f"({100.0*comb_c/comb_n:5.1f}%)   "
              f"[pre-fix recorded baseline was 60/145 = 41.4% — see results_spider_*.jsonl]")

    # ── 2. out_of_scope_register: BOTH variants side by side (S40 attribution) ─
    print("\n2. out_of_scope_register — S40 OFF (residual) vs S40 ON (resolution), side by side")
    print("   correct% here = REFUSED% (refuse is the correct behavior on this tier).")
    for domain in DOMAINS:
        ro = [r for r in off[domain] if _tier_of(golds[domain], r["qid"]) == REFUSE_TIER]
        rn = [r for r in on[domain] if _tier_of(golds[domain], r["qid"]) == REFUSE_TIER]
        print(f"     {domain:<8s} OFF: refused={_rate(ro,'correct'):5.1f}% "
              f"silent_wrong={_rate(ro,'silent_wrong'):5.1f}% (n={len(ro)})   ||   "
              f"ON: refused={_rate(rn,'correct'):5.1f}% "
              f"silent_wrong={_rate(rn,'silent_wrong'):5.1f}% (n={len(rn)})")
    if not any(on.values()):
        print("     (S40-ON side empty — the on-variant has not been run yet)")

    # ── 3. out_of_contract_aggregate: as-is (genuine architectural boundary) ──
    print("\n3. out_of_contract_aggregate — as-is (the genuine Goal-contract boundary;")
    print("   low recovery here is CORRECT, not a failure — these are unreachable by design).")
    for domain in DOMAINS:
        rc = [r for r in off[domain]
              if _tier_of(golds[domain], r["qid"]) == "out_of_contract_aggregate"]
        print(f"     {domain:<8s} n={len(rc):<3d} correct={_rate(rc,'correct'):5.1f}%  "
              f"refused_should_answer={_rate(rc,'refused_should_answer'):5.1f}%  "
              f"silent_wrong={_rate(rc,'silent_wrong'):5.1f}%")
    print("=" * 90)


def main() -> int:
    print(__doc__)
    check_wiring()
    print_cost_estimate()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.")
        print("Phase R makes live network Call-1 requests (see the estimate above). Set the key:")
        print("  export ANTHROPIC_API_KEY=sk-...")
        print("  python scripts/run_phase_r.py")
        print("\n(S40-ON over the full set instead of oos-only: S40_ON_QIDS=all python scripts/run_phase_r.py)")
        return 2

    ok_off = run_variant(s40_on=False)
    if not ok_off:
        return 1
    ok_on = run_variant(s40_on=True)
    if not ok_on:
        return 1
    print_report()
    print("\nPhase R complete. The entire Spider fix engagement is done — back to the paper.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
