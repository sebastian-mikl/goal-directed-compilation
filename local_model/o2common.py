"""Shared driver for the O2 local-model runs (smoke test and full run).

Drives cells through the UNCHANGED harness path (evidence.eval_run.harness.run_cells),
one (system, qid) at a time, as scripts/rerun_temp0_batch2.py does, from the scratch
copy at OUT/tree (git archive of public-release 8605f52 plus local_model.patch).

Configuration (fixed): model qwen3-coder-16k:30b-a3b-q4_K_M (an Ollama alias of
qwen3-coder:30b-a3b-q4_K_M, same weights, num_ctx 16384; OUT/Modelfile.16k) for all five
systems (GDC's Call-1 included), temperature 0 on every request (the alias's Modelfile
default is 0.7; LocalOnlyClient refuses any request not at 0), prompt caching on, bounds react 15 iterations /
code_agent 12 retries / 60 s per-query timeout, every refuse/decline toggle OFF
(the S40-off configuration behind the paper's Spider headline numbers for GDC).
The client points at http://localhost:11434 only (localguard).
"""
from __future__ import annotations

import localguard as G          # noqa: F401  -- FIRST: env, dotenv and socket guards

import datetime
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent
TREE = OUT / "tree"
PR_COMMIT = "8605f521f4d106509b79ca49f0f8fc439dc2fd3f"
MODEL = "qwen3-coder-16k:30b-a3b-q4_K_M"      # Ollama alias: FROM BASE_MODEL, PARAMETER num_ctx 16384
BASE_MODEL = "qwen3-coder:30b-a3b-q4_K_M"
NUM_CTX = 16384
TEMP = 0
BOUNDS = {"react_max_iters": 15, "code_max_retries": 12, "per_query_timeout_s": 60}
SYSTEMS = ("network", "planner", "react", "code_agent", "text_to_sql")
GUARD = "refusing to send"
OLLAMA_LOG = Path.home() / ".ollama" / "logs" / "server.log"

# toggles that would change any system's prompt or model: all OFF / unset
for _k in ("REFUSE_UNSUPPORTED", "REFUSE_OUT_OF_SCOPE_DATA", "DECLINE_AFFORDANCE",
           "EMISSION_MODEL", "MODEL_CALL1", "MODEL_CALL2", "MODEL_CALL3",
           "PARITY_TEMP0", "PARTIAL_BATCH2"):
    os.environ.pop(_k, None)

sys.path.insert(0, str(TREE))
os.chdir(OUT)                   # not a git checkout: nothing can touch a repo from here

import gdc.config as GC                                    # noqa: E402
import evidence.eval_run.harness as H                       # noqa: E402
from evidence.eval_run.comparator import COMPARATOR_VERSION  # noqa: E402
from evidence.eval_run.logger import sqlite_info            # noqa: E402
from evidence.eval_run.pricing import PRICING_USD_PER_MTOK  # noqa: E402
from planner.llm import GOAL_PROMPT_VERSION                 # noqa: E402

assert GC.LOCAL_BASE_URL == G.LOCAL_BASE_URL, (GC.LOCAL_BASE_URL, G.LOCAL_BASE_URL)
assert str(GC.CLIENT.base_url).rstrip("/") == G.LOCAL_BASE_URL, GC.CLIENT.base_url
assert GC.MODEL_CALL1 == MODEL and GC.MODEL_CALL2 == MODEL and GC.MODEL_CALL3 == MODEL
assert PRICING_USD_PER_MTOK[MODEL] == {"input": 0.0, "output": 0.0}
assert Path(H.__file__).resolve().is_relative_to(TREE), H.__file__

DOMAINS = {
    "employee": {"corpus": TREE / "eval_corpus_employee.json",
                 "gold": TREE / "evidence" / "gold" / "gold_employee.json",
                 "register": TREE / "probe_domain" / "employees.pkl",
                 "schema": TREE / "probe_domain" / "schema.json"},
    "wta_1": {"corpus": TREE / "eval_corpus_spider_wta_1.json",
              "gold": TREE / "evidence" / "gold" / "gold_spider_wta_1.json",
              "register": TREE / "wta_1" / "matches.pkl", "schema": TREE / "wta_1" / "schema.json"},
    "world_1": {"corpus": TREE / "eval_corpus_spider_world_1.json",
                "gold": TREE / "evidence" / "gold" / "gold_spider_world_1.json",
                "register": TREE / "world_1" / "country.pkl", "schema": TREE / "world_1" / "schema.json"},
    "bike_1": {"corpus": TREE / "eval_corpus_spider_bike_1.json",
               "gold": TREE / "evidence" / "gold" / "gold_spider_bike_1.json",
               "register": TREE / "bike_1" / "trip.pkl", "schema": TREE / "bike_1" / "schema.json"},
}


def sha256_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _get(path: str, body: dict | None = None) -> dict:
    url = G.LOCAL_BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _modelfile_param(params: str | None, name: str):
    for ln in (params or "").splitlines():
        parts = ln.split(None, 1)
        if len(parts) == 2 and parts[0] == name:
            return parts[1].strip()
    return None


def _weights_blob(modelfile: str | None):
    for ln in (modelfile or "").splitlines():
        if ln.startswith("FROM ") and "sha256-" in ln:
            return ln.split("sha256-", 1)[1].strip()
    return None


def ollama_facts() -> dict:
    """Ollama version; the alias's name, digest, num_ctx, parameters and weights blob;
    the base model's digest and weights blob; and the context the loaded runner has
    (None when no runner of the alias is loaded)."""
    ver = _get("/api/version").get("version")
    tags = {m["name"]: m for m in _get("/api/tags").get("models", [])}
    show = _get("/api/show", {"model": MODEL})
    base = _get("/api/show", {"model": BASE_MODEL})
    ps = {m["name"]: m for m in _get("/api/ps").get("models", [])}
    t = tags.get(MODEL, {})
    num_ctx = _modelfile_param(show.get("parameters"), "num_ctx")
    return {"ollama_version": ver, "model": MODEL, "alias": MODEL, "digest": t.get("digest"),
            "num_ctx": int(num_ctx) if num_ctx else None,
            "modelfile_temperature": _modelfile_param(show.get("parameters"), "temperature"),
            "weights_blob_sha256": _weights_blob(show.get("modelfile")),
            "base_model": BASE_MODEL, "base_digest": tags.get(BASE_MODEL, {}).get("digest"),
            "base_weights_blob_sha256": _weights_blob(base.get("modelfile")),
            "size_bytes": t.get("size"), "details": show.get("details"),
            "parameters": show.get("parameters"),
            "model_context_length": (show.get("model_info") or {}).get("qwen3moe.context_length"),
            "loaded_runner": ({k: ps[MODEL].get(k) for k in ("context_length", "size", "size_vram")}
                              if MODEL in ps else None),
            "loaded_models": sorted(ps),
            "endpoint": G.LOCAL_BASE_URL + "/v1/messages"}


def provenance_block(kind: str, driver: Path) -> dict:
    return {
        "kind": kind, "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_commit": PR_COMMIT, "source": "git archive of public-release HEAD into OUT/tree",
        "local_model_patch_sha256": sha256_file(OUT / "local_model.patch"),
        "driver_sha256": {driver.name: sha256_file(driver), "o2common.py": sha256_file(OUT / "o2common.py"),
                          "localguard.py": sha256_file(OUT / "localguard.py")},
        "ollama": ollama_facts(), "host": {"machine": platform.machine(), "platform": platform.platform(),
                                           "python": platform.python_version()},
        "model": MODEL, "temperature": TEMP, "bounds": BOUNDS, "use_cache": True,
        "goal_prompt_version": GOAL_PROMPT_VERSION, "comparator_version": COMPARATOR_VERSION,
        "toggles": {"REFUSE_UNSUPPORTED": False, "REFUSE_OUT_OF_SCOPE_DATA": False,
                    "DECLINE_AFFORDANCE": False, "s40": "off"},
        "sqlite": sqlite_info(),
        "guard": {"client_base_url": str(GC.CLIENT.base_url), "ANTHROPIC_API_KEY_present":
                  "ANTHROPIC_API_KEY" in os.environ, "dotenv_attempts_blocked": list(G.DOTENV_ATTEMPTS)},
    }


class LocalOnlyClient:
    """Innermost client: refuses any call not on MODEL at temperature 0, and logs every
    call (latency, tokens, stop reason, tool-use count) to a sidecar JSONL."""

    def __init__(self, calls_log: Path, session: str | None = None):
        self._inner = G.guarded_client()
        self.messages = self
        self.calls_log = Path(calls_log)
        self.session = session
        self.current = {}
        self.cell_calls: list[dict] = []
        self.refused: list[str] = []

    def _refuse(self, why: str):
        self.refused.append(why)
        raise RuntimeError(f"{GUARD}: {why}")

    def create(self, **kw):
        t = kw.get("temperature")
        if isinstance(t, bool) or t != TEMP:
            self._refuse(f"temperature={t!r}, expected {TEMP}")
        if kw.get("model") != MODEL:
            self._refuse(f"model={kw.get('model')!r}, expected {MODEL}")
        t_start, p0 = time.time(), time.perf_counter()
        err = None
        try:
            resp = self._inner.messages.create(**kw)
            return resp
        except Exception as exc:                          # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            resp = None
            raise
        finally:
            u = getattr(resp, "usage", None)
            blocks = list(getattr(resp, "content", []) or [])
            rec = {**self.current, "session": self.session, "t_start": t_start, "t_end": time.time(),
                   "latency_s": round(time.perf_counter() - p0, 3),
                   "in": int(getattr(u, "input_tokens", 0) or 0) if u else None,
                   "cache_read": int(getattr(u, "cache_read_input_tokens", 0) or 0) if u else None,
                   "out": int(getattr(u, "output_tokens", 0) or 0) if u else None,
                   "stop": getattr(resp, "stop_reason", None),
                   "tool_uses": sum(1 for b in blocks if getattr(b, "type", None) == "tool_use"),
                   "max_tokens": kw.get("max_tokens"), "n_messages": len(kw.get("messages") or []),
                   "error": err}
            self.cell_calls.append(rec)
            with open(self.calls_log, "a") as fh:
                fh.write(json.dumps(rec) + "\n")


class _Halt(Exception):
    pass


def cell_context_record(domain, sysn, qid, seeds, calls, off0, off1) -> dict:
    """Per-cell context-loss flag, computed right after the cell from the Ollama server
    log segment the cell produced, aligned call by call (one server task per request)."""
    tasks = server_tasks(off0, off1)
    aligned = len(tasks) == len(calls)
    calls = [dict(c) for c in calls]
    for c, t in zip(calls, tasks if aligned else []):
        c.update(prompt_tokens=t["prompt_tokens"], n_ctx=t["n_ctx"], temp=t["temp"],
                 truncated_from=t["input_truncated_from"], context_shifts=t["context_shifts"],
                 released_truncated=t["released_truncated"])
    mark_context_loss(calls)
    ev = sorted({e for c in calls for e in (c.get("loss_events") or [])})
    pt = [c.get("prompt_tokens") or 0 for c in calls]
    return {"domain": domain, "system": sysn, "qid": qid, "seeds": list(seeds), "calls": len(calls),
            "server_tasks": len(tasks), "aligned": aligned,
            "n_ctx": sorted({c.get("n_ctx") for c in calls if c.get("n_ctx")}),
            "temps": sorted({c.get("temp") for c in calls if c.get("temp") is not None}),
            "max_prompt_tokens": max(pt, default=0),
            "max_prompt_plus_out": max(((c.get("prompt_tokens") or 0) + (c.get("out") or 0) for c in calls),
                                       default=0),
            "loss_events": ev,
            "calls_lost_context": sum(1 for c in calls if c.get("context_lost")),
            # unaligned = cannot be checked: flagged, not assumed clean
            "context_lost": bool(ev) or not aligned}


def run_domain(domain: str, qids: list[str], systems, seeds, results: Path, prov: Path,
               client: LocalOnlyClient, echo=print, cell_log: Path | None = None) -> dict:
    """Every (system, qid, seed) cell of one domain through H.run_cells, system-outer,
    one qid per call so the call log knows its cell. Stops on a transport abort or a
    guard refusal; resumable (logged cells are skipped)."""
    cfg = DOMAINS[domain]
    tot = {"ran": 0, "skipped": 0, "aborted": False, "abort": None}

    def progress(msg):
        echo(msg)
        if client.refused:
            raise _Halt(client.refused[0])

    for sysn in systems:
        for qid in qids:
            client.current = {"domain": domain, "system": sysn, "qid": qid}
            client.cell_calls = []
            off0 = server_log_offset()
            try:
                s = H.run_cells(qids=[qid], seeds=list(seeds), systems=[sysn],
                                client_factory=lambda: client, model=MODEL, temperature=TEMP,
                                use_cache=True, bounds=BOUNDS, corpus_path=cfg["corpus"],
                                gold_path=cfg["gold"], register_path=cfg["register"],
                                schema_path=cfg["schema"], results_path=results,
                                provenance_path=prov, progress=progress)
            except _Halt as exc:
                tot.update(aborted=True, abort={"guard": str(exc), "system": sysn, "qid": qid})
                return tot
            tot["ran"] += s["ran"]
            tot["skipped"] += s["skipped"]
            if s["ran"] and cell_log is not None:
                time.sleep(0.3)                      # let the server flush the request's log lines
                rec = cell_context_record(domain, sysn, qid, seeds, client.cell_calls, off0,
                                          server_log_offset())
                with open(cell_log, "a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                if rec["context_lost"]:
                    echo(f"  [CONTEXT LOSS] {domain} {sysn} {qid}: events {rec['loss_events']} "
                         f"aligned {rec['aligned']} max prompt {rec['max_prompt_tokens']} / n_ctx {rec['n_ctx']}")
            if s.get("aborted"):
                tot.update(aborted=True, abort=s["abort"])
                return tot
    return tot


def add_o2_provenance(prov: Path, block: dict) -> None:
    p = json.loads(prov.read_text())
    runs = p.setdefault("o2_local_model", [])
    runs.append(block)
    prov.write_text(json.dumps(p, indent=2))


# ── Ollama server-log scan: silently truncated prompts ──────────────────────
_TRUNC = re.compile(r'^time=(\S+) level=WARN .*msg="truncating input prompt" limit=(\d+) prompt=(\d+)')


def truncations(since_epoch: float, until_epoch: float | None = None) -> list[dict]:
    out = []
    try:
        lines = OLLAMA_LOG.read_text(errors="replace").splitlines()
    except Exception:                                      # noqa: BLE001
        return out
    for ln in lines:
        m = _TRUNC.match(ln)
        if not m:
            continue
        ts = datetime.datetime.fromisoformat(m.group(1)).timestamp()
        if ts >= since_epoch and (until_epoch is None or ts <= until_epoch):
            out.append({"t": ts, "limit": int(m.group(2)), "prompt": int(m.group(3))})
    return out


def attach_truncations(calls: list[dict], truncs: list[dict]) -> None:
    """Mark each logged call that a truncation warning falls inside (by wall clock)."""
    for c in calls:
        c["truncated_from"] = None
    for tr in truncs:
        for c in calls:
            if c["t_start"] - 0.05 <= tr["t"] <= c["t_end"] + 0.05:
                c["truncated_from"] = tr["prompt"]
                break


# ── Ollama server-log scan, per request: input truncation and context shifts ──
_NEWP = re.compile(r"new prompt, n_ctx_slot = (\d+), n_keep = (\d+), task\.n_tokens = (\d+)")
_TEMP = re.compile(r"top_k = \d+, top_p = [\d.]+, .*\btemp = ([\d.]+)")
_REL = re.compile(r"release: .*stop processing: n_tokens = (\d+), truncated = (\d)")
_GIN_POST = re.compile(r"^\[GIN\] (\d{4}/\d\d/\d\d - \d\d:\d\d:\d\d) \| (\d+) \|.*POST\s+\"/v1/messages\"")


def server_log_offset() -> int:
    try:
        return OLLAMA_LOG.stat().st_size
    except Exception:                                      # noqa: BLE001
        return 0


def server_tasks(offset: int = 0, until: int | None = None) -> list[dict]:
    """One entry per /v1/messages request in the Ollama server log from byte `offset`:
    the prompt length llama-server saw, whether Ollama truncated the input first
    ('truncating input prompt', prompt -> limit), the context shifts during the request,
    the release's `truncated` flag, and the HTTP status of the request."""
    try:
        with open(OLLAMA_LOG, "rb") as fh:
            fh.seek(offset)
            data = fh.read() if until is None else fh.read(max(0, until - offset))
    except Exception:                                      # noqa: BLE001
        return []
    tasks, pending_trunc, pending_temp, cur = [], None, None, None
    for ln in data.decode(errors="replace").splitlines():
        m = _TRUNC.match(ln)
        if m:
            pending_trunc = {"limit": int(m.group(2)), "prompt": int(m.group(3))}
            continue
        m = _TEMP.search(ln)
        if m:
            pending_temp = float(m.group(1))
            continue
        m = _NEWP.search(ln)
        if m:
            cur = {"n_ctx": int(m.group(1)), "prompt_tokens": int(m.group(3)),
                   "input_truncated_from": pending_trunc["prompt"] if pending_trunc else None,
                   "context_shifts": 0, "released_truncated": None, "http": None, "temp": pending_temp}
            pending_trunc = pending_temp = None
            tasks.append(cur)
            continue
        if "slot context shift" in ln and cur is not None:
            cur["context_shifts"] += 1
            continue
        m = _REL.search(ln)
        if m and cur is not None:
            cur["released_truncated"] = int(m.group(2))
            continue
        m = _GIN_POST.match(ln)
        if m:
            for t in reversed(tasks):
                if t["http"] is None:
                    t["http"] = int(m.group(2))
                    break
    return tasks


def mark_context_loss(calls: list[dict]) -> None:
    """Mark, in place, every call that ran without part of its episode's context, from the
    first loss event of the episode (domain, system, qid) onward. Three mechanisms at a
    small Ollama context (each found in the smoke test's server log or call sizes):
      dropped_messages  Ollama silently drops the oldest chat messages to fit the context
                        (no log line): the prompt shrinks although the harness sent more
                        messages than on the previous call of the episode;
      input_truncated   Ollama truncates an over-long input ('truncating input prompt',
                        first 4 tokens kept);
      context_shift     llama-server discards half the context during generation.
    Calls need the server-log fields from server_tasks (prompt_tokens etc.), in order."""
    last: dict[tuple, dict] = {}
    for c in calls:
        k = (c.get("domain"), c.get("system"), c.get("qid"), c.get("session"))
        prev = last.get(k)
        kinds = []
        if c.get("truncated_from"):
            kinds.append("input_truncated")
        if c.get("context_shifts") or c.get("released_truncated"):
            kinds.append("context_shift")
        if (prev is not None and c.get("prompt_tokens") is not None and prev.get("prompt_tokens") is not None
                and (c.get("n_messages") or 0) > (prev.get("n_messages") or 0)
                and c["prompt_tokens"] < prev["prompt_tokens"]):
            kinds.append("dropped_messages")
        c["loss_events"] = kinds
        # a dropped message or a truncated input stays lost for the rest of the episode;
        # a context shift corrupts the call it happens in (the next call re-sends everything)
        c["context_lost_carry"] = bool({"dropped_messages", "input_truncated"} & set(kinds)) or bool(
            prev and prev.get("context_lost_carry"))
        c["context_lost"] = bool(kinds) or c["context_lost_carry"]
        last[k] = c
