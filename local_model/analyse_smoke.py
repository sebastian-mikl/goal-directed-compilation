#!/usr/bin/env python
"""Score an O2 smoke-test directory against the fixed O2.a pass criteria (checklist
2026-10, O2.a), and report per-cell verdicts, throughput and prompt truncation.

Criteria (fixed in the checklist, not changed here):
  C1 every system completes every cell without a harness error;
  C2 GDC emits a parseable Goal in at least 4 of 5 cells;
  C3 react and code_agent each make at least one valid tool call.

Operational definitions used:
  harness error  = the run aborted on a transport/API error (the cell is then missing),
                   a cell is missing, or a row is a cell the harness did not complete in the
                   sense of scripts/parity_overlay.incomplete_row (guard refusal, credit /
                   billing error, or a crash the harness logged for an exception out of the
                   adapter: status 'crashed' with artifact.exception). Any other 'crashed'
                   row is listed separately.
  parseable Goal = llm1 returned a Goal (artifact.goal present); a decline envelope or
                   a ParseError is not one. Goal validity (validate_goal) reported beside it.
  valid tool call
     react       = a call to one of the five declared primitives that the executor applied
                   (observation "frame 'fN': ..."), or final_answer naming a frame that exists.
     code_agent  = a run_python / final_answer call whose code ran without raising.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT / "tree"))
from scripts.parity_overlay import incomplete_row          # noqa: E402

import o2common as C                                       # noqa: E402

QIDS = ["t1_01", "t2_01", "t3_01", "t4_01", "t5_01"]
DECL = {"filter", "select", "aggregate", "compute", "order"}


def load(p: Path):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def react_valid(r):
    tr = (r.get("artifact") or {}).get("trace") or []
    produced = {"register"}
    ok_ops, ok_final = 0, 0
    for st in tr:
        if st.get("tool") in DECL and str(st.get("summary", "")).startswith("frame '"):
            ok_ops += 1
            produced.add(str(st["summary"]).split("'")[1])
        elif st.get("tool") == "final_answer" and (st.get("args") or {}).get("frame") in produced:
            ok_final += 1
    return ok_ops, ok_final, len(tr)


def code_valid(r):
    tr = (r.get("artifact") or {}).get("trace") or []
    ok = sum(1 for st in tr if st.get("tool") in ("run_python", "final_answer")
             and not str(st.get("summary", "")).startswith("error:"))
    return ok, len(tr)


def gin_offset_before(epoch: float) -> int:
    """Byte offset just after the last [GIN] request line logged before `epoch`."""
    import datetime, re
    rx = re.compile(r"^\[GIN\] (\d{4}/\d\d/\d\d - \d\d:\d\d:\d\d) ")
    off = pos = 0
    with open(C.OLLAMA_LOG, "rb") as fh:
        for raw in fh:
            pos += len(raw)
            m = rx.match(raw.decode(errors="replace"))
            if m:
                ts = datetime.datetime.strptime(m.group(1), "%Y/%m/%d - %H:%M:%S").timestamp()
                if ts < epoch - 1:
                    off = pos
    return off


def main(tag="smoke"):
    d = OUT / tag
    rows = load(d / "results_o2_smoke_employee.jsonl")
    calls = load(d / "calls.jsonl")
    prov = json.loads((d / "results_o2_smoke_employee.provenance.json").read_text())
    o2 = (prov.get("o2_local_model") or [{}])[-1]
    t0, t1 = o2.get("started_epoch", 0), o2.get("finished_epoch")
    tr = C.truncations(t0 - 1, (t1 or 1e12) + 1)
    off = o2.get("server_log_offset")
    if off is None:
        off = gin_offset_before(t0)
    end = o2.get("server_log_offset_end")
    if end is None and t1:
        end = gin_offset_before(t1 + 2)
    tasks = C.server_tasks(off, end)
    aligned = len(tasks) == len(calls)
    for c, t in zip(calls, tasks if aligned else []):
        c.update(prompt_tokens=t["prompt_tokens"], n_ctx=t["n_ctx"], temp=t.get("temp"),
                 truncated_from=t["input_truncated_from"], context_shifts=t["context_shifts"],
                 released_truncated=t["released_truncated"])
    for c in calls:
        c.setdefault("domain", "employee")
    C.mark_context_loss(calls)
    by = {(r["system"], r["qid"]): r for r in rows}

    # C1
    missing = [(s, q) for s in C.SYSTEMS for q in QIDS if (s, q) not in by]
    harness_err = [(r["system"], r["qid"], incomplete_row(r)) for r in rows if incomplete_row(r)]
    other_crash = [(r["system"], r["qid"], r.get("error")) for r in rows
                   if r.get("status") == "crashed" and not incomplete_row(r)]
    aborted = (o2.get("summary") or {}).get("aborted")
    c1 = not missing and not harness_err and not aborted
    # C2
    net = [by[("network", q)] for q in QIDS if ("network", q) in by]
    parse = [q for q in QIDS if ("network", q) in by and "goal" in (by[("network", q)].get("artifact") or {})]
    valid = [q for q in parse if (by[("network", q)]["artifact"]).get("goal_valid")]
    c2 = len(parse) >= 4
    # C3
    rv = {q: react_valid(by[("react", q)]) for q in QIDS if ("react", q) in by}
    cv = {q: code_valid(by[("code_agent", q)]) for q in QIDS if ("code_agent", q) in by}
    react_total = sum(a + b for a, b, _ in rv.values())
    code_total = sum(a for a, _ in cv.values())
    c3 = react_total >= 1 and code_total >= 1

    print("=" * 100)
    print(f"O2.a smoke test ({tag}): {len(rows)}/25 cells logged; aborted={aborted}")
    print(f"  C1 no harness error ........ {'PASS' if c1 else 'FAIL'}   missing={missing} harness_errors={harness_err}"
          f" other_crashed={other_crash}")
    print(f"  C2 GDC parseable Goal >=4/5  {'PASS' if c2 else 'FAIL'}   parseable {len(parse)}/5 {parse}; "
          f"contract-valid {len(valid)}/5")
    print(f"  C3 react & code_agent tool . {'PASS' if c3 else 'FAIL'}   react valid calls {react_total} "
          f"(per cell ops/final/total {rv}); code_agent valid calls {code_total} (per cell ok/total {cv})")
    print(f"  OVERALL ................... {'PASS' if (c1 and c2 and c3) else 'FAIL'}")

    print("\nper cell (seed 0)")
    print(f"  {'system':<12}{'qid':<7}{'status':<11}{'verdict':<24}{'calls':>5}{'in_tok':>8}{'out_tok':>8}"
          f"{'wall_s':>8}  trunc  note")
    calls_by = defaultdict(list)
    for c in calls:
        calls_by[(c["system"], c["qid"])].append(c)
    for s in C.SYSTEMS:
        for q in QIDS:
            r = by.get((s, q))
            if not r:
                print(f"  {s:<12}{q:<7}MISSING")
                continue
            cs = calls_by[(s, q)]
            ntr = sum(1 for c in cs if c.get("context_lost"))
            note = ""
            a = r.get("artifact") or {}
            if s == "network":
                note = ("goal " + ("valid" if a.get("goal_valid") else "INVALID")) if "goal" in a else (r.get("error") or "")[:60]
            elif s == "react":
                o, f, n = rv[q]; note = f"tool calls ok {o}+final {f} of {n}"
            elif s == "code_agent":
                o, n = cv[q]; note = f"code calls ok {o} of {n}"
            elif r.get("error"):
                note = r["error"][:60]
            print(f"  {s:<12}{q:<7}{r['status']:<11}{r['comparator_verdict']:<24}{r['llm_calls']:>5}"
                  f"{r['input_tokens']:>8}{r['output_tokens']:>8}{r['wall_time_s']:>8.1f}  {ntr:>2}/{len(cs):<3} {note}")

    print("\nverdict counts (information only)")
    vc = defaultdict(Counter)
    for r in rows:
        v = r["comparator_verdict"]
        v = {"refused_should_answer": "refused"}.get(v, v)
        vc[r["system"]][v] += 1
    for s in C.SYSTEMS:
        print(f"  {s:<12} {dict(vc[s])}")

    print("\nthroughput (wall time per cell, seconds)")
    import statistics as st
    for s in C.SYSTEMS:
        w = [by[(s, q)]["wall_time_s"] for q in QIDS if (s, q) in by]
        cl = [by[(s, q)]["llm_calls"] for q in QIDS if (s, q) in by]
        if w:
            print(f"  {s:<12} mean {st.mean(w):6.1f}  median {st.median(w):6.1f}  max {max(w):6.1f}  "
                  f"total {sum(w):7.1f}  calls/cell {st.mean(cl):.1f}")
    lat = [c["latency_s"] for c in calls if c.get("latency_s")]
    outt = sum(c.get("out") or 0 for c in calls)
    print(f"  all calls: {len(calls)}, mean latency {st.mean(lat):.2f}s, output tokens {outt}, "
          f"decode-inclusive tok/s {outt / sum(lat):.1f}")
    if t1:
        print(f"  run wall clock {t1 - t0:.1f}s for {len(rows)} cells")
    (d / "calls_annotated.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))

    print("\ncontext window (Ollama server log, per request; aligned to the call log: "
          f"{'yes' if aligned else 'NO'} — {len(tasks)} server tasks, {len(calls)} calls)")
    print(f"  sampling temperature the server applied, per request: "
          f"{dict(Counter(c.get('temp') for c in calls))}")
    print(f"  server n_ctx {sorted({c.get('n_ctx') for c in calls if c.get('n_ctx')})}; "
          f"'truncating input prompt' warnings in the run window: {len(tr)}")
    bad = lambda c: bool(c.get("context_lost"))  # noqa: E731
    ev = Counter(k for c in calls for k in (c.get("loss_events") or []))
    print(f"  loss events: {dict(ev)} (dropped_messages = Ollama silently dropped the oldest chat "
          f"messages; context_shift = llama-server discarded half the context mid-reply)")
    tc = Counter(c["system"] for c in calls if bad(c))
    tot = Counter(c["system"] for c in calls)
    mx = defaultdict(int)
    for c in calls:
        mx[c["system"]] = max(mx[c["system"]], (c.get("truncated_from") or c.get("prompt_tokens") or 0))
    for s in C.SYSTEMS:
        print(f"  {s:<12} calls without their full context {tc[s]}/{tot[s]}; "
              f"longest prompt {mx[s]} tokens")
    cells_tr = sorted({(c['system'], c['qid']) for c in calls if bad(c)})
    print(f"  cells with >=1 such call: {len(cells_tr)} {cells_tr}")
    cl = load(d / "cell_context.jsonl")
    if cl:
        print(f"  run-time per-cell flag (cell_context.jsonl): {len(cl)} cells, context_lost in "
              f"{sum(1 for x in cl if x['context_lost'])}, all aligned {all(x['aligned'] for x in cl)}, "
              f"max prompt+reply {max(x['max_prompt_plus_out'] for x in cl)} tokens")
    timeouts = [(r["system"], r["qid"]) for r in rows if "interrupted" in str(r.get("error") or "").lower()]
    capped = [(r["system"], r["qid"], r.get("error")) for r in rows if r.get("status") == "timeout"]
    print(f"\n60 s per-query timeout hits (text_to_sql SQL 'interrupted'): {len(timeouts)} {timeouts}")
    print(f"iteration/retry-bound hits (status=timeout): {len(capped)} {capped}")
    print(f"guard: {o2.get('guard_after')}; violations log exists: {C.G.VIOLATIONS_LOG.exists()}")
    json.dump({"c1": c1, "c2": c2, "c3": c3, "context_lost_calls": sum(tc.values()), "calls": len(calls),
               "aligned": aligned, "n_ctx": sorted({c.get('n_ctx') for c in calls if c.get('n_ctx')}),
               "temps": sorted({c.get('temp') for c in calls if c.get('temp') is not None}),
               "model": o2.get("model"), "max_prompt_by_system": dict(mx),
               "cells_truncated": cells_tr}, open(d / "criteria.json", "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])
