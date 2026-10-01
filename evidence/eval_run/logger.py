"""Streaming, resumable JSONL logger + provenance writer.

One JSON line per (system, qid, seed) is APPENDED to results.jsonl the moment
the cell completes — never buffered, so a crash/interruption never loses work
and never forces a full re-run. On startup the logger reads the file and
populates `seen`; the harness consults `has(...)` before running each cell.

Provenance: written ONCE per run (at start), captures the full audit trail
(model, temperature, seeds, all 5 prompt texts, bounds, pricing, file hashes,
git commit, lib versions, timestamp).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


# ── streaming JSONL logger ──────────────────────────────────────────────────

class JsonlLogger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seen: set[tuple[str, str, int]] = set()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:                            # noqa: BLE001
                    continue                                 # skip malformed lines
                key = (r.get("system"), r.get("qid"), int(r.get("seed", 0)))
                self.seen.add(key)

    def has(self, system: str, qid: str, seed: int) -> bool:
        return (system, qid, int(seed)) in self.seen

    def emit(self, rec: dict) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        self.seen.add((rec["system"], rec["qid"], int(rec["seed"])))


# ── caching client wrapper (THE shared LLM-call wrapper for all 5 adapters) ──
# Two jobs, applied uniformly to every messages.create from every adapter
# (including the Network's llm1, which calls create internally and swallows the
# resp): (1) mark the STATIC prefix with cache_control ephemeral so prompt
# caching engages; (2) capture the four-category token ledger of the last call.

class CachingClient:
    """Pass-through wrapper around an Anthropic client.

    When `cache=True`, the `system` argument of every messages.create is converted
    to a list of content blocks with `cache_control: {"type": "ephemeral"}` on the
    LAST block. Per the API's tools -> system -> messages cache order, a breakpoint
    on the final system block caches the entire static prefix (tools + system); the
    per-query `messages` come after and stay uncached. The system text each adapter
    passes is byte-identical across queries/seeds/steps, so the prefix is reused.

    `last_ledger()` returns the last call's four token categories.
    """

    def __init__(self, inner, cache: bool = True):
        self._inner = inner
        self._cache = bool(cache)
        self._messages = _MessagesProxy(inner.messages, self)
        self._last = {"uncached": 0, "cache_write": 0, "cache_read": 0, "output": 0}

    @property
    def messages(self):
        return self._messages

    def last_ledger(self) -> dict:
        return dict(self._last)

    def last_usage(self) -> tuple[int, int]:
        """Back-compat: (raw_input, output)."""
        return (self._last["uncached"] + self._last["cache_write"]
                + self._last["cache_read"], self._last["output"])

    def __getattr__(self, name):
        return getattr(self._inner, name)


# Back-compat alias for any earlier import sites.
UsageCapturingClient = CachingClient


def _mark_cache_control(system):
    """Return a system value (list of content blocks) with cache_control on the
    last block. Accepts a plain string or an existing list of blocks. Returns the
    input unchanged if it is empty/None (nothing to cache)."""
    if not system:
        return system
    if isinstance(system, str):
        blocks = [{"type": "text", "text": system}]
    else:
        blocks = [dict(b) for b in system]            # copy; don't mutate caller's
    if not blocks:
        return system
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    return blocks


class _MessagesProxy:
    def __init__(self, inner, parent):
        self._inner = inner
        self._parent = parent

    def create(self, **kw):
        if self._parent._cache and kw.get("system"):
            kw = {**kw, "system": _mark_cache_control(kw["system"])}
        resp = self._inner.create(**kw)
        u = getattr(resp, "usage", None)
        self._parent._last = {
            "uncached":    int(getattr(u, "input_tokens", 0) or 0) if u else 0,
            "cache_write": int(getattr(u, "cache_creation_input_tokens", 0) or 0) if u else 0,
            "cache_read":  int(getattr(u, "cache_read_input_tokens", 0) or 0) if u else 0,
            "output":      int(getattr(u, "output_tokens", 0) or 0) if u else 0,
        }
        return resp


# ── provenance ──────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256(); h.update(Path(path).read_bytes()); return h.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    except Exception:                                        # noqa: BLE001
        return "<unknown>"


def sqlite_info() -> dict:
    """The SQLite text_to_sql runs on: the linked library's version, the Python
    sqlite3 module's own version (None where Python no longer has one, 3.14+),
    and whether the SQL functions that differ across SQLite builds exist. A build
    with the percentile functions answers MEDIAN; one without fails with
    'no such function', which text_to_sql records as a refusal."""
    import sqlite3
    fns = {}
    con = sqlite3.connect(":memory:")
    try:
        for fn, sql in (("median", "SELECT median(1)"), ("percentile", "SELECT percentile(1, 50)"),
                        ("corr", "SELECT corr(1, 1)")):
            try:
                con.execute(sql).fetchall()
                fns[fn] = True
            except sqlite3.Error:
                fns[fn] = False
    finally:
        con.close()
    return {"sqlite_version": sqlite3.sqlite_version,
            "sqlite3_module_version": getattr(sqlite3, "version", None), "functions": fns}


def _lib_versions() -> dict[str, str]:
    import importlib.metadata as md
    out = {}
    for pkg in ("anthropic", "pandas", "numpy"):
        try:
            out[pkg] = md.version(pkg)
        except Exception:                                    # noqa: BLE001
            out[pkg] = "<unknown>"
    out["python"] = sys.version.split()[0]
    sq = sqlite_info()
    out["sqlite"] = sq["sqlite_version"]
    out["sqlite3_module"] = sq["sqlite3_module_version"]
    return out


def write_provenance(path: Path, *, model: str, temperature: float | None,
                     seeds: list[int], bounds: dict, pricing: dict,
                     corpus_path: Path, gold_path: Path, prompts: dict[str, str],
                     cache: dict | None = None, comparator_version: str | None = None,
                     network_prompt_version: str | None = None,
                     refuse_unsupported: bool = False,
                     emission_model: str | None = None,
                     contract_version: str | None = None) -> None:
    prov = {
        "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
        "model": model,
        "emission_model": emission_model,           # S33: resolved Call-1 model (network/planner)
        "contract_version": contract_version,       # S35: goal contract + lint version
        "temperature": temperature,
        "repetitions": list(seeds),          # count/index labels, NOT determinism seeds (no such API param)
        "bounds": dict(bounds),
        "pricing": dict(pricing),
        "cache": dict(cache) if cache else None,
        "corpus_path": str(corpus_path),
        "corpus_sha256": _sha256(corpus_path),
        "gold_path": str(gold_path),
        "gold_sha256": _sha256(gold_path),
        "comparator_version": comparator_version,
        "network_prompt_version": network_prompt_version,
        "refuse_unsupported": refuse_unsupported,
        "git_commit": _git_commit(),
        "lib_versions": _lib_versions(),
        "prompts": dict(prompts),
        "env": {"ANTHROPIC_API_KEY_present": bool(os.getenv("ANTHROPIC_API_KEY"))},
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(prov, indent=2))
