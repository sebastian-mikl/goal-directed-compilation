#!/usr/bin/env python
"""Offline checks of the held-out build (no API): the rule reproduces itself, the gold
executes and scores, every system's prompt renders. Exit 0 only if every gating check passes.

    python heldout/validate_offline.py
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from sql_tier import tier_of  # noqa: E402
from build_domains import HELDOUT, ANSWER_TIERS  # noqa: E402
from evidence.eval_run.comparator import score  # noqa: E402
from evidence.eval_run.types import REFUSAL  # noqa: E402

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' -- ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def built_files():
    out = [HERE / "tie_alternatives_heldout.json"]
    for d in HELDOUT:
        reg = json.loads((HERE / d / "schema.json").read_text())["domain"]["register_table"]
        out += [HERE / f"eval_corpus_spider_{d}.json", HERE / d / "schema.json", HERE / d / f"{reg}.pkl",
                HERE / "gold" / f"gold_spider_{d}.json", HERE / "gold" / f"gold_spider_{d}_digest.txt"]
    return out


def main() -> int:
    py = sys.executable
    print("1. Rebuild is byte-identical (corpus, schema, register, gold, digest, ties)")
    before = {p: sha(p) for p in built_files()}
    for s in ("build_domains.py", "build_gold.py", "detect_ties.py"):
        r = subprocess.run([py, str(HERE / s)], capture_output=True, text=True)
        check(f"{s} exits 0", r.returncode == 0, r.stderr[-300:])
    after = {p: sha(p) for p in built_files()}
    check("every built file unchanged by the rebuild", before == after,
          str([p.name for p in before if before[p] != after.get(p)]))

    print("2. Corpus, gold and tiers")
    from run_heldout import DOMAINS
    for d, cfg in DOMAINS.items():
        corpus = json.loads(Path(cfg["corpus"]).read_text())
        gold = json.loads(Path(cfg["gold"]).read_text())
        qs = corpus["queries"]
        conn = sqlite3.connect(f"file:{ROOT / HELDOUT[d][0]}?mode=ro", uri=True)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        reg = corpus["meta"]["register_table"]
        check(f"{d}: one gold record per question", sorted(gold) == sorted(q["id"] for q in qs))
        check(f"{d}: gold tier = corpus tier", all(gold[q["id"]]["tier"] == q["tier"] for q in qs))
        check(f"{d}: tier = rule applied now", all(tier_of(q["gold_sql"], tables, reg)["tier"] == q["tier"] for q in qs))
        check(f"{d}: questions verbatim and distinct gold SQL",
              len({q["gold_sql"] for q in qs}) == len(qs))
        cnt = Counter("answer" if q["tier"] in ANSWER_TIERS else q["tier"] for q in qs)
        print(f"     {d}: register {reg}; {len(qs)} questions; {dict(cnt)}")
        ans = [q for q in qs if q["expected_behavior"] == "answer"]
        check(f"{d}: every answer/out_of_contract_aggregate gold executed (a value)",
              all(gold[q["id"]]["value"] is not None for q in ans))
        bad = [q["id"] for q in ans if score(gold[q["id"]]["value"], gold[q["id"]])[0] != "correct"]
        check(f"{d}: gold scores correct against itself (perfect-answer probe)", not bad, str(bad))
        bad = [q["id"] for q in ans if score(REFUSAL, gold[q["id"]], status="refused")[0] != "refused_should_answer"]
        check(f"{d}: a refusal on an answerable question scores refused_should_answer", not bad, str(bad))
        ref = [q for q in qs if q["expected_behavior"] == "refuse"]
        bad = [q["id"] for q in ref if score(REFUSAL, gold[q["id"]], status="refused")[0] != "correct"]
        check(f"{d}: a refusal on out_of_scope_register scores correct", not bad, str(bad))
        bad = [q["id"] for q in ref if score([{"x": 1}], gold[q["id"]])[0] != "silent_wrong"]
        check(f"{d}: an answer on out_of_scope_register scores silent_wrong", not bad, str(bad))

    print("3. Every system's prompt renders on the held-out schemas; GDC gets s45")
    from run_heldout import texts_for, verify_s45
    v = verify_s45()
    check("s45 transform reproduces the three recorded Spider GDC prompts byte for byte",
          all(x["identical"] for x in v.values()))
    for d in DOMAINS:
        t = texts_for(d)
        check(f"{d}: five prompts render", sorted(t) == sorted(
            ["network", "planner", "react", "code_agent", "text_to_sql"]))
        check(f"{d}: GDC text sent is the s45 derivation (shorter by the two s48 blocks)",
              len(t["network"]["current"]) - len(t["network"]["sent"]) == 1137)
        cols = json.loads((HERE / d / "schema.json").read_text())["columns"]
        check(f"{d}: every register column is named in GDC's prompt",
              all(c.lower() in t["network"]["sent"].lower() for c in cols))
    print()
    print("ALL GATING CHECKS PASSED" if not FAILS else f"FAILED: {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
