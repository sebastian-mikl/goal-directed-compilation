#!/usr/bin/env python
"""The pre-registered held-out run (heldout/PREREGISTRATION.md): five systems on the
held-out Spider domains, temperature 0, seeds 0 and 1, on the frozen engine.

What runs
---------
Every (system, question, seed) cell of the plan: all five systems on the answer tier
and on out_of_scope_register; GDC alone on out_of_contract_aggregate (run, not scored,
as in the paper's Appendix A.5). Model claude-sonnet-4-5, prompt caching on, react 15
iterations, code_agent 12 retries (13 calls), 60 s per query: the bounds of the
batch-2 re-run (scripts/rerun_temp0_batch2.py), which this script reuses for its
prompt guard, its fake client and its fixed-code check.

What is sent
------------
GDC (`network`) is sent its s45 Call-1 prompt, the version behind the paper's Spider
numbers (results_spider_<db>_postfix_s40off, S40 off). The frozen code renders s48;
s45 is that text with the two blocks s46a/s48 added removed (S48_BLOCKS). Before
anything runs, the transform is applied to the current rendering for wta_1, world_1
and bike_1 and must reproduce, byte for byte, the network prompt recorded in each of
those three provenance files; otherwise the script refuses. The four alternatives are
sent the text the frozen code renders, which for the three development databases is
byte-identical to their recorded Spider prompts. Each (domain, system) group runs
through batch 2's SubstitutingClient: a call with another system text, a temperature
other than 0 or another model is refused before it is sent. No refusal or decline
toggle may be set (REFUSE_UNSUPPORTED, REFUSE_OUT_OF_SCOPE_DATA, DECLINE_AFFORDANCE),
nor EMISSION_MODEL / MODEL_CALL1.

Integrity
---------
heldout/FROZEN.json (written by heldout/freeze.py when the pre-registration is
approved) holds the sha256 of every input file, this script, the analysis script and
PREREGISTRATION.md, and the engine-file hashes (parity_overlay.engine_hashes) of the
frozen commit. The script refuses to plan or run if any differs. Each provenance file
gets a `heldout` block: FROZEN.json's sha256, the engine hashes, the s45 verification,
the plan keys, the prompt hashes sent per call and per cell, and the SQLite.

Spend
-----
Hard cap --cap (default the pre-registered $15). One (system, question) batch at a
time; a batch whose worst case (the call bound times the dearest single call recorded
for that system on Spider) would take the billed spend over the cap is not started.
Resumable; halts on the first guard refusal or API/credit error.

    python heldout/run_heldout.py --dry          # plan, cost, checks, no-API preflight of every cell
    python heldout/run_heldout.py --run          # live (needs ANTHROPIC_API_KEY; .env at the root is read)

Outputs: heldout/results/heldout_<db>.jsonl + .provenance.json, heldout/results/heldout_spend.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import evidence.eval_run.harness as H  # noqa: E402
from evidence.eval_run.logger import sqlite_info  # noqa: E402
from evidence.eval_run.pricing import cost_usd  # noqa: E402
from evidence.eval_run.refuse_config import (decline_affordance_enabled,  # noqa: E402
                                             refuse_out_of_scope_data_enabled,
                                             refuse_unsupported_enabled)
from scripts.parity_overlay import engine_hashes  # noqa: E402
from scripts.probe_temperature import DOMAINS as DEV_DOMAINS  # noqa: E402
from scripts.rerun_temp0_batch2 import (BOUNDS, MODEL, TEMP, SubstitutingClient,  # noqa: E402
                                        _FakeInner, _GuardHalt, _halting, _prompts_recorded,
                                        fixed_code)

SEEDS = (0, 1)
CAP_USD = 15.0
SYSTEMS = ("network", "planner", "react", "code_agent", "text_to_sql")
ANSWER_TIERS = ("filter", "aggregate", "group_by", "order_by")
OOSR, OOCA = "out_of_scope_register", "out_of_contract_aggregate"
CALLS = {"network": 1, "planner": 1, "react": 15, "code_agent": 13, "text_to_sql": 1}
OUT_DIR = HERE / "results"
FROZEN = HERE / "FROZEN.json"
LEDGER = "heldout_spend.json"
ENV_OFF = ("REFUSE_UNSUPPORTED", "REFUSE_OUT_OF_SCOPE_DATA", "DECLINE_AFFORDANCE",
           "EMISSION_MODEL", "MODEL_CALL1")

DOMAINS = {
    "employee_hire_evaluation": {
        "corpus": HERE / "eval_corpus_spider_employee_hire_evaluation.json",
        "gold": HERE / "gold" / "gold_spider_employee_hire_evaluation.json",
        "register": HERE / "employee_hire_evaluation" / "shop.pkl",
        "schema": HERE / "employee_hire_evaluation" / "schema.json"},
    "train_station": {
        "corpus": HERE / "eval_corpus_spider_train_station.json",
        "gold": HERE / "gold" / "gold_spider_train_station.json",
        "register": HERE / "train_station" / "station.pkl",
        "schema": HERE / "train_station" / "schema.json"},
}

# The two blocks s46a/s48 added to the Call-1 prompt (planner/llm.py, GOAL_ORDER_INSTRUCTION).
S48_BLOCKS = (
    '  - "top N" with no stated direction means the N LARGEST — emit dir "desc" unless\n'
    '    the request says lowest/smallest/least/fewest/ascending.\n',
    '  - STAGED FILTERS — a request that derives a quantity and then RESTRICTS rows by it\n'
    '    ("keep only positive-gap entries", "for entries with a negative buffer", "where\n'
    '    the gap exceeds X") contributes a "where" conjunct on that DERIVED name, exactly\n'
    '    like a filter on a base column. Emit one conjunct per such restriction. This is\n'
    '    REQUIRED even when the derived column is not itself reported: a row-membership\n'
    '    filter that never reaches the output still selects which rows the aggregate sees,\n'
    '    so dropping it silently changes the answer. A multi-stage request ("derive A,\n'
    '    keep A>0, then derive B, and for B<0 report avg(B) per G") yields BOTH conjuncts\n'
    '    — A>0 AND B<0 — not just the one feeding the reported value. Every derived\n'
    '    quantity must be consumed by something: a where conjunct, another derive, the\n'
    '    group key, order_by, or "columns". A derive consumed by nothing means a\n'
    '    restriction was dropped — the request is refused rather than answered wrongly.\n',
)
GDC_PROMPT_VERSION_SENT = "s45"
S45_REFERENCE = "results_spider_{d}_postfix_s40off.provenance.json"   # GDC's Spider numbers of record


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def _file_sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def to_s45(text: str) -> str:
    """The s45 Call-1 text from the frozen code's s48 rendering: each S48 block removed
    once. Raises if a block does not occur exactly once."""
    for b in S48_BLOCKS:
        n = text.count(b)
        if n != 1:
            raise ValueError(f"an s48 block occurs {n} times in the GDC prompt, expected 1")
        text = text.replace(b, "")
    return text


# ── checks ──────────────────────────────────────────────────────────────────

def env_check() -> list[str]:
    bad = [k for k in ENV_OFF if os.environ.get(k, "").strip()]
    if refuse_unsupported_enabled() or refuse_out_of_scope_data_enabled() or decline_affordance_enabled():
        bad.append("a refusal/decline toggle is on")
    return bad


def frozen_check() -> tuple[bool, list[str], str]:
    """(ok, problems, sha256 of FROZEN.json)."""
    if not FROZEN.exists():
        return False, [f"{FROZEN.name} is missing: run heldout/freeze.py when the pre-registration is approved"], ""
    fz = json.loads(FROZEN.read_text())
    probs = []
    for rel, sha in fz["files"].items():
        p = ROOT / rel
        if not p.exists():
            probs.append(f"missing {rel}")
        elif _file_sha(p) != sha:
            probs.append(f"changed {rel}")
    cur = engine_hashes(ROOT)
    if cur != fz["engine_sha256"]:
        moved = sorted(f for f in set(cur) | set(fz["engine_sha256"]) if cur.get(f) != fz["engine_sha256"].get(f))
        probs.append(f"engine files differ from the frozen commit: {moved}")
    return not probs, probs, _file_sha(FROZEN)


def _adapters(domain_cfg: dict):
    return H.build_adapters(H.CachingClient(_FakeInner(), cache=True), pd.read_pickle(domain_cfg["register"]),
                            H.load_briefing(domain_cfg["schema"], domain_cfg["register"]), model=MODEL,
                            temperature=TEMP, bounds=BOUNDS, schema_path=domain_cfg["schema"])


def verify_s45() -> dict:
    """Apply to_s45 to the frozen code's GDC prompt for the three development databases
    and compare with the recorded s45 prompt of GDC's Spider numbers of record."""
    out = {}
    for d in ("wta_1", "world_1", "bike_1"):
        rec = json.loads((ROOT / S45_REFERENCE.format(d=d)).read_text())
        assert rec.get("network_prompt_version") == "s45" and rec.get("s40_variant") == "off", d
        cur = _adapters(DEV_DOMAINS[d])["network"].cache_prefix_text()
        sent = to_s45(cur)
        out[d] = {"recorded_sha256": _sha(rec["prompts"]["network"]), "derived_sha256": _sha(sent),
                  "identical": sent == rec["prompts"]["network"]}
    return out


_TEXTS: dict[str, dict] = {}


def texts_for(domain: str) -> dict[str, dict[str, str]]:
    """{system: {"current": what the frozen code renders, "sent": what is sent}}."""
    if domain not in _TEXTS:
        ads = _adapters(DOMAINS[domain])
        t = {s: {"current": a.cache_prefix_text()} for s, a in ads.items()}
        for s in t:
            t[s]["sent"] = to_s45(t[s]["current"]) if s == "network" else t[s]["current"]
        _TEXTS[domain] = t
    return _TEXTS[domain]


def expected_tools(system: str, domain: str):
    from evidence.eval_run.tools_code import code_tools
    from evidence.eval_run.tools_decl import DECL_TOOLS
    from evidence.eval_run.tools_sql import SQL_TOOLS
    if system == "code_agent":
        return code_tools(len(pd.read_pickle(DOMAINS[domain]["register"])))
    return {"react": DECL_TOOLS, "text_to_sql": SQL_TOOLS}.get(system)


# ── the plan and its price ──────────────────────────────────────────────────

def tier_class(tier: str) -> str:
    return "answer" if tier in ANSWER_TIERS else tier


def build_plan() -> list[dict]:
    rows = []
    for dom, cfg in DOMAINS.items():
        corpus = H.load_corpus(cfg["corpus"])
        gold = H.load_gold(cfg["gold"])
        for sysn in SYSTEMS:
            for qid, q in corpus.items():
                tc = tier_class(q["tier"])
                if gold[qid]["tier"] != q["tier"]:
                    raise SystemExit(f"{dom}/{qid}: corpus tier {q['tier']} != gold tier {gold[qid]['tier']}")
                if tc == OOCA and sysn != "network":
                    continue
                for seed in SEEDS:
                    rows.append({"domain": dom, "system": sysn, "qid": qid, "seed": seed, "tier": q["tier"],
                                 "tier_class": tc})
    return rows


# Recorded Spider sources for the price, newest first per system (tracked files only).
PRICE_SOURCES = {
    "network": ["results_spider_{d}_postfix_s40off.jsonl"],
    "planner": ["results_spider_{d}.jsonl"],
    "react": ["results_temp0_batch2_{d}.jsonl", "results_temp0_parity_{d}.jsonl", "results_spider_{d}.jsonl"],
    "code_agent": ["results_temp0_batch2_{d}.jsonl", "results_temp0_parity_c1_bound12_rerun_{d}.jsonl",
                   "results_spider_{d}.jsonl"],
    "text_to_sql": ["results_temp0_batch2_{d}.jsonl", "results_temp0_parity_{d}.jsonl", "results_spider_{d}.jsonl"],
}


def price_table(root: Path = ROOT) -> tuple[dict, dict]:
    """realistic: {(system, tier class): mean recorded billed $ per cell on the three
    development databases, from the newest source holding that system and tier class};
    worst: {system: call bound x the dearest single call (cache-blind) recorded}."""
    realistic, worst = {}, defaultdict(float)
    for sysn, pats in PRICE_SOURCES.items():
        for tc in ("answer", OOSR, OOCA):
            for pat in pats:
                vals = [r.get("cost_usd") or 0.0 for d in ("wta_1", "world_1", "bike_1")
                        for r in _load(root / pat.format(d=d))
                        if r.get("system") == sysn and tier_class(r.get("tier", "")) == tc]
                if vals:
                    realistic[(sysn, tc)] = (sum(vals) / len(vals), pat.replace("{d}", "*"), len(vals))
                    break
        dear = 0.0
        for pat in pats:
            for d in ("wta_1", "world_1", "bike_1"):
                for r in _load(root / pat.format(d=d)):
                    if r.get("system") == sysn:
                        for t in r.get("per_call_tokens") or []:
                            dear = max(dear, cost_usd(MODEL, t.get("in", 0), t.get("out", 0)))
        worst[sysn] = CALLS[sysn] * dear
    return realistic, dict(worst)


def estimate(rows, realistic, worst) -> tuple[float, float, list]:
    agg = defaultdict(lambda: [0, 0.0, 0.0])
    for r in rows:
        a = agg[(r["system"], r["tier_class"])]
        a[0] += 1
        a[1] += realistic.get((r["system"], r["tier_class"]), (0.0,))[0]
        a[2] += worst[r["system"]]
    lines = [(k, *v) for k, v in sorted(agg.items())]
    return sum(v[1] for v in agg.values()), sum(v[2] for v in agg.values()), lines


# ── execution ───────────────────────────────────────────────────────────────

class Ledger:
    """Billed spend, persisted beside the outputs (dollar totals only). A reservation
    left by a process that died is moved to unaccounted_usd on the next start."""

    def __init__(self, out_dir: Path):
        self.path = Path(out_dir) / LEDGER
        self.d = (json.loads(self.path.read_text()) if self.path.exists()
                  else {"billed_usd": 0.0, "unaccounted_usd": 0.0, "reserved": None})
        if self.d.get("reserved"):
            self.d["unaccounted_usd"] += self.d["reserved"]["usd"]
            self.d["reserved"] = None
            self._save()

    def _save(self):
        self.path.write_text(json.dumps(self.d, indent=1))

    def total(self) -> float:
        r = self.d.get("reserved")
        return self.d["billed_usd"] + self.d["unaccounted_usd"] + (r["usd"] if r else 0.0)

    def reserve(self, usd: float, what: str):
        self.d["reserved"] = {"usd": usd, "what": what}
        self._save()

    def settle(self, billed: float):
        self.d["billed_usd"] += billed
        self.d["reserved"] = None
        self._save()


@contextmanager
def _prompt_version_sent():
    """Provenance records the GDC prompt version actually sent."""
    orig = H.GOAL_PROMPT_VERSION
    H.GOAL_PROMPT_VERSION = GDC_PROMPT_VERSION_SENT
    try:
        yield
    finally:
        H.GOAL_PROMPT_VERSION = orig


def _record(prov: Path, domain: str, meta: dict, plan_keys: list, sent: Counter, cells: dict):
    p = json.loads(prov.read_text())
    b = p.setdefault("heldout", {**meta, "domain": domain, "plan_keys": plan_keys,
                                 "plan_keys_sha256": _sha(json.dumps(plan_keys)),
                                 "sent_calls_by_sha256": {}, "cell_prompt": {}})
    for h, n in sent.items():
        b["sent_calls_by_sha256"][h] = b["sent_calls_by_sha256"].get(h, 0) + n
    b["cell_prompt"].update(cells)
    prov.write_text(json.dumps(p, indent=2))


def execute(rows, client_for, out_dir: Path, meta: dict, *, cap=None, worst=None, quiet=False):
    """Run every planned cell, one (system, qid) batch at a time. Returns (done, {group: client})."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(out_dir)
    subs = {}
    by_dom = defaultdict(list)
    for r in rows:
        by_dom[r["domain"]].append(r)
    for dom, drows in by_dom.items():
        cfg, tx = DOMAINS[dom], texts_for(dom)
        res, prov = out_dir / f"heldout_{dom}.jsonl", out_dir / f"heldout_{dom}.provenance.json"
        plan_keys = sorted([r["system"], r["qid"], r["seed"]] for r in drows)
        old = json.loads(prov.read_text()).get("heldout", {}) if prov.exists() else {}
        if old and (old.get("plan_keys") != plan_keys or old.get("frozen_sha256") != meta["frozen_sha256"]):
            raise SystemExit(f"[resume guard] {res.name} was started under another plan or freeze")
        groups = defaultdict(lambda: defaultdict(list))
        for r in drows:
            groups[r["system"]][r["qid"]].append(r["seed"])
        with _prompts_recorded({s: tx[s]["sent"] for s in tx}), _prompt_version_sent():
            for sysn in SYSTEMS:
                if sysn not in groups:
                    continue
                sub = subs[(dom, sysn)] = client_for(tx[sysn]["current"], tx[sysn]["sent"])
                for qid, seeds in groups[sysn].items():
                    done = {(r["system"], r["qid"], r["seed"]) for r in _load(res)}
                    todo = sorted(s for s in seeds if (sysn, qid, s) not in done)
                    if not todo:
                        continue
                    reserve = worst[sysn] * len(todo) if worst else 0.0
                    if cap is not None and ledger.total() + reserve > cap:
                        print(f"  STOP before {sysn}/{dom}/{qid}: billed ${ledger.total():.2f} + worst "
                              f"${reserve:.2f} would exceed the cap ${cap:.2f}.")
                        return False, subs
                    before, billed0 = Counter(sub.sent), sub.billed
                    ledger.reserve(reserve, f"{dom}/{sysn}/{qid}")
                    try:
                        summary = H.run_cells(
                            qids=[qid], seeds=todo, systems=[sysn], client_factory=lambda: sub,
                            model=MODEL, temperature=TEMP, use_cache=True, bounds=BOUNDS,
                            corpus_path=cfg["corpus"], gold_path=cfg["gold"], register_path=cfg["register"],
                            schema_path=cfg["schema"], results_path=res, provenance_path=prov,
                            progress=_halting(sub, echo=(lambda *a: None) if quiet else print))
                    except _GuardHalt as exc:
                        ledger.settle(sub.billed - billed0)
                        print(f"  HALT: the prompt guard refused {sysn}/{dom}/{qid} ({exc}); nothing was sent "
                              f"for that cell. Delete its row from {res.name} before re-running.")
                        return False, subs
                    billed = sub.billed - billed0
                    ledger.settle(billed)
                    ran = {f"{r['system']}/{r['qid']}/{r['seed']}": _sha(tx[sysn]["sent"]) for r in _load(res)
                           if r["system"] == sysn and r["qid"] == qid and r["seed"] in todo}
                    _record(prov, dom, meta, plan_keys, sub.sent - before, ran)
                    if summary.get("aborted"):
                        print(f"  ABORTED: {summary.get('abort')} -- fix the cause and re-run to resume.")
                        return False, subs
                    if cap is not None and billed > reserve + 1e-9:
                        print(f"  STOP after {sysn}/{dom}/{qid}: billed ${billed:.4f} > worst-case ${reserve:.4f}.")
                        return False, subs
    return True, subs


def preflight(rows, meta) -> bool:
    """No-API run of every planned cell through the real harness."""
    fakes, ok = {}, True

    def client_for(current, target):
        fake = _FakeInner()
        sub = SubstitutingClient(fake, current, target)
        fakes[id(sub)] = fake
        return sub

    with tempfile.TemporaryDirectory() as d:
        done, subs = execute(rows, client_for, Path(d), meta, quiet=True)
        ok &= done
        for (dom, sysn), sub in subs.items():
            calls, want_sha = fakes[id(sub)].calls, _sha(texts_for(dom)[sysn]["sent"])
            want_tools = expected_tools(sysn, dom)
            bad = [c for c in calls if c.get("temperature") != TEMP or c.get("model") != MODEL
                   or _sha(c["system"][-1]["text"]) != want_sha or c.get("tools") != want_tools]
            g_ok = bool(calls) and not bad and not sub.refused and set(sub.sent) == {want_sha}
            ok &= g_ok
            print(f"  {dom:<25} {sysn:<12} calls {len(calls):>4}  prompt {want_sha[:10]}  "
                  f"{'ok' if g_ok else f'FAILED (bad {len(bad)}, refused {sub.refused[:1]})'}")
        for dom in DOMAINS:
            res, prov = Path(d) / f"heldout_{dom}.jsonl", Path(d) / f"heldout_{dom}.provenance.json"
            got = Counter((r["system"], r["qid"], r["seed"]) for r in _load(res))
            want = Counter((r["system"], r["qid"], r["seed"]) for r in rows if r["domain"] == dom)
            p = json.loads(prov.read_text())
            tx = texts_for(dom)
            f_ok = (got == want and all(p["prompts"].get(s) == tx[s]["sent"] for s in SYSTEMS)
                    and p["temperature"] == TEMP and p["bounds"] == BOUNDS
                    and p["network_prompt_version"] == GDC_PROMPT_VERSION_SENT
                    and set(p["heldout"]["cell_prompt"]) == {f"{s}/{q}/{sd}" for s, q, sd in want}
                    and p["heldout"]["fixed_code"]["sqlite"] == sqlite_info())
            ok &= f_ok
            print(f"  heldout_{dom:<25} cells {sum(got.values()):>4}/{sum(want.values()):<4} "
                  f"{'ok' if f_ok else 'FAILED'}")
    print("  PREFLIGHT", "PASSED" if ok else "FAILED")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--cap", type=float, default=CAP_USD)
    ap.add_argument("--allow-unfrozen", action="store_true",
                    help="dry run only: plan and preflight before FROZEN.json exists")
    a = ap.parse_args(argv)
    print("=" * 96)
    print("HELD-OUT RUN (pre-registered): five systems, held-out Spider domains, temperature 0, seeds", SEEDS)
    problems = []
    bad_env = env_check()
    if bad_env:
        problems.append(f"environment: {bad_env}")
    fz_ok, fz_probs, fz_sha = frozen_check()
    if not fz_ok:
        print("  FROZEN:", "; ".join(fz_probs))
        if a.run or not a.allow_unfrozen:
            problems.append("the frozen inputs do not match FROZEN.json")
    fixes = fixed_code()
    if not all(fixes["checks"].values()):
        problems.append(f"fixes missing: {[k for k, v in fixes['checks'].items() if not v]}")
    s45 = verify_s45()
    for d, v in s45.items():
        print(f"  s45 check {d:<8} derived {v['derived_sha256'][:10]} recorded {v['recorded_sha256'][:10]} "
              f"{'identical' if v['identical'] else 'DIFFERENT'}")
    if not all(v["identical"] for v in s45.values()):
        problems.append("the s45 transform does not reproduce the recorded Spider prompts")
    if problems:
        print("  REFUSED:", "; ".join(problems))
        return 2
    meta = {"script": "heldout/run_heldout.py", "frozen_sha256": fz_sha, "seeds": list(SEEDS),
            "gdc_prompt_version_sent": GDC_PROMPT_VERSION_SENT,
            "gdc_prompt_derivation": "frozen s48 rendering with S48_BLOCKS removed",
            "s45_verification": s45, "fixed_code": fixes, "cap_usd": a.cap}
    rows = build_plan()
    realistic, worst = price_table()
    tot_r, tot_w, lines = estimate(rows, realistic, worst)
    per = Counter((r["domain"], r["tier_class"], r["system"]) for r in rows)
    print(f"  cells {len(rows)}: " + ", ".join(f"{k[0]}/{k[1]}/{k[2]}={v}" for k, v in sorted(per.items())))
    print(f"  {'system':<12}{'tier':<27}{'cells':>6}{'realistic':>11}{'worst':>10}   source of the realistic price")
    for (sysn, tc), n, rl, wc in lines:
        src = realistic.get((sysn, tc))
        print(f"  {sysn:<12}{tc:<27}{n:>6}{rl:>11.2f}{wc:>10.2f}   "
              f"{(src[1] + '  (n=' + str(src[2]) + ')') if src else 'none recorded'}")
    print(f"  {'total':<39}{len(rows):>6}{tot_r:>11.2f}{tot_w:>10.2f}   cap ${a.cap:.2f}   bounds {BOUNDS}")
    print("=" * 96)
    if not preflight(rows, meta):
        return 2
    if not a.run:
        print("\nDry run only (no API calls made).")
        return 0
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:                                  # noqa: BLE001 -- .env is optional
        pass
    from anthropic import Anthropic
    api = Anthropic()
    done, _ = execute(rows, lambda cur, tgt: SubstitutingClient(api, cur, tgt), OUT_DIR, meta,
                      cap=a.cap, worst=worst)
    n = sum(len(_load(OUT_DIR / f"heldout_{d}.jsonl")) for d in DOMAINS)
    print(f"\n{'COMPLETE' if done and n == len(rows) else 'INCOMPLETE'}: {n}/{len(rows)} cells, "
          f"billed ${Ledger(OUT_DIR).total():.2f}")
    return 0 if done and n == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
