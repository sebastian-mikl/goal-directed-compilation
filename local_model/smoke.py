#!/usr/bin/env python
"""O2.a smoke test: all five systems on five employee in-scope questions, seed 0, on the
local model through the unchanged harness. Writes OUT/smoke/."""
from __future__ import annotations

import o2common as C            # FIRST (imports localguard)

import json
import sys
import time
from pathlib import Path

QIDS = ["t1_01", "t2_01", "t3_01", "t4_01", "t5_01"]
SEEDS = [0]


def main(tag: str = "smoke") -> int:
    d = C.OUT / tag
    d.mkdir(exist_ok=True)
    results, prov, calls = (d / "results_o2_smoke_employee.jsonl",
                            d / "results_o2_smoke_employee.provenance.json", d / "calls.jsonl")
    print("guard self-test:", C.G.self_test())
    block = C.provenance_block("O2.a smoke test", Path(__file__).resolve())
    print("ollama:", json.dumps(block["ollama"], default=str)[:400])
    client = C.LocalOnlyClient(calls, session=tag)
    block["server_log_offset"] = C.server_log_offset()
    t0 = time.time()
    summary = C.run_domain("employee", QIDS, C.SYSTEMS, SEEDS, results, prov, client,
                           cell_log=d / "cell_context.jsonl")
    t1 = time.time()
    block.update(started_epoch=t0, finished_epoch=t1, summary=summary,
                 server_log_offset_end=C.server_log_offset(),
                 ollama_after=C.ollama_facts(),
                 guard_after={"requests_to_localhost_11434": C.G.REQUESTS_SEEN["localhost_11434"],
                              "dotenv_attempts_blocked": list(C.G.DOTENV_ATTEMPTS),
                              "violations_log_exists": C.G.VIOLATIONS_LOG.exists()})
    C.add_o2_provenance(prov, block)
    print(f"\nsummary {summary}  wall {t1 - t0:.1f}s  requests to localhost:11434 "
          f"{C.G.REQUESTS_SEEN['localhost_11434']}")
    return 0 if not summary["aborted"] else 3


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
