#!/usr/bin/env python
"""Post hoc equal-information run (review/planner_info_prereg.md): the planner under its reported
prompt with the shared briefing replaced by GDC's domain information. System id planner_gdcinfo.

Why. The planner writes the steps for GDC's executor in one call, so it is the clean
derive-versus-write comparison, but its prompt carried the shared briefing, not GDC's richer
domain information. This run gives it that information (scripts/planner_info_prompt.py; the
line-by-line split is review/planner_info_split.md).

What is sent. Per database, the informed prompt of scripts/planner_info_prompt.build(), pinned
below by sha256. The shipped PlannerUpfrontAdapter renders the planner's recorded prompt, byte for
byte (checked); batch 2's SubstitutingClient (scripts/rerun_temp0_batch2.py) swaps it for the
informed prompt on every call and refuses anything else (another system text, a temperature other
than 0, another model). The user message is the corpus question, as for every reported cell.

What runs. The shipped PlannerUpfrontAdapter (evidence/eval_run/adapters/planner_upfront.py) and
its executor on the fixed engine (gdc/, CONTRACT_VERSION s49), through the harness's own run_cell
and CachingClient: claude-sonnet-4-5, temperature 0 and max_tokens 1500 (the adapter's own, as for
every reported planner cell), one call per cell, prompt caching on, REFUSE_UNSUPPORTED /
REFUSE_OUT_OF_SCOPE_DATA / DECLINE_AFFORDANCE off. Cells, 398, in this order:
  employee  the 81 in-scope questions (every gold tier but out_of_range) x seeds 0-2     243
  held-out  the 10 answer-tier questions of employee_hire_evaluation and train_station
            x seeds 0-1                                                                  20
  Spider    the 27 answer-tier questions of wta_1, world_1 and bike_1 x seeds 0-4        135
Every planned cell has a reported planner cell with the same question. A seed is a repetition
label; the API takes none.

Outputs (results/):
  results_temp0_planner_gdcinfo.jsonl             one harness row per cell, system planner_gdcinfo,
                                                  plus its database ("domain")
  results_temp0_planner_gdcinfo.provenance.json   model, temperature, max_tokens, every prompt (text +
                                                  sha256) and its diff, commit, engine hashes, plan, cap, cost
  results_temp0_planner_gdcinfo.spend.json        billed ledger (dollar totals only)
No builder reads these names, and pi.SYSTEMS has no planner_gdcinfo.

Spend. --cap-usd (default 5.00). Before each cell the worst case of its one call is reserved
(cache-blind: 3 x the largest recorded input of a reported planner call in that database, plus the
UTF-8 bytes the informed prompt adds, plus the full 1500-token output); the run stops BEFORE a cell
whose reservation would take the spend over the cap. The spend is the larger of the billed ledger
(every response's usage, billed on arrival) and the logged rows' cost. A cell that bills more than
its reservation stops the run.

Resume. Re-running the same command skips every (qid, seed) already logged. A transport/API error
aborts without logging the in-flight cell. The provenance pins the prompts, model, engine and plan;
a resume under any other one is refused.

Usage:
  PYTHON_DOTENV_DISABLED=1 python scripts/run_planner_info.py --dry   # no API: plan, hashes, diffs,
                                                                      # cost estimate, offline preflight
  python scripts/run_planner_info.py --run [--cap-usd 5.00]           # live; ANTHROPIC_API_KEY from the file
                                                                      # $GDC_ENV_FILE (default: <repo>/.env)
Score and analyse afterwards with scripts/planner_info_stats.py (run_all.sh step 6).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True          # never leave .pyc files in the repository

HERE = Path(__file__).resolve()
DEFAULT_ROOT = HERE.parents[1]          # the repository this script sits in (scripts/..)
ENV_PATH = Path(os.environ.get("GDC_ENV_FILE") or (DEFAULT_ROOT / ".env"))   # the key file; never printed

SYSTEM_ID = "planner_gdcinfo"
MODEL = "claude-sonnet-4-5"
TEMP = 0
MAX_TOKENS = 1500                       # PlannerUpfrontAdapter's own (Adapter.max_tokens), checked below
OUT_STEM = "results_temp0_planner_gdcinfo"
DEFAULT_CAP = 5.00
CONTRACT_EXPECTED = "s49"
ANSWER_TIERS = ("filter", "aggregate", "group_by", "order_by")
TOGGLES = ("REFUSE_UNSUPPORTED", "REFUSE_OUT_OF_SCOPE_DATA", "DECLINE_AFFORDANCE", "EMISSION_MODEL",
           "MODEL_CALL1")

# group -> (databases, seeds); the order of the run
GROUPS = (
    ("employee", ("employee",), (0, 1, 2)),
    ("heldout", ("employee_hire_evaluation", "train_station"), (0, 1)),
    ("spider", ("wta_1", "world_1", "bike_1"), (0, 1, 2, 3, 4)),
)
N_CELLS = {"employee": 243, "heldout": 20, "spider": 135}

# database -> (reported planner prompt sha256, informed prompt sha256): scripts/planner_info_prompt.py,
# review/planner_info_split.md
PROMPTS = {
    "employee": ("cba3002cf8d32c4ef5e06c9e4cde2403fa8669849f8c026ee9e6d89536fcfdeb",
                 "86fe7a782515b787c1ca3b06f362e6a925da9bee5bbc5e735acab536dac81bfa"),
    "employee_hire_evaluation": ("3f281f131e8a749fa725cf200f4c1e891e4ecab08138dfa031e76fe1bc8a6919",
                                "6f971cab1b75ee19603ef1c70a70f87f74f044db415ea2909dddb21bcf4b801d"),
    "train_station": ("090eebbe032bc02412e4cc1ba52ada5ffc3fb97a5a03caf13e4cce72b47368c8",
                     "82deae245548408304ea97ac212b4f0094832b641570d6eb81946eae74e4af46"),
    "wta_1": ("27a4432d9ec3e5400b87451797917bc122c28b2ab798b2e19e0dc8a5ed0a78d9",
             "e61746e463eee60ef2433ddffe546a4f8dfb90f79ee283d273728512d11c9626"),
    "world_1": ("5d61b98cae09a52930d8042eee3c5de0d87037c10770eb1ef308b2a8d79df125",
               "144c4be047d1e1c27439def6358d835bb1066aed9d9416d3a8345b5b9b183c97"),
    "bike_1": ("b7af1edbd87f67928acfff743bf217ce72901dd6e1ce303fb40e879b9e8e135b",
              "7a1f8bb9531ec253846d12918e4f2a8329ee58a3293c1c2f601621efc452692c"),
}

# database -> the recorded file holding its reported planner cells
REPORTED = {
    "employee": "results/results_employee.jsonl",
    "employee_hire_evaluation": "heldout/results/heldout_employee_hire_evaluation.jsonl",
    "train_station": "heldout/results/heldout_train_station.jsonl",
    "wta_1": "results/results_spider_wta_1.jsonl",
    "world_1": "results/results_spider_world_1.jsonl",
    "bike_1": "results/results_spider_bike_1.jsonl",
}


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _sha_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ── environment ─────────────────────────────────────────────────────────────

class NetGuard:
    """Dry mode: every socket connect / DNS lookup raises, and constructing an Anthropic
    client raises. `hits` counts attempts; a dry run must end with 0."""
    hits = 0

    @classmethod
    def install(cls):
        def no_net(*a, **k):
            cls.hits += 1
            raise RuntimeError("dry mode: network access attempted")
        socket.socket.connect = no_net
        socket.socket.connect_ex = no_net
        socket.create_connection = no_net
        socket.getaddrinfo = no_net
        try:
            import anthropic

            class _NoClient:
                def __init__(self, *a, **k):
                    no_net()
            anthropic.Anthropic = _NoClient
            anthropic.AsyncAnthropic = _NoClient
        except ImportError:
            pass

    @classmethod
    def self_test(cls) -> bool:
        before = cls.hits
        try:
            socket.create_connection(("api.anthropic.com", 443), timeout=1)
        except RuntimeError:
            pass
        ok = cls.hits == before + 1
        cls.hits = before
        return ok


def check_toggles():
    on = {k: os.environ[k] for k in TOGGLES if os.environ.get(k, "").strip()}
    if on:
        raise SystemExit(f"refusing: these must be unset for the run: {sorted(on)}")


def resolve_root(arg: str | None) -> Path:
    if arg:
        return Path(arg).resolve()
    return DEFAULT_ROOT


# ── inputs ──────────────────────────────────────────────────────────────────

class Ctx:
    """Everything the run needs, read once from the repository (no writes)."""

    def __init__(self, root: Path):
        self.root = root
        sys.path.insert(0, str(root))
        import pandas as pd
        import evidence.eval_run.harness as H
        from evidence.eval_run.adapters.planner_upfront import PlannerUpfrontAdapter
        from evidence.eval_run.refuse_config import decline_affordance, refuse_suffix
        from gdc.contract import CONTRACT_VERSION
        from scripts.parity_overlay import engine_hashes, engine_sha, incomplete_row
        from scripts.rerun_temp0_batch2 import BOUNDS, SubstitutingClient, MODEL as B2_MODEL, TEMP as B2_TEMP
        import scripts.planner_info_prompt as PIP
        self.H, self.pd, self.Adapter, self.BOUNDS = H, pd, PlannerUpfrontAdapter, BOUNDS
        self.SubstitutingClient, self.incomplete_row = SubstitutingClient, incomplete_row
        if (B2_MODEL, B2_TEMP) != (MODEL, TEMP):
            raise SystemExit(f"SubstitutingClient guards {B2_MODEL}/{B2_TEMP}, this run needs {MODEL}/{TEMP}")
        if CONTRACT_VERSION != CONTRACT_EXPECTED:
            raise SystemExit(f"CONTRACT_VERSION {CONTRACT_VERSION}, expected {CONTRACT_EXPECTED}")
        if refuse_suffix("planner") or decline_affordance("planner"):
            raise SystemExit("a refuse/decline toggle is on: the planner prompt would carry a suffix")
        self.contract_version = CONTRACT_VERSION
        try:
            self.builds = PIP.build_all(root)
        except PIP.SplitError as exc:
            raise SystemExit(f"prompt build stopped: {exc}")
        self.dom, self.corpus, self.gold, self.register, self.briefing, self.paths = {}, {}, {}, {}, {}, {}
        self.cells, self.reported = [], {}
        for group, doms, seeds in GROUPS:
            n = 0
            for d in doms:
                p = PIP.paths(root, d)
                b = self.builds[d]
                rep_sha, inf_sha = PROMPTS[d]
                if b["planner_sha256"] != rep_sha or b["informed_sha256"] != inf_sha:
                    raise SystemExit(f"{d}: prompt hashes {b['planner_sha256'][:12]}/{b['informed_sha256'][:12]} "
                                     f"differ from the pinned {rep_sha[:12]}/{inf_sha[:12]}")
                self.paths[d] = p
                self.corpus[d] = H.load_corpus(p["corpus"])
                self.gold[d] = H.load_gold(p["gold"])
                self.register[d] = pd.read_pickle(p["register"])
                self.briefing[d] = H.load_briefing(p["schema"], p["register"])
                rows = [json.loads(l) for l in (root / REPORTED[d]).read_text().splitlines() if l.strip()]
                rep = {(r["qid"], int(r["seed"])): r for r in rows if r.get("system") == "planner"}
                if group == "employee":
                    qids = [q for q in self.corpus[d] if q in self.gold[d] and self.gold[d][q]["tier"] != "out_of_range"]
                else:
                    qids = [q for q in self.corpus[d] if q in self.gold[d] and self.gold[d][q]["tier"] in ANSWER_TIERS]
                for q in qids:
                    if self.corpus[d][q].get("tier") not in (None, self.gold[d][q]["tier"]) and group != "spider":
                        raise SystemExit(f"{d}/{q}: corpus tier {self.corpus[d][q].get('tier')} != gold tier")
                    for s in seeds:
                        r = rep.get((q, s))
                        if r is None or r.get("query") != self.corpus[d][q]["question"]:
                            raise SystemExit(f"{d}/{q} s{s}: no reported planner cell, or its recorded question differs")
                        if q in self.dom and self.dom[q] != d:
                            raise SystemExit(f"question id {q} occurs in two databases")
                        self.dom[q] = d
                        self.cells.append((d, q, s))
                        self.reported[(d, q, s)] = r
                        n += 1
            if n != N_CELLS[group]:
                raise SystemExit(f"{group}: {n} planned cells, expected {N_CELLS[group]}")
        self.plan_keys = sorted([SYSTEM_ID, d, q, s] for d, q, s in self.cells)
        # engine: the engine the reported replay numbers were built on
        self.engine_hashes = engine_hashes(root)
        self.engine_sha = engine_sha(self.engine_hashes)
        reported_engine = json.loads((root / "reports" / "paper_numbers.engine.json").read_text())["engine_sha256"]
        if self.engine_sha != reported_engine:
            raise SystemExit(f"engine {self.engine_sha[:12]} is not the reported numbers' engine {reported_engine[:12]}")
        self.gdc_sha256 = {str(p.relative_to(root)): _sha_file(p) for p in sorted((root / "gdc").glob("*.py"))}
        # per-call worst case per database
        from evidence.eval_run.pricing import cost_usd
        self.cost_usd = cost_usd
        self.in_max, self.worst = {}, {}
        for d in self.builds:
            rep = [r for k, r in self.reported.items() if k[0] == d]
            self.in_max[d] = max(int(t.get("in", 0) or 0) for r in rep for t in (r.get("per_call_tokens") or []))
            extra = len(self.builds[d]["informed"].encode()) - len(self.builds[d]["planner_prompt"].encode())
            self.worst[d] = cost_usd(MODEL, 3 * self.in_max[d] + max(extra, 0), MAX_TOKENS)

    def adapter(self, d: str, client):
        """The shipped PlannerUpfrontAdapter, built as harness.build_adapters builds it; only its
        system-id label differs, so run_cell writes `planner_gdcinfo`."""
        a = self.Adapter(name=SYSTEM_ID, client=client, register=self.register[d], briefing=self.briefing[d],
                         model=MODEL, temperature=TEMP, max_iters=self.BOUNDS["react_max_iters"],
                         max_retries=self.BOUNDS["code_max_retries"], timeout_s=self.BOUNDS.get("per_query_timeout_s"))
        if a.max_tokens != MAX_TOKENS:
            raise SystemExit(f"PlannerUpfrontAdapter.max_tokens {a.max_tokens}, expected {MAX_TOKENS}")
        return a

    def current_prompt(self, d: str) -> str:
        cur = self.adapter(d, None).cache_prefix_text()
        if cur != self.builds[d]["planner_prompt"]:
            raise SystemExit(f"{d}: the current code no longer renders the reported planner prompt")
        return cur


def git_info(root: Path) -> dict:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], env=env,
                                       stderr=subprocess.DEVNULL).decode().strip()
        dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                                        env=env, stderr=subprocess.DEVNULL).strip()
        return {"commit": head, "tracked_changes": bool(dirty)}
    except Exception:                                   # noqa: BLE001
        return {"commit": "<unknown>", "tracked_changes": None}


# ── ledger, provenance, execution ───────────────────────────────────────────

def _load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _write_json(p: Path, obj) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(p)


class Ledger:
    """Billed spend, persisted beside the outputs: billed_usd (every response received) and an
    open reservation, which a process that died leaves behind and the next start keeps counted."""

    def __init__(self, path: Path):
        self.path = path
        self.d = json.loads(path.read_text()) if path.exists() else {"billed_usd": 0.0, "unaccounted_usd": 0.0,
                                                                     "reserved": None}
        if self.d.get("reserved"):
            self.d["unaccounted_usd"] += self.d["reserved"]["usd"]
            self.d["reserved"] = None
            self._save()

    def _save(self):
        _write_json(self.path, self.d)

    def total(self) -> float:
        r = self.d.get("reserved")
        return self.d["billed_usd"] + self.d["unaccounted_usd"] + (r["usd"] if r else 0.0)

    def reserve(self, usd: float, cell: str):
        self.d["reserved"] = {"usd": usd, "cell": cell}
        self._save()

    def settle(self, billed: float):
        self.d["billed_usd"] += billed
        self.d["reserved"] = None
        self._save()


def provenance_base(ctx: Ctx, cap: float) -> dict:
    from evidence.eval_run.comparator import COMPARATOR_VERSION
    from evidence.eval_run.logger import _lib_versions
    import scripts.planner_info_prompt as PIP
    doms = {}
    for d, b in ctx.builds.items():
        p = ctx.paths[d]
        doms[d] = {
            "group": PIP.DOMAINS[d]["group"],
            "gdc_prompt_source": f"{b['gdc_source']} prompts.network", "gdc_prompt_version": b["gdc_version"],
            "gdc_prompt_sha256": b["gdc_sha256"],
            "planner_prompt_source": f"{b['planner_source']} prompts.planner",
            "planner_prompt_sha256": b["planner_sha256"],
            "informed_prompt_sha256": b["informed_sha256"], "informed_prompt": b["informed"],
            "diff": PIP.prompt_diff(b),
            "split": [{"n": x["n"], "class": x["cls"], "reason": x["reason"]} for x in b["split"]],
            "corpus_path": str(p["corpus"].relative_to(ctx.root)), "corpus_sha256": _sha_file(p["corpus"]),
            "gold_path": str(p["gold"].relative_to(ctx.root)), "gold_sha256": _sha_file(p["gold"]),
            "register_sha256": _sha_file(p["register"]), "schema_sha256": _sha_file(p["schema"]),
            "reported_cells": REPORTED[d], "worst_case_per_cell_usd": round(ctx.worst[d], 6),
            "cells": sum(1 for c in ctx.cells if c[0] == d),
        }
    return {
        "script": "scripts/run_planner_info.py", "system": SYSTEM_ID,
        "what": ("the planner under its reported prompt with the shared briefing replaced by GDC's domain "
                 "information (post hoc equal-information run, review/planner_info_prereg.md)"),
        "timestamp_utc_start": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": MODEL, "temperature": TEMP, "max_tokens": MAX_TOKENS, "llm_calls_per_cell": 1,
        "prompt_caching": True, "groups": {g: {"databases": list(ds), "seeds": list(ss)} for g, ds, ss in GROUPS},
        "toggles_off": list(TOGGLES[:3]),
        "contract_version": ctx.contract_version, "comparator_version": COMPARATOR_VERSION,
        "domains": doms,
        "prompts": {d: b["informed"] for d, b in ctx.builds.items()},
        "git": [git_info(ctx.root)],
        "engine": {"engine_sha256": ctx.engine_sha, "files": ctx.engine_hashes, "gdc_py_sha256": ctx.gdc_sha256},
        "plan_keys": ctx.plan_keys, "plan_keys_sha256": _sha(json.dumps(ctx.plan_keys)),
        "cap_usd": cap,
        "lib_versions": _lib_versions(),
        "env": {"ANTHROPIC_API_KEY_present": bool(os.getenv("ANTHROPIC_API_KEY"))},
        "cell_prompt": {}, "sent_calls_by_sha256": {}, "cost": {},
    }


PINNED = ("model", "temperature", "max_tokens", "contract_version", "plan_keys")


def check_resume(old: dict, new: dict):
    diffs = [k for k in PINNED if old.get(k) != new.get(k)]
    for d, nd in new["domains"].items():
        od = (old.get("domains") or {}).get(d) or {}
        for k in ("informed_prompt_sha256", "corpus_sha256", "gold_sha256", "register_sha256", "schema_sha256"):
            if od.get(k) != nd[k]:
                diffs.append(f"domains.{d}.{k}")
    if old.get("engine", {}).get("engine_sha256") != new["engine"]["engine_sha256"]:
        diffs.append("engine.engine_sha256")
    if diffs:
        raise SystemExit(f"[resume guard] the existing provenance differs on {diffs}: start a fresh output "
                         f"(--out-dir) or restore that configuration")


def execute(ctx: Ctx, inner, out_dir: Path, *, cap: float, echo=print) -> tuple[bool, dict]:
    """Run every planned cell not yet logged. `inner` is the innermost client (Anthropic, or a
    fake for the preflight). Returns (complete, {database: SubstitutingClient})."""
    from evidence.eval_run.logger import CachingClient, JsonlLogger
    res, prov_p = out_dir / f"{OUT_STEM}.jsonl", out_dir / f"{OUT_STEM}.provenance.json"
    ledger = Ledger(out_dir / f"{OUT_STEM}.spend.json")
    bad = [(r["qid"], r["seed"], why) for r in _load(res) if (why := ctx.incomplete_row(r))]
    if bad:
        raise SystemExit(f"{res.name} holds rows that are not completed cells {bad[:3]}: delete them first")
    base = provenance_base(ctx, cap)
    if prov_p.exists():
        prov = json.loads(prov_p.read_text())
        check_resume(prov, base)
        gi = git_info(ctx.root)
        if gi not in prov["git"]:
            prov["git"].append(gi)
        prov["cap_usd"] = cap
    else:
        prov = base
    subs, adapters = {}, {}
    for d, b in ctx.builds.items():
        current = ctx.current_prompt(d)
        subs[d] = ctx.SubstitutingClient(inner, current, b["informed"], model=MODEL)
        adapters[d] = ctx.adapter(d, CachingClient(subs[d], cache=True))
        if adapters[d].cache_prefix_text() != current:
            raise SystemExit(f"{d}: the adapter's rendered prompt changed between construction and run")
    logger = JsonlLogger(res)

    def billed_total() -> float:
        return sum(s.billed for s in subs.values())

    def spent() -> float:
        logged = sum(r.get("cost_usd_cached") or r.get("cost_usd") or 0.0 for r in _load(res))
        return max(ledger.total(), logged)

    def save(complete: bool):
        rows = _load(res)
        prov["cost"] = {"billed_usd": round(ledger.d["billed_usd"], 6),
                        "unaccounted_usd": round(ledger.d["unaccounted_usd"], 6),
                        "logged_cost_usd_cached": round(sum(r.get("cost_usd_cached") or 0.0 for r in rows), 6),
                        "logged_cost_usd_raw": round(sum(r.get("cost_usd_raw") or 0.0 for r in rows), 6),
                        "spent_usd": round(spent(), 6)}
        prov["cells_logged"] = len(rows)
        prov["complete"] = complete and len(rows) == len(ctx.cells)
        prov["timestamp_utc_last"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _write_json(prov_p, prov)

    save(False)
    for d, qid, seed in ctx.cells:
        if logger.has(SYSTEM_ID, qid, seed):
            continue
        sub, worst = subs[d], ctx.worst[d]
        if spent() + worst > cap:
            echo(f"  STOP before {d}/{qid} s{seed}: spent ${spent():.4f} + worst case ${worst:.4f} "
                 f"would exceed the cap ${cap:.2f}. Re-run with a higher --cap-usd to continue.")
            save(False)
            return False, subs
        billed0, sent0 = billed_total(), dict(sub.sent)
        ledger.reserve(worst, f"{d}/{qid}/{seed}")
        try:
            rec = ctx.H.run_cell(adapters[d], qid, ctx.corpus[d][qid]["question"], seed, ctx.gold[d][qid], model=MODEL)
        except Exception as exc:                        # noqa: BLE001 -- run_cell re-raises transport errors only
            ledger.settle(billed_total() - billed0)
            echo(f"  ABORT at {d}/{qid} s{seed}: {type(exc).__name__}: {exc}. The cell was not logged; "
                 f"fix the cause and re-run the same command to resume.")
            save(False)
            return False, subs
        billed = billed_total() - billed0
        ledger.settle(billed)
        if sub.refused:
            echo(f"  HALT at {d}/{qid} s{seed}: the prompt guard refused ({sub.refused[0]}). Nothing was sent "
                 f"and the cell was not logged.")
            save(False)
            return False, subs
        if rec["system"] != SYSTEM_ID:
            raise SystemExit(f"run_cell wrote system {rec['system']!r}")
        rec["domain"] = d
        logger.emit(rec)
        for h, n in sub.sent.items():
            k = n - sent0.get(h, 0)
            if k:
                prov["sent_calls_by_sha256"][h] = prov["sent_calls_by_sha256"].get(h, 0) + k
        prov["cell_prompt"][f"{d}/{qid}/{seed}"] = ctx.builds[d]["informed_sha256"]
        save(False)
        echo(f"[{SYSTEM_ID} {d:<24s} {qid:>10s} s{seed}] {rec['outcome_class']:<22s} in={rec['input_tokens']:>5d} "
             f"(u{rec['uncached_input_tokens']}/w{rec['cache_write_tokens']}/r{rec['cache_read_tokens']}) "
             f"out={rec['output_tokens']:>4d} ${rec['cost_usd']:.4f}  spent ${spent():.4f}")
        if billed > worst + 1e-9:
            echo(f"  STOP after {d}/{qid} s{seed}: it billed ${billed:.4f}, more than its worst case "
                 f"${worst:.4f}; the estimate no longer bounds the spend.")
            save(False)
            return False, subs
    save(True)
    return True, subs


# ── dry run: plan, estimate, offline preflight ──────────────────────────────

class _Usage:
    def __init__(self, i, o):
        self.input_tokens, self.output_tokens = i, o
        self.cache_creation_input_tokens = self.cache_read_input_tokens = 0


class _FakeInner:
    """No-API stand-in: records every call and answers with one fixed, valid plan."""
    TEXT = '{"plan": [{"op": "order", "input": "register", "order_by": [], "limit": 1}], "final_frame": "f0"}'

    def __init__(self, usage=(10, 5)):
        self.calls = []
        self.usage = usage
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        block = type("B", (), {"type": "text", "text": self.TEXT})()
        u = self.usage(kw) if callable(self.usage) else self.usage
        return type("R", (), {"content": [block], "usage": _Usage(*u), "stop_reason": "end_turn"})()


def preflight(ctx: Ctx, scratch: Path) -> bool:
    """Every planned cell through the real adapter, harness and guard with a fake client, in a
    scratch directory: each call sends its database's informed text (cache_control kept) at
    temperature 0 on the model of record with max_tokens 1500 and the corpus question; 398 rows
    with system planner_gdcinfo; a second pass resumes with 0 calls; a cap stops before it is
    exceeded; a call that bills more than its reservation stops the run."""
    ok = True
    d = scratch / "preflight"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    informed = {k: b["informed"] for k, b in ctx.builds.items()}
    try:
        fake = _FakeInner()
        done, subs = execute(ctx, fake, d, cap=1e9, echo=lambda *a: None)
        rows = _load(d / f"{OUT_STEM}.jsonl")
        want = [(dm, ctx.corpus[dm][q]["question"]) for dm, q, _ in ctx.cells]
        texts = [c["system"][0].get("text") if len(c.get("system") or []) == 1 else None for c in fake.calls]
        bad = [c for c in fake.calls
               if c.get("temperature") != TEMP or c.get("model") != MODEL or c.get("max_tokens") != MAX_TOKENS
               or len(c.get("system") or []) != 1 or c["system"][0].get("cache_control") != {"type": "ephemeral"}
               or len(c.get("messages") or []) != 1 or c["messages"][0].get("role") != "user"]
        sent_ok = [t == informed[dm] for t, (dm, _) in zip(texts, want)]
        qs_ok = [c["messages"][0]["content"] == f"QUERY: {q}" for c, (_, q) in zip(fake.calls, want)]
        keys = {(r["system"], r["domain"], r["qid"], r["seed"]) for r in rows}
        prov = json.loads((d / f"{OUT_STEM}.provenance.json").read_text())
        kinds = {k for s in subs.values() for k in s.kinds}
        c1 = (done and len(fake.calls) == len(ctx.cells) and not bad and all(sent_ok) and all(qs_ok)
              and not any(s.refused for s in subs.values()) and len(rows) == len(ctx.cells)
              and keys == {tuple(k) for k in ctx.plan_keys} and kinds == {"swapped"}
              and set(prov["sent_calls_by_sha256"]) == {b["informed_sha256"] for b in ctx.builds.values()}
              and prov["complete"] and len(prov["cell_prompt"]) == len(ctx.cells)
              and all(r["status"] == "completed" for r in rows))
        print(f"  preflight pass 1: {len(fake.calls)} calls, {len(rows)} rows, calls off-spec {len(bad)}, "
              f"informed text sent {sum(sent_ok)}/{len(sent_ok)}, questions {sum(qs_ok)}/{len(qs_ok)}, "
              f"swaps {sorted(kinds)}, executed {sum(r['status'] == 'completed' for r in rows)}  {'ok' if c1 else 'FAILED'}")
        fake2 = _FakeInner()
        done2, _ = execute(ctx, fake2, d, cap=1e9, echo=lambda *a: None)
        c2 = done2 and not fake2.calls and len(_load(d / f"{OUT_STEM}.jsonl")) == len(ctx.cells)
        print(f"  preflight pass 2 (resume): {len(fake2.calls)} calls  {'ok' if c2 else 'FAILED'}")
        # cap: each fake call bills its database's worst case exactly; a cap of 5.5 employee worst
        # cases must stop before the 6th cell, with the spend still under the cap
        d3 = scratch / "preflight_cap"
        if d3.exists():
            shutil.rmtree(d3)
        d3.mkdir()
        w = ctx.worst["employee"]
        extra = len(informed["employee"].encode()) - len(ctx.builds["employee"]["planner_prompt"].encode())
        cap = 5.5 * w
        fake3 = _FakeInner(usage=(3 * ctx.in_max["employee"] + max(extra, 0), MAX_TOKENS))
        done3, _ = execute(ctx, fake3, d3, cap=cap, echo=lambda *a: None)
        n3 = len(_load(d3 / f"{OUT_STEM}.jsonl"))
        spent3 = json.loads((d3 / f"{OUT_STEM}.provenance.json").read_text())["cost"]["spent_usd"]
        c3 = (not done3) and n3 == len(fake3.calls) == 5 and spent3 <= cap < spent3 + w
        print(f"  preflight cap test (cap ${cap:.4f} = 5.5 worst cases, each fake call bills one): stopped "
              f"before cell {n3 + 1}, spent ${spent3:.4f} <= cap  {'ok' if c3 else 'FAILED'}")
        d4 = scratch / "preflight_over"
        if d4.exists():
            shutil.rmtree(d4)
        d4.mkdir()
        fake4 = _FakeInner(usage=(3 * ctx.in_max["employee"] + max(extra, 0), MAX_TOKENS + 1))
        done4, _ = execute(ctx, fake4, d4, cap=1e9, echo=lambda *a: None)
        c4 = (not done4) and len(fake4.calls) == 1 == len(_load(d4 / f"{OUT_STEM}.jsonl"))
        print(f"  preflight over-reservation test: stopped after {len(fake4.calls)} call  {'ok' if c4 else 'FAILED'}")
        ok = c1 and c2 and c3 and c4
    finally:
        for sub_d in ("preflight", "preflight_cap", "preflight_over"):
            shutil.rmtree(scratch / sub_d, ignore_errors=True)
    return ok


def dry(ctx: Ctx, cap: float, scratch: Path) -> int:
    import scripts.planner_info_prompt as PIP
    print("=" * 100)
    print(f"EQUAL-INFORMATION RUN ({SYSTEM_ID}): the planner with GDC's domain information in place of the briefing")
    print(f"  repository {ctx.root}  {git_info(ctx.root)}")
    print(f"  cells {len(ctx.cells)}: " + ", ".join(
        f"{g} {sum(1 for c in ctx.cells if c[0] in ds)} ({len({c[1] for c in ctx.cells if c[0] in ds})} questions x "
        f"seeds {list(ss)})" for g, ds, ss in GROUPS))
    print(f"  model {MODEL}, temperature {TEMP}, max_tokens {MAX_TOKENS}, one call per cell, caching on, toggles off")
    print(f"  engine {ctx.engine_sha[:12]} (= reports/paper_numbers.engine.json), CONTRACT_VERSION "
          f"{ctx.contract_version}, gdc/*.py files {len(ctx.gdc_sha256)}")
    tot_c = tot_r = 0.0
    out_max = 0
    for d, b in ctx.builds.items():
        rep = [r for k, r in ctx.reported.items() if k[0] == d]
        cached = sum(r.get("cost_usd_cached") or 0.0 for r in rep)
        raw = sum(r.get("cost_usd_raw") or 0.0 for r in rep)
        omax = max(r.get("output_tokens") or 0 for r in rep)
        out_max = max(out_max, omax)
        extra = len(b["informed"]) - len(b["planner_prompt"])
        n = len(rep)
        add = ctx.cost_usd(MODEL, n * extra // 3, 0)    # the added characters at about 3 per token, every call uncached
        tot_c += cached + add
        tot_r += raw + add
        print(f"  {d:<26} {n:>3} cells  planner {b['planner_sha256'][:12]} ({len(b['planner_prompt'])} chars) -> "
              f"informed {b['informed_sha256'][:12]} ({len(b['informed'])} chars, +{extra}); reported cells billed "
              f"${cached:.4f} (cache-blind ${raw:.4f}), max output {omax}; worst case ${ctx.worst[d]:.5f}/cell")
    worst_total = sum(ctx.worst[d] for d, _, _ in ctx.cells)
    print(f"  estimate: the reported planner cells' bill plus the added prompt text uncached: about ${tot_c:.2f} "
          f"(cache-blind ${tot_r:.2f}); worst case ${worst_total:.2f}; cap ${cap:.2f}; reported max output "
          f"{out_max} of {MAX_TOKENS}")
    print(f"  outputs on --run: {ctx.root / 'results' / (OUT_STEM + '.jsonl')} (+ .provenance.json, .spend.json)")
    print("=" * 100)
    ok = preflight(ctx, scratch)
    if NetGuard.hits:
        print(f"  NETWORK GUARD: {NetGuard.hits} connection attempt(s) blocked")
        ok = False
    print(f"  network guard active and never triggered: {NetGuard.self_test() and NetGuard.hits == 0}")
    print("  PREFLIGHT", "PASSED" if ok else "FAILED")
    print("\nDry run only (no API calls made). Re-invoke with --run to execute live.")
    return 0 if ok else 2


def run(ctx: Ctx, cap: float, out_dir: Path) -> int:
    """The key comes from ENV_PATH and nowhere else: main() disabled python-dotenv while the
    repository's modules were imported (gdc/config.py calls load_dotenv() at import), and it is
    re-enabled here for this one file. Never printed."""
    try:
        from dotenv import dotenv_values, load_dotenv
    except ImportError:
        raise SystemExit("python-dotenv is not installed")
    os.environ.pop("PYTHON_DOTENV_DISABLED", None)
    if not ENV_PATH.exists() or not (dotenv_values(ENV_PATH).get("ANTHROPIC_API_KEY") or "").strip():
        raise SystemExit(f"{ENV_PATH} is missing or sets no ANTHROPIC_API_KEY")
    os.environ.pop("ANTHROPIC_API_KEY", None)
    load_dotenv(ENV_PATH, override=True)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(f"ANTHROPIC_API_KEY is not set after loading {ENV_PATH}")
    from anthropic import Anthropic
    api = Anthropic()                                   # reads the key from the environment; never printed
    done, _ = execute(ctx, api, out_dir, cap=cap)
    n = len(_load(out_dir / f"{OUT_STEM}.jsonl"))
    print(f"\n{'COMPLETE' if done and n == len(ctx.cells) else 'INCOMPLETE'}: {n}/{len(ctx.cells)} cells")
    return 0 if done else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="the planner with GDC's domain information (planner_gdcinfo)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true", help="no API: plan, hashes, estimate, offline preflight")
    g.add_argument("--run", action="store_true", help="live run (API)")
    ap.add_argument("--cap-usd", type=float, default=DEFAULT_CAP)
    ap.add_argument("--root", default=None, help="repository root (default: this script's repository)")
    ap.add_argument("--out-dir", default=None, help="--run output directory (default: <root>/results)")
    ap.add_argument("--scratch", default=None, help="--dry scratch directory for the preflight (default: a temp dir)")
    a = ap.parse_args(argv)
    check_toggles()
    if a.dry:
        if os.environ.get("PYTHON_DOTENV_DISABLED") != "1":
            raise SystemExit("--dry needs PYTHON_DOTENV_DISABLED=1, so no .env is loaded")
        os.environ.pop("ANTHROPIC_API_KEY", None)
        NetGuard.install()
    else:
        os.environ["PYTHON_DOTENV_DISABLED"] = "1"     # no .env from the imports; run() loads ENV_PATH only
    root = resolve_root(a.root)
    ctx = Ctx(root)
    if a.dry:
        import tempfile
        if a.scratch:
            scratch = Path(a.scratch)
            scratch.mkdir(parents=True, exist_ok=True)
            return dry(ctx, a.cap_usd, scratch)
        with tempfile.TemporaryDirectory() as t:
            return dry(ctx, a.cap_usd, Path(t))
    return run(ctx, a.cap_usd, Path(a.out_dir) if a.out_dir else root / "results")


if __name__ == "__main__":
    sys.exit(main())
