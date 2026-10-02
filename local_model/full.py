#!/usr/bin/env python
"""O2.c full run: the public domains (employee, Spider wta_1 / world_1 / bike_1), ONE seed
(0), all five systems, on the local model through the unchanged harness. Resumable: run
the same command again and completed cells are skipped (harness logger); an aborted cell
retries. Launched by run_full.sh with O2_CTX=16384 (see preflight). Model: the Ollama alias
qwen3-coder-16k:30b-a3b-q4_K_M (qwen3-coder:30b-a3b-q4_K_M weights, num_ctx 16384).

Outputs (OUT/results/): results_o2_<domain>.jsonl + .provenance.json (the harness's own
provenance plus an `o2_local_model` list, one block per session: model, Ollama version,
digest, quantisation, source commit, patch / driver / tree-manifest sha256, server
context, server-log offsets, summary), calls_<domain>.jsonl (one line per model call),
cell_context_<domain>.jsonl (one line per completed cell, written right after it: the
context-loss flag, its events, the largest prompt and the n_ctx and sampling temperature
the server applied), and summary.json at the end.
"""
from __future__ import annotations

import o2common as C            # FIRST (imports localguard)

import hashlib
import json
import os
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

RESULTS = C.OUT / "results"
SEEDS = [0]
ORDER = ("employee", "wta_1", "world_1", "bike_1")
SCOPE_FILE = C.OUT / "full_scope.json"
MANIFEST = C.OUT / "tree_manifest.sha256"
EXPECTED_SCOPE = {"employee": 91, "wta_1": 30, "world_1": 30, "bike_1": 25}


def fail(msg: str):
    print(f"[preflight] REFUSED: {msg}")
    sys.exit(2)


def verify_tree():
    want = {}
    for ln in MANIFEST.read_text().splitlines():
        h, p = ln.split("  ", 1)
        want[p] = h
    have = {"./" + str(p.relative_to(C.TREE)) for p in C.TREE.rglob("*") if p.is_file()}
    extra, missing = sorted(have - set(want)), sorted(set(want) - have)
    if extra or missing:
        fail(f"tree file set changed: extra {extra[:5]} missing {missing[:5]}")
    bad = [p for p, h in want.items()
           if hashlib.sha256((C.TREE / p[2:]).read_bytes()).hexdigest() != h]
    if bad:
        fail(f"tree files changed since the smoke test: {bad[:5]}")
    return len(want)


def preflight(smoke_dir: Path) -> dict:
    st = C.G.self_test()
    if any(v == "NOT BLOCKED" for v in st.values()) or st["ANTHROPIC_API_KEY_present"]:
        fail(f"guard self-test {st}")
    # self_test() just wrote its own 3 expected "blocked" lines + a note to VIOLATIONS_LOG
    # (by design: "the self-test's own violations are expected; mark them so the log is
    # unambiguous"). Consume them here so the check below catches only a REAL violation
    # from the run that follows, not the self-test's own designed-in noise.
    C.G.VIOLATIONS_LOG.unlink(missing_ok=True)
    if C.G.VIOLATIONS_LOG.exists() and C.G.VIOLATIONS_LOG.read_text().strip():
        fail(f"{C.G.VIOLATIONS_LOG} is not empty: a non-local request was blocked earlier; inspect it")
    n_files = verify_tree()
    scope = json.loads(SCOPE_FILE.read_text())
    if {k: len(v) for k, v in scope.items()} != EXPECTED_SCOPE:
        fail(f"scope changed: {({k: len(v) for k, v in scope.items()})}")
    crit = json.loads((smoke_dir / "criteria.json").read_text())
    if not (crit["c1"] and crit["c2"] and crit["c3"]):
        fail(f"the smoke test in {smoke_dir} did not pass: {crit}")
    if crit.get("model") != C.MODEL:
        fail(f"the smoke test in {smoke_dir} ran on {crit.get('model')}, not {C.MODEL}")
    if crit.get("context_lost_calls") != 0 or not crit.get("aligned"):
        fail(f"the smoke test in {smoke_dir} lost context or could not be checked: {crit}")
    if crit.get("temps") != [0.0]:
        fail(f"the smoke test's server-side sampling temperatures were {crit.get('temps')}, not [0.0]")
    sprov = json.loads((smoke_dir / "results_o2_smoke_employee.provenance.json").read_text())
    so = sprov["o2_local_model"][-1]
    if so["local_model_patch_sha256"] != C.sha256_file(C.OUT / "local_model.patch"):
        fail("local_model.patch differs from the smoke test's")
    # load the model with one tiny request, then read the runner's context
    c = C.G.guarded_client()
    c.messages.create(model=C.MODEL, max_tokens=1, temperature=0,
                      messages=[{"role": "user", "content": "ok"}])
    of = C.ollama_facts()
    if of["ollama_version"] != so["ollama"]["ollama_version"] or of["digest"] != so["ollama"]["digest"]:
        fail(f"Ollama/model differ from the smoke test: {of['ollama_version']} {of['digest']} vs "
             f"{so['ollama']['ollama_version']} {so['ollama']['digest']}")
    ctx = (of["loaded_runner"] or {}).get("context_length")
    if of["num_ctx"] != C.NUM_CTX or ctx != C.NUM_CTX:
        fail(f"{C.MODEL}: Modelfile num_ctx {of['num_ctx']}, loaded runner context {ctx}; both must be "
             f"{C.NUM_CTX}")
    if not of["weights_blob_sha256"] or of["weights_blob_sha256"] != of["base_weights_blob_sha256"]:
        fail(f"{C.MODEL} weights {of['weights_blob_sha256']} differ from {C.BASE_MODEL}'s "
             f"{of['base_weights_blob_sha256']}")
    want_ctx = os.environ.get("O2_CTX")
    if want_ctx is None or int(want_ctx) != ctx:
        fail(f"the loaded runner has context {ctx}; set O2_CTX={ctx} to confirm it. At 4096 a react "
             f"or code_agent episode whose prompt + reply exceeds 4096 tokens loses context (Ollama "
             f"silently drops the oldest messages, the question included, or shifts the context); "
             f"the smoke test saw this in react t5_01 (9 of its 12 calls). Raising Ollama's context length is a server "
             f"setting outside this run; if it is changed, re-run the smoke test at the new context "
             f"(smoke.py <dir>, analyse_smoke.py <dir>) and pass --smoke-dir <dir>.")
    if crit.get("n_ctx") != [ctx]:
        fail(f"the smoke test in {smoke_dir} ran at context {crit.get('n_ctx')}, the server now has {ctx}")
    print(f"[preflight] ok: guard {st}; tree {n_files} files unchanged; scope {EXPECTED_SCOPE}; "
          f"Ollama {of['ollama_version']} {C.MODEL} {of['digest'][:12]} "
          f"{(of['details'] or {}).get('quantization_level')}; weights {of['weights_blob_sha256'][:12]} "
          f"(= {C.BASE_MODEL}); context {ctx}; smoke {smoke_dir.name} passed")
    return {"guard_self_test": st, "tree_files": n_files, "tree_manifest_sha256": C.sha256_file(MANIFEST),
            "scope_sha256": C.sha256_file(SCOPE_FILE), "smoke_dir": str(smoke_dir),
            "smoke_criteria": crit, "context_length": ctx}


SESSIONS = RESULTS / "sessions.jsonl"


def _session_log(rec: dict) -> None:
    RESULTS.mkdir(exist_ok=True)
    with open(SESSIONS, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def _sessions() -> list[dict]:
    """Sessions in start order with their server-log byte range; a session killed before
    its end record ends where the next one starts (or at the end of the log)."""
    if not SESSIONS.exists():
        return []
    recs = [json.loads(l) for l in SESSIONS.read_text().splitlines() if l.strip()]
    starts = [r for r in recs if r["event"] == "start"]
    ends = {r["session"]: r for r in recs if r["event"] == "end"}
    out = []
    for i, s in enumerate(starts):
        e = ends.get(s["session"])
        nxt = starts[i + 1]["server_log_offset"] if i + 1 < len(starts) else None
        out.append({"session": s["session"], "domain": s["domain"], "server_log_offset": s["server_log_offset"],
                    "server_log_offset_end": e["server_log_offset"] if e else nxt, "ended": bool(e)})
    return out


def context_loss() -> dict:
    """Per domain and system: calls (and cells) whose prompt was truncated or whose
    context shifted, from the Ollama server log, aligned session by session."""
    out = {}
    for dom in ORDER:
        calls_p = RESULTS / f"calls_{dom}.jsonl"
        if not calls_p.exists():
            continue
        calls = [json.loads(l) for l in calls_p.read_text().splitlines() if l.strip()]
        sessions = [x for x in _sessions() if x["domain"] == dom]
        lost_calls, lost_cells, tot, aligned_all = Counter(), defaultdict(set), Counter(), True
        events, annotated = Counter(), []
        for s in sessions:
            mine = [c for c in calls if c.get("session") == s.get("session")]
            tasks = C.server_tasks(s.get("server_log_offset", 0), s.get("server_log_offset_end"))
            aligned = len(tasks) == len(mine)
            aligned_all &= aligned
            for c, t in zip(mine, tasks if aligned else []):
                c.update(prompt_tokens=t["prompt_tokens"], n_ctx=t["n_ctx"],
                         truncated_from=t["input_truncated_from"], context_shifts=t["context_shifts"],
                         released_truncated=t["released_truncated"])
            C.mark_context_loss(mine)
            for c in mine:
                tot[c["system"]] += 1
                if c.get("context_lost"):
                    lost_calls[c["system"]] += 1
                    lost_cells[c["system"]].add(c["qid"])
                for e in c.get("loss_events") or []:
                    events[e] += 1
            annotated.extend(mine)
        (RESULTS / f"calls_{dom}_annotated.jsonl").write_text("".join(json.dumps(c) + "\n" for c in annotated))
        out[dom] = {"aligned": aligned_all, "loss_events": dict(events),
                    "by_system": {s: {"calls": tot[s], "calls_lost_context": lost_calls[s],
                                      "cells_lost_context": sorted(lost_cells[s]),
                                  "n_cells_lost_context": len(lost_cells[s])} for s in C.SYSTEMS}}
    return out


def report(scope: dict) -> dict:
    summ = {}
    for dom in ORDER:
        p = RESULTS / f"results_o2_{dom}.jsonl"
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
        have = {(r["system"], r["qid"]) for r in rows}
        want = {(s, q) for s in C.SYSTEMS for q in scope[dom]}
        summ[dom] = {"cells": len(rows), "planned": len(want), "missing": len(want - have),
                     "status": {s: dict(Counter(r["status"] for r in rows if r["system"] == s)) for s in C.SYSTEMS},
                     "verdict": {s: dict(Counter(r["comparator_verdict"] for r in rows if r["system"] == s))
                                 for s in C.SYSTEMS},
                     "wall_hours": round(sum(r["wall_time_s"] for r in rows) / 3600, 3)}
        cp = RESULTS / f"cell_context_{dom}.jsonl"
        cc = [json.loads(l) for l in cp.read_text().splitlines() if l.strip()] if cp.exists() else []
        last = {(x["system"], x["qid"]): x for x in cc}      # a re-run cell keeps its latest record
        summ[dom]["cell_context"] = {
            "cells_flagged": len(last), "cells_without_flag": sorted(f"{s}/{q}" for s, q in have - set(last)),
            "context_lost": sorted(f"{s}/{q}" for (s, q), x in last.items() if x["context_lost"]),
            "max_prompt_by_system": {s: max([x["max_prompt_tokens"] for (t, _), x in last.items() if t == s],
                                            default=0) for s in C.SYSTEMS},
            "n_ctx": sorted({n for x in last.values() for n in x["n_ctx"]}),
            "temps": sorted({t for x in last.values() for t in x["temps"]})}
    return summ


def main(argv):
    smoke_dir = C.OUT / "smoke_ctx16k"
    if "--smoke-dir" in argv:
        smoke_dir = Path(argv[argv.index("--smoke-dir") + 1]).resolve()
    pf = preflight(smoke_dir)
    if "--preflight-only" in argv:
        return 0
    scope = json.loads(SCOPE_FILE.read_text())
    RESULTS.mkdir(exist_ok=True)
    for dom in ORDER:
        results, prov = RESULTS / f"results_o2_{dom}.jsonl", RESULTS / f"results_o2_{dom}.provenance.json"
        session = uuid.uuid4().hex[:12]
        client = C.LocalOnlyClient(RESULTS / f"calls_{dom}.jsonl", session=session)
        block = C.provenance_block(f"O2.c full run, {dom}", Path(__file__).resolve())
        block.update(session=session, preflight=pf, server_log_offset=C.server_log_offset(),
                     started_epoch=time.time(), qids=scope[dom], seeds=SEEDS)
        _session_log({"session": session, "domain": dom, "event": "start",
                      "epoch": block["started_epoch"], "server_log_offset": block["server_log_offset"]})
        print(f"\n=== {dom}: {len(scope[dom])} qids x {len(C.SYSTEMS)} systems, session {session} ===",
              flush=True)

        def echo(msg):
            print(msg, flush=True)
        summary = C.run_domain(dom, scope[dom], C.SYSTEMS, SEEDS, results, prov, client, echo=echo,
                               cell_log=RESULTS / f"cell_context_{dom}.jsonl")
        block.update(finished_epoch=time.time(), server_log_offset_end=C.server_log_offset(),
                     summary=summary, ollama_after=C.ollama_facts(),
                     guard_after={"requests_to_localhost_11434": C.G.REQUESTS_SEEN["localhost_11434"],
                                  "dotenv_attempts_blocked": list(C.G.DOTENV_ATTEMPTS),
                                  "violations_log_nonempty": C.G.VIOLATIONS_LOG.exists()
                                  and bool(C.G.VIOLATIONS_LOG.read_text().strip())})
        _session_log({"session": session, "domain": dom, "event": "end", "epoch": block["finished_epoch"],
                      "server_log_offset": block["server_log_offset_end"], "summary": summary})
        if prov.exists():
            C.add_o2_provenance(prov, block)
        if summary["aborted"]:
            print(f"\nABORTED in {dom}: {summary['abort']}\nCompleted cells are saved; fix the cause "
                  f"(e.g. Ollama not running) and re-run the same command to resume.")
            json.dump({"report": report(scope), "context_loss": context_loss()},
                      open(RESULTS / "summary.json", "w"), indent=1)
            return 3
    rep, cl = report(scope), context_loss()
    json.dump({"report": rep, "context_loss": cl}, open(RESULTS / "summary.json", "w"), indent=1)
    done = sum(v["cells"] for v in rep.values())
    planned = sum(v["planned"] for v in rep.values())
    print(f"\n{'COMPLETE' if done == planned else 'INCOMPLETE'}: {done}/{planned} cells")
    for dom, v in rep.items():
        print(f"  {dom:<9} cells {v['cells']}/{v['planned']}  wall {v['wall_hours']} h  "
              f"cells that lost context {len(v['cell_context']['context_lost'])} "
              f"{v['cell_context']['context_lost'][:10]}  n_ctx {v['cell_context']['n_ctx']}")
    return 0 if done == planned else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
