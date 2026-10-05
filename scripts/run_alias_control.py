#!/usr/bin/env python
"""Post hoc alias control: GDC on the employee in-scope questions under its recorded s31
prompt with ONLY the "active employees" alias removed. System id `network_noalias`.

Why. GDC's recorded employee prompt (results/results_employee.provenance.json,
prompts.network, GOAL_PROMPT_VERSION s31) states the business rule as
  When the request refers to "current" or "active employees" rows, filter status to one of
  those values: ...
The four alternatives' shared briefing gives the rule ('current': 'Status' in [...]) without
the alias. The batch-2 alias arm (results/results_temp0_batch2_employee_alias.jsonl) re-ran the
alternatives WITH the alias. This run is the converse: GDC WITHOUT it.

What is sent. The recorded s31 prompt, byte for byte, with one edit (control_prompt()):
  'When the request refers to "current" or "active employees" rows,'
  -> 'When the request refers to "current" rows,'
which is exactly what the s31 code renders for a rule with no `aliases` field. The current
code renders the s48 prompt; batch 2's SubstitutingClient (scripts/rerun_temp0_batch2.py)
swaps that text for the control text on every call and refuses anything else (another system
text, a temperature other than 0, another model). The user message is the corpus question,
as recorded for every reported cell.

What runs. The shipped NetworkAdapter (evidence/eval_run/adapters/network.py) on the fixed
engine (gdc/, CONTRACT_VERSION s49), through the harness's own run_cell and CachingClient:
claude-sonnet-4-5, temperature 0 and max_tokens 900 (both fixed in gdc/llm.py llm1), prompt
caching on, REFUSE_UNSUPPORTED / REFUSE_OUT_OF_SCOPE_DATA / DECLINE_AFFORDANCE off (the
table-coverage instruction is off). Cells: the 81 employee in-scope questions (every gold tier
but out_of_range) x seeds 0, 1, 2 = 243. A seed is a repetition label; the API takes none.

Outputs (results/):
  results_temp0_employee_gdc_noalias.jsonl             one harness row per cell, system network_noalias
  results_temp0_employee_gdc_noalias.provenance.json   model, temperature, prompts (text + sha256),
                                                       diff, commit, engine hashes, plan, cap, cost
  results_temp0_employee_gdc_noalias.spend.json        billed ledger (dollar totals only)
No builder reads these names: parity_overlay overlays only results_temp0_parity_<dom> and
results_temp0_batch2_<dom> files, and pi.SYSTEMS has no network_noalias.

Spend. --cap-usd (default 3.00). Before each cell the worst case of one call is reserved
(cache-blind: 3 x the largest recorded input of a reported employee GDC call, plus the full
900-token output); the run stops BEFORE a cell whose reservation would take the spend over the
cap. The spend is the larger of the billed ledger (every response's usage, billed on arrival)
and the logged rows' cost. A cell that bills more than its reservation stops the run.

Resume. Re-running the same command skips every (qid, seed) already logged. A transport/API
error aborts without logging the in-flight cell. The provenance pins the control prompt, model,
engine and plan; a resume under any other one is refused.

Usage:
  PYTHON_DOTENV_DISABLED=1 python scripts/run_alias_control.py --dry   # no API: plan, hashes, diff,
                                                                       # cost estimate, offline preflight
  python scripts/run_alias_control.py --run [--cap-usd 3.00]           # live; ANTHROPIC_API_KEY from the file
                                                                       # $GDC_ENV_FILE (default: <repo>/.env)
Score afterwards with scripts/score_alias_control.py (replay through the fixed engine, strict and
tie-tolerant, exactly as the reported GDC employee cells are scored).
"""
from __future__ import annotations

import argparse
import datetime
import difflib
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

SYSTEM_ID = "network_noalias"
MODEL = "claude-sonnet-4-5"
TEMP = 0
LLM1_MAX_TOKENS = 900                   # gdc/llm.py llm1 (and s31's): fixed, checked below
SEEDS = (0, 1, 2)
N_QUESTIONS = 81
DOMAIN = "employee"
RECORDED_RESULTS = "results_employee.jsonl"
RECORDED_PROV = "results_employee.provenance.json"
OUT_STEM = "results_temp0_employee_gdc_noalias"
DEFAULT_CAP = 3.00
CONTRACT_EXPECTED = "s49"

S31_SHA256 = "b41d4eb53a27a4f86b2edbd15d9e2e5c6ec1121865e51b373d2e59b9fe6a7a49"
CONTROL_SHA256 = "470cb29bc3391e6c43dd7a89218aacb7d3ed5f5d6857a6046a6025639952333f"   # control_prompt(s31) ==
                                        # the s31 code's own render of the schema without `aliases`
CURRENT_SHA256 = "a5a30cb596012b86da6019b699cf4f099f740e44580b0d819073ed4f65b735f1"   # s48 render, swapped out
ALIAS_OLD = 'When the request refers to "current" or "active employees" rows,'
ALIAS_NEW = 'When the request refers to "current" rows,'
TOGGLES = ("REFUSE_UNSUPPORTED", "REFUSE_OUT_OF_SCOPE_DATA", "DECLINE_AFFORDANCE", "EMISSION_MODEL",
           "MODEL_CALL1")


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _sha_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ── the control prompt ──────────────────────────────────────────────────────

def control_prompt(recorded: str) -> str:
    """The recorded s31 prompt with the "active employees" alias removed, and nothing else.
    Raises unless the result differs from the input in exactly that one place."""
    if _sha(recorded) != S31_SHA256:
        raise ValueError(f"recorded prompt sha {_sha(recorded)[:12]} is not s31 {S31_SHA256[:12]}")
    if recorded.count(ALIAS_OLD) != 1:
        raise ValueError(f"the rule line occurs {recorded.count(ALIAS_OLD)} times, expected 1")
    if recorded.lower().count("active employee") != 1:
        raise ValueError("'active employee' occurs outside the rule line")
    out = recorded.replace(ALIAS_OLD, ALIAS_NEW)
    a, b = recorded.split("\n"), out.split("\n")
    changed = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if len(a) != len(b) or len(changed) != 1:
        raise ValueError(f"control differs on {len(changed)} lines, expected 1")
    i = changed[0]
    if a[i].replace(' or "active employees"', "", 1) != b[i]:
        raise ValueError("the changed line differs by more than ' or \"active employees\"'")
    if "active employee" in out.lower():
        raise ValueError("'active employee' left in the control prompt")
    return out


def prompt_diff(recorded: str, control: str) -> str:
    return "".join(difflib.unified_diff(
        recorded.splitlines(True), control.splitlines(True),
        f"s31 recorded ({RECORDED_PROV} prompts.network, sha256 {_sha(recorded)[:12]})",
        f"s31 no-alias control (sha256 {_sha(control)[:12]})", n=0))


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
        raise SystemExit(f"refusing: these must be unset for the control run: {sorted(on)}")


def resolve_root(arg: str | None) -> Path:
    if arg:
        return Path(arg).resolve()
    if HERE.parent.name == "scripts" and (HERE.parents[1] / "gdc" / "contract.py").exists():
        return HERE.parents[1]
    return DEFAULT_ROOT


# ── inputs ──────────────────────────────────────────────────────────────────

class Ctx:
    """Everything the run needs, read once from the repository (no writes)."""

    def __init__(self, root: Path):
        self.root = root
        sys.path.insert(0, str(root))
        import pandas as pd
        import evidence.eval_run.harness as H
        from evidence.eval_run.adapters.network import NetworkAdapter
        from evidence.eval_run.refuse_config import refuse_suffix
        from gdc.contract import CONTRACT_VERSION
        import gdc.llm as L
        from scripts.parity_overlay import engine_hashes, engine_sha, incomplete_row
        from scripts.rerun_temp0_batch2 import SubstitutingClient, GUARD, MODEL as B2_MODEL, TEMP as B2_TEMP
        self.H, self.pd, self.NetworkAdapter = H, pd, NetworkAdapter
        self.SubstitutingClient, self.GUARD, self.incomplete_row = SubstitutingClient, GUARD, incomplete_row
        if (B2_MODEL, B2_TEMP) != (MODEL, TEMP):
            raise SystemExit(f"SubstitutingClient guards {B2_MODEL}/{B2_TEMP}, this run needs {MODEL}/{TEMP}")
        if CONTRACT_VERSION != CONTRACT_EXPECTED:
            raise SystemExit(f"CONTRACT_VERSION {CONTRACT_VERSION}, expected {CONTRACT_EXPECTED}")
        if refuse_suffix("network") != "":
            raise SystemExit("a refuse/decline toggle is on: refuse_suffix('network') is not empty")
        import inspect
        src = inspect.getsource(L.llm1)
        if f"max_tokens={LLM1_MAX_TOKENS}" not in src or "temperature=0" not in src:
            raise SystemExit("gdc/llm.py llm1 no longer fixes max_tokens=900 and temperature=0")
        self.contract_version, self.goal_prompt_version = CONTRACT_VERSION, L.GOAL_PROMPT_VERSION
        d = root / "evaluation" / "domains" / "employee"
        self.corpus_path, self.gold_path = d / "eval_corpus_employee.json", root / "evidence/gold/gold_employee.json"
        self.register_path, self.schema_path = d / "employees.pkl", d / "schema.json"
        self.corpus = H.load_corpus(self.corpus_path)
        self.gold = H.load_gold(self.gold_path)
        self.register = pd.read_pickle(self.register_path)
        self.briefing = H.load_briefing(self.schema_path, self.register_path)
        # the recorded prompt and the control
        prov = json.loads((root / "results" / RECORDED_PROV).read_text())
        if prov.get("network_prompt_version") != "s31" or prov.get("model") != MODEL:
            raise SystemExit(f"{RECORDED_PROV}: network_prompt_version {prov.get('network_prompt_version')}, "
                             f"model {prov.get('model')}")
        self.recorded = prov["prompts"]["network"]
        self.control = control_prompt(self.recorded)
        if _sha(self.control) != CONTROL_SHA256:
            raise SystemExit(f"control prompt sha {_sha(self.control)} != pinned {CONTROL_SHA256}")
        self.recorded_commit = prov.get("git_commit")
        # the cells: 81 in-scope questions x 3 seeds, each with its 3 reported GDC cells
        rows = [json.loads(l) for l in (root / "results" / RECORDED_RESULTS).read_text().splitlines() if l.strip()]
        self.reported = {(r["qid"], int(r["seed"])): r for r in rows if r.get("system") == "network"}
        qids = [q for q in self.corpus if q in self.gold and self.gold[q]["tier"] != "out_of_range"]
        if len(qids) != N_QUESTIONS:
            raise SystemExit(f"{len(qids)} in-scope questions, expected {N_QUESTIONS}")
        self.cells = [(q, s) for q in qids for s in SEEDS]
        for q, s in self.cells:
            r = self.reported.get((q, s))
            if r is None or r.get("query") != self.corpus[q]["question"]:
                raise SystemExit(f"{q} s{s}: no reported GDC cell, or its recorded question differs")
        self.plan_keys = sorted([SYSTEM_ID, q, s] for q, s in self.cells)
        # engine: the engine the reported replay numbers were built on
        self.engine_hashes = engine_hashes(root)
        self.engine_sha = engine_sha(self.engine_hashes)
        reported_engine = json.loads((root / "reports" / "paper_numbers.engine.json").read_text())["engine_sha256"]
        if self.engine_sha != reported_engine:
            raise SystemExit(f"engine {self.engine_sha[:12]} is not the reported numbers' engine {reported_engine[:12]}")
        self.gdc_sha256 = {str(p.relative_to(root)): _sha_file(p) for p in sorted((root / "gdc").glob("*.py"))}
        # per-call bounds for the worst case: the reported cells' largest input
        rep = [self.reported[c] for c in self.cells]
        self.in_max = max(int(t.get("in", 0) or 0) for r in rep for t in (r.get("per_call_tokens") or []))
        from evidence.eval_run.pricing import cost_usd, cost_usd_cached
        self.cost_usd, self.cost_usd_cached = cost_usd, cost_usd_cached
        self.worst_cell = cost_usd(MODEL, 3 * self.in_max, LLM1_MAX_TOKENS)

    def adapter(self, client):
        """The shipped NetworkAdapter, built as harness.build_adapters builds it; only its
        system-id label differs, so run_cell writes `network_noalias`."""
        return self.NetworkAdapter(name=SYSTEM_ID, client=client, register=self.register, briefing=self.briefing,
                                   model=MODEL, temperature=TEMP, max_iters=15, max_retries=12, timeout_s=60,
                                   schema_path=self.schema_path)

    def current_prompt(self) -> str:
        return self.adapter(None).cache_prefix_text()


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


def provenance_base(ctx: Ctx, current: str, cap: float) -> dict:
    from evidence.eval_run.comparator import COMPARATOR_VERSION
    from evidence.eval_run.logger import _lib_versions
    return {
        "script": "scripts/run_alias_control.py", "system": SYSTEM_ID,
        "what": ("GDC on the 81 employee in-scope questions x seeds 0-2 under its recorded s31 prompt with "
                 "only the 'active employees' alias removed (post hoc alias control)"),
        "timestamp_utc_start": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": MODEL, "emission_model": MODEL, "temperature": TEMP, "max_tokens": LLM1_MAX_TOKENS,
        "prompt_caching": True, "repetitions": list(SEEDS),
        "toggles_off": list(TOGGLES[:3]),
        "contract_version": ctx.contract_version, "comparator_version": COMPARATOR_VERSION,
        "network_prompt_version": "s31-noalias",
        "code_goal_prompt_version_replaced": ctx.goal_prompt_version,
        "prompts": {SYSTEM_ID: ctx.control},
        "prompt_sha256": {"sent_control": _sha(ctx.control), "recorded_s31": _sha(ctx.recorded),
                          "current_code_render_swapped_out": _sha(current)},
        "prompt_source": {"recorded_in": f"results/{RECORDED_PROV} prompts.network",
                          "recorded_git_commit": ctx.recorded_commit,
                          "edit": {"from": ALIAS_OLD, "to": ALIAS_NEW},
                          "diff": prompt_diff(ctx.recorded, ctx.control)},
        "corpus_path": str(ctx.corpus_path.relative_to(ctx.root)), "corpus_sha256": _sha_file(ctx.corpus_path),
        "gold_path": str(ctx.gold_path.relative_to(ctx.root)), "gold_sha256": _sha_file(ctx.gold_path),
        "register_sha256": _sha_file(ctx.register_path), "schema_sha256": _sha_file(ctx.schema_path),
        "git": [git_info(ctx.root)],
        "engine": {"engine_sha256": ctx.engine_sha, "files": ctx.engine_hashes, "gdc_py_sha256": ctx.gdc_sha256},
        "plan_keys": ctx.plan_keys, "plan_keys_sha256": _sha(json.dumps(ctx.plan_keys)),
        "cap_usd": cap, "worst_case_per_cell_usd": round(ctx.worst_cell, 6),
        "lib_versions": _lib_versions(),
        "env": {"ANTHROPIC_API_KEY_present": bool(os.getenv("ANTHROPIC_API_KEY"))},
        "cell_prompt": {}, "sent_calls_by_sha256": {}, "cost": {},
    }


PINNED = ("model", "temperature", "max_tokens", "contract_version", "plan_keys", "corpus_sha256", "gold_sha256",
          "register_sha256", "schema_sha256")


def check_resume(old: dict, new: dict):
    diffs = [k for k in PINNED if old.get(k) != new.get(k)]
    if old.get("prompt_sha256", {}).get("sent_control") != new["prompt_sha256"]["sent_control"]:
        diffs.append("prompt_sha256.sent_control")
    if old.get("engine", {}).get("engine_sha256") != new["engine"]["engine_sha256"]:
        diffs.append("engine.engine_sha256")
    if diffs:
        raise SystemExit(f"[resume guard] the existing provenance differs on {diffs}: start a fresh output "
                         f"(--out-dir) or restore that configuration")


def execute(ctx: Ctx, inner, out_dir: Path, *, cap: float, current: str, echo=print) -> tuple[bool, object]:
    """Run every planned cell not yet logged. `inner` is the innermost client (Anthropic, or a
    fake for the preflight). Returns (complete, SubstitutingClient)."""
    from evidence.eval_run.logger import CachingClient, JsonlLogger
    res, prov_p = out_dir / f"{OUT_STEM}.jsonl", out_dir / f"{OUT_STEM}.provenance.json"
    ledger = Ledger(out_dir / f"{OUT_STEM}.spend.json")
    bad = [(r["qid"], r["seed"], why) for r in _load(res) if (why := ctx.incomplete_row(r))]
    if bad:
        raise SystemExit(f"{res.name} holds rows that are not completed cells {bad[:3]}: delete them first")
    base = provenance_base(ctx, current, cap)
    if prov_p.exists():
        prov = json.loads(prov_p.read_text())
        check_resume(prov, base)
        gi = git_info(ctx.root)
        if gi not in prov["git"]:
            prov["git"].append(gi)
        prov["cap_usd"] = cap
    else:
        prov = base
    sub = ctx.SubstitutingClient(inner, current, ctx.control, model=MODEL)
    client = CachingClient(sub, cache=True)
    adapter = ctx.adapter(client)
    if adapter.cache_prefix_text() != current:
        raise SystemExit("the adapter's rendered prompt changed between construction and run")
    logger = JsonlLogger(res)

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
    for qid, seed in ctx.cells:
        if logger.has(SYSTEM_ID, qid, seed):
            continue
        if spent() + ctx.worst_cell > cap:
            echo(f"  STOP before {qid} s{seed}: spent ${spent():.4f} + worst case ${ctx.worst_cell:.4f} "
                 f"would exceed the cap ${cap:.2f}. Re-run with a higher --cap-usd to continue.")
            save(False)
            return False, sub
        billed0, sent0 = sub.billed, dict(sub.sent)
        ledger.reserve(ctx.worst_cell, f"{qid}/{seed}")
        try:
            rec = ctx.H.run_cell(adapter, qid, ctx.corpus[qid]["question"], seed, ctx.gold[qid], model=MODEL)
        except Exception as exc:                        # noqa: BLE001 -- run_cell re-raises transport errors only
            ledger.settle(sub.billed - billed0)
            echo(f"  ABORT at {qid} s{seed}: {type(exc).__name__}: {exc}. The cell was not logged; "
                 f"fix the cause and re-run the same command to resume.")
            save(False)
            return False, sub
        billed = sub.billed - billed0
        ledger.settle(billed)
        if sub.refused:
            echo(f"  HALT at {qid} s{seed}: the prompt guard refused ({sub.refused[0]}). Nothing was sent "
                 f"and the cell was not logged.")
            save(False)
            return False, sub
        if rec["system"] != SYSTEM_ID:
            raise SystemExit(f"run_cell wrote system {rec['system']!r}")
        logger.emit(rec)
        for h, n in sub.sent.items():
            k = n - sent0.get(h, 0)
            if k:
                prov["sent_calls_by_sha256"][h] = prov["sent_calls_by_sha256"].get(h, 0) + k
        prov["cell_prompt"][f"{qid}/{seed}"] = _sha(ctx.control)
        save(False)
        echo(f"[{SYSTEM_ID} {qid:>6s} s{seed}] {rec['outcome_class']:<22s} in={rec['input_tokens']:>5d} "
             f"(u{rec['uncached_input_tokens']}/w{rec['cache_write_tokens']}/r{rec['cache_read_tokens']}) "
             f"out={rec['output_tokens']:>4d} ${rec['cost_usd']:.4f}  spent ${spent():.4f}")
        if billed > ctx.worst_cell + 1e-9:
            echo(f"  STOP after {qid} s{seed}: it billed ${billed:.4f}, more than its worst case "
                 f"${ctx.worst_cell:.4f}; the estimate no longer bounds the spend.")
            save(False)
            return False, sub
    save(True)
    return True, sub


# ── dry run: plan, estimate, offline preflight ──────────────────────────────

class _Usage:
    def __init__(self, i, o):
        self.input_tokens, self.output_tokens = i, o
        self.cache_creation_input_tokens = self.cache_read_input_tokens = 0


class _FakeInner:
    """No-API stand-in: records every call and answers with one fixed, valid Goal."""
    TEXT = ('{"goal": {"columns": ["employee_id"], "grain": "row", "where": [], "derive": []}, '
            '"order": {"order_by": [], "limit": null}}')

    def __init__(self, usage=(10, 5)):
        self.calls = []
        self.usage = usage
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        block = type("B", (), {"type": "text", "text": self.TEXT})()
        return type("R", (), {"content": [block], "usage": _Usage(*self.usage), "stop_reason": "end_turn"})()


def preflight(ctx: Ctx, current: str, scratch: Path) -> bool:
    """Every planned cell through the real adapter, harness and guard with a fake client, in a
    scratch directory: each call sends the control text (cache_control kept) at temperature 0 on
    the model of record with max_tokens 900 and the corpus question; 243 rows with system
    network_noalias; a second pass resumes with 0 calls; a cap stops before it is exceeded."""
    ok = True
    d = scratch / "preflight"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    try:
        fake = _FakeInner()
        done, sub = execute(ctx, fake, d, cap=1e9, current=current, echo=lambda *a: None)
        rows = _load(d / f"{OUT_STEM}.jsonl")
        bad = [c for c in fake.calls
               if c.get("temperature") != TEMP or c.get("model") != MODEL or c.get("max_tokens") != LLM1_MAX_TOKENS
               or len(c.get("system") or []) != 1 or c["system"][0].get("text") != ctx.control
               or c["system"][0].get("cache_control") != {"type": "ephemeral"}
               or len(c.get("messages") or []) != 1 or c["messages"][0].get("role") != "user"]
        qs = [c["messages"][0]["content"] for c in fake.calls]
        want_q = [ctx.corpus[q]["question"] for q, _ in ctx.cells]
        keys = {(r["system"], r["qid"], r["seed"]) for r in rows}
        prov = json.loads((d / f"{OUT_STEM}.provenance.json").read_text())
        c1 = (done and len(fake.calls) == len(ctx.cells) and not bad and qs == want_q and not sub.refused
              and len(rows) == len(ctx.cells) and keys == {tuple(k) for k in ctx.plan_keys}
              and set(sub.kinds) == {"swapped"} and set(sub.sent) == {_sha(ctx.control)}
              and prov["complete"] and prov["prompts"][SYSTEM_ID] == ctx.control
              and len(prov["cell_prompt"]) == len(ctx.cells))
        print(f"  preflight pass 1: {len(fake.calls)} calls, {len(rows)} rows, calls off-spec {len(bad)}, "
              f"questions in corpus order {qs == want_q}, swaps {dict(sub.kinds)}  {'ok' if c1 else 'FAILED'}")
        fake2 = _FakeInner()
        done2, _ = execute(ctx, fake2, d, cap=1e9, current=current, echo=lambda *a: None)
        c2 = done2 and not fake2.calls and len(_load(d / f"{OUT_STEM}.jsonl")) == len(ctx.cells)
        print(f"  preflight pass 2 (resume): {len(fake2.calls)} calls  {'ok' if c2 else 'FAILED'}")
        # cap: every fake call bills exactly the worst case, so a cap of 5.5 worst cases must
        # stop before the 6th cell, with the spend still under the cap
        d3 = scratch / "preflight_cap"
        if d3.exists():
            shutil.rmtree(d3)
        d3.mkdir()
        cap = 5.5 * ctx.worst_cell
        fake3 = _FakeInner(usage=(3 * ctx.in_max, LLM1_MAX_TOKENS))
        done3, _ = execute(ctx, fake3, d3, cap=cap, current=current, echo=lambda *a: None)
        n3 = len(_load(d3 / f"{OUT_STEM}.jsonl"))
        spent3 = json.loads((d3 / f"{OUT_STEM}.provenance.json").read_text())["cost"]["spent_usd"]
        c3 = (not done3) and n3 == len(fake3.calls) == 5 and spent3 <= cap < spent3 + ctx.worst_cell
        print(f"  preflight cap test (cap ${cap:.4f} = 5.5 worst cases, each fake call bills one): stopped "
              f"before cell {n3 + 1}, spent ${spent3:.4f} <= cap  {'ok' if c3 else 'FAILED'}")
        # a call that bills more than its reservation stops the run after that cell
        d4 = scratch / "preflight_over"
        if d4.exists():
            shutil.rmtree(d4)
        d4.mkdir()
        fake4 = _FakeInner(usage=(3 * ctx.in_max, LLM1_MAX_TOKENS + 1))
        done4, _ = execute(ctx, fake4, d4, cap=1e9, current=current, echo=lambda *a: None)
        c4 = (not done4) and len(fake4.calls) == 1 == len(_load(d4 / f"{OUT_STEM}.jsonl"))
        print(f"  preflight over-reservation test: stopped after {len(fake4.calls)} call  {'ok' if c4 else 'FAILED'}")
        ok = c1 and c2 and c3 and c4
    finally:
        for sub_d in ("preflight", "preflight_cap", "preflight_over"):
            shutil.rmtree(scratch / sub_d, ignore_errors=True)
    return ok


def dry(ctx: Ctx, cap: float, scratch: Path) -> int:
    current = ctx.current_prompt()
    rep = [ctx.reported[c] for c in ctx.cells]
    cached = sum(r.get("cost_usd_cached") or 0.0 for r in rep)
    raw = sum(r.get("cost_usd_raw") or 0.0 for r in rep)
    tin = sum(r.get("input_tokens") or 0 for r in rep)
    tout = sum(r.get("output_tokens") or 0 for r in rep)
    out_max = max(r.get("output_tokens") or 0 for r in rep)
    delta_chars = len(ctx.recorded) - len(ctx.control)
    print("=" * 96)
    print(f"ALIAS CONTROL ({SYSTEM_ID}): GDC, employee in-scope, s31 prompt minus the 'active employees' alias")
    print(f"  repository {ctx.root}  {git_info(ctx.root)}")
    print(f"  cells {len(ctx.cells)} = {len({q for q, _ in ctx.cells})} questions x seeds {list(SEEDS)}; "
          f"model {MODEL}, temperature {TEMP}, max_tokens {LLM1_MAX_TOKENS}, caching on, toggles off")
    print(f"  engine {ctx.engine_sha[:12]} (= reports/paper_numbers.engine.json), CONTRACT_VERSION "
          f"{ctx.contract_version}, gdc/*.py files {len(ctx.gdc_sha256)}")
    print(f"  prompt sha256  recorded s31          {_sha(ctx.recorded)}  ({len(ctx.recorded)} chars)")
    print(f"                 sent (control)        {_sha(ctx.control)}  ({len(ctx.control)} chars, "
          f"-{delta_chars})")
    print(f"                 current code (s48)    {_sha(current)}  ({len(current)} chars; swapped out on every "
          f"call){'' if _sha(current) == CURRENT_SHA256 else '  ** differs from the pinned render **'}")
    print("  diff:")
    for line in prompt_diff(ctx.recorded, ctx.control).splitlines():
        print("    " + line)
    print(f"  cost from the 243 reported employee GDC cells' recorded tokens: in {tin}, out {tout} "
          f"(max output {out_max} of {LLM1_MAX_TOKENS})")
    print(f"    realistic (recorded cache-aware bill) ${cached:.4f}; cache-blind ${raw:.4f}; "
          f"control prompt is {delta_chars} chars shorter (about {delta_chars // 4} tokens per call)")
    print(f"    worst case: ${ctx.worst_cell:.6f} per cell (3 x largest recorded input {ctx.in_max} tok + "
          f"{LLM1_MAX_TOKENS} out, cache-blind) x {len(ctx.cells)} = ${ctx.worst_cell * len(ctx.cells):.4f}; "
          f"cap ${cap:.2f}")
    print(f"  outputs on --run: {ctx.root / 'results' / (OUT_STEM + '.jsonl')} (+ .provenance.json, .spend.json)")
    print("=" * 96)
    ok = preflight(ctx, current, scratch)
    if NetGuard.hits:
        print(f"  NETWORK GUARD: {NetGuard.hits} connection attempt(s) blocked")
        ok = False
    print(f"  network guard active and never triggered: {NetGuard.self_test() and NetGuard.hits == 0}")
    print("  PREFLIGHT", "PASSED" if ok else "FAILED")
    print("\nDry run only (no API calls made). Re-invoke with --run to execute live.")
    return 0 if ok else 2


def run(ctx: Ctx, cap: float, out_dir: Path) -> int:
    """The key comes from ENV_PATH and nowhere else: main() disabled python-dotenv while the
    repository's modules were imported (gdc/config.py and scripts/probe_temperature.py call
    load_dotenv() at import), and it is re-enabled here for this one file. Never printed."""
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
    current = ctx.current_prompt()
    done, _ = execute(ctx, api, out_dir, cap=cap, current=current)
    n = len(_load(out_dir / f"{OUT_STEM}.jsonl"))
    print(f"\n{'COMPLETE' if done and n == len(ctx.cells) else 'INCOMPLETE'}: {n}/{len(ctx.cells)} cells")
    return 0 if done else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GDC employee alias control (network_noalias)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true", help="no API: plan, hashes, diff, estimate, offline preflight")
    g.add_argument("--run", action="store_true", help="live run (API)")
    ap.add_argument("--cap-usd", type=float, default=DEFAULT_CAP)
    ap.add_argument("--root", default=None, help="repository root (default: this script's repo, else the release)")
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
