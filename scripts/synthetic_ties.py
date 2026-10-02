#!/usr/bin/env python
"""Data-grounded limit ties on the synthetic domains, for disclosure (D6).

In this repository the synthetic domain is the employee domain; the risk domain's gold
builder is withheld with its data, so BUILDERS holds the employee builder only and the
file it writes has no risk key. The development tree's copy of this script also scans
risk (no tie found there).

The synthetic domains were scored strict only. A ranked question whose limit cuts
through a tie (the k-th and (k+1)-th rows share the ranking value) has more than one
valid answer, and strict scoring accepts only the gold builder's tie-break. This
script finds every such question from the gold builders themselves, not from any
answer: it runs each builder's build() with its two top-k paths watched (topk(),
which already flags a boundary tie, and the grouped top-k's Series.head(k)), and for
each tie question re-runs build() once per way of filling the tied slots, so every
alternative passes through the builder's own projection and id stamping.

Writes reports/tie_alternatives_synthetic.json:
  {"scanned": {domain: [ranked qids]}, "ties": {domain: {qid: {k, by, boundary,
   tied_rows, slots, alt_values}}}}
alt_values excludes gold itself. scripts/build_paper_numbers.py reads it for the
per-system tie counts (fig_table Ties) and, since D9, score_tie_tolerant.score_tt_syn
reads it for the employee tolerant rows beside strict.

    python scripts/synthetic_ties.py
"""
from __future__ import annotations

import importlib.util
import inspect
import itertools
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "tie_alternatives_synthetic.json"
BUILDERS = {"employee": "build_gold_employee"}


def _builder(name: str):
    """A fresh copy of evidence/gold/<name>.py (its own module, so no other `build_gold`
    on sys.path, e.g. heldout's, can stand in for it)."""
    spec = importlib.util.spec_from_file_location(f"_synthetic_ties_{name}", ROOT / "evidence" / "gold" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _qid_of_caller(depth: int) -> str | None:
    line = (inspect.stack()[depth].code_context or [""])[0]
    m = re.findall(r'G\["(\w+)"\]', line)
    return m[0] if m else None


def scan(m, df) -> tuple[list, dict]:
    """(ranked qids, {qid: tie record}) for one builder module on its register."""
    ranked, ties = [], {}
    real_topk, real_head = m.topk, pd.Series.head

    def topk(d, by, k, qid, ascending=False):
        ranked.append(qid)
        s = d.sort_values(by, ascending=ascending, kind="mergesort")
        if len(s) > k and pd.notna(s.iloc[k - 1][by]) and s.iloc[k - 1][by] == s.iloc[k][by]:
            b = s.iloc[k - 1][by]
            ties[qid] = {"k": k, "by": str(by), "boundary": b.item() if hasattr(b, "item") else b,
                         "path": "topk", "ascending": ascending}
        return real_topk(d, by, k, qid, ascending)

    def head(self, n=5):                      # the grouped top-k (_grp_topk): sort_values().head(k)
        q = _qid_of_caller(3)
        if q is not None and len(self) > n:
            ranked.append(q)
            if pd.notna(self.iloc[n - 1]) and self.iloc[n - 1] == self.iloc[n]:
                b = self.iloc[n - 1]
                ties[q] = {"k": n, "by": str(self.name), "boundary": b.item() if hasattr(b, "item") else b,
                           "path": "series_head"}
        return real_head(self, n)

    m.topk, pd.Series.head = topk, head
    try:
        m.build(df)
    finally:
        m.topk, pd.Series.head = real_topk, real_head
    return sorted(set(ranked)), ties


def alternatives(m, df, qid: str, tie: dict) -> tuple[list, int, int]:
    """Every gold-shaped value of `qid` with the tied slots filled another way.
    -> (alt values excluding gold, number of tied rows, tied slots)."""
    if tie["path"] != "topk":
        raise NotImplementedError(f"{qid}: alternatives for a grouped top-k tie are not built")
    real_topk = m.topk
    captured = {}

    def capture(d, by, k, q, ascending=False):
        if q == qid:
            captured["s"] = d.sort_values(by, ascending=ascending, kind="mergesort")
        return real_topk(d, by, k, q, ascending)

    m.topk = capture
    try:
        gold = m.build(df)[qid]["value"]
    finally:
        m.topk = real_topk
    s, k, by, b = captured["s"], tie["k"], tie["by"], tie["boundary"]
    above, tied = s[s[by] != b].head(k), s[s[by] == b]
    above = above[above.index.isin(s.head(k).index)]
    slots = k - len(above)
    alts = []
    for combo in itertools.combinations(range(len(tied)), slots):
        pick = pd.concat([above, tied.iloc[list(combo)]])

        def fixed(d, by_, k_, q, ascending=False, _pick=pick):
            return _pick if q == qid else real_topk(d, by_, k_, q, ascending)

        m.topk = fixed
        try:
            v = m.build(df)[qid]["value"]
        finally:
            m.topk = real_topk
        if v != gold and v not in alts:
            alts.append(v)
    return alts, len(tied), slots


def main() -> int:
    out = {"scanned": {}, "ties": {}}
    for dom, name in BUILDERS.items():
        m = _builder(name)
        df = pd.read_pickle(m.REGISTER)
        ranked, ties = scan(m, df)
        m.FLAGS.clear()
        out["scanned"][dom] = ranked
        out["ties"][dom] = {}
        for qid, t in sorted(ties.items()):
            alts, n_tied, slots = alternatives(m, df, qid, t)
            out["ties"][dom][qid] = {**{k: v for k, v in t.items() if k != "ascending"},
                                     "tied_rows": n_tied, "slots": slots, "alt_values": alts}
        m.FLAGS.clear()
        print(f"{dom}: {len(ranked)} ranked questions scanned; limit ties: "
              + (", ".join(f"{q} (top-{t['k']} on {t['by']!r} = {t['boundary']}: {t['tied_rows']} rows tied for "
                           f"{t['slots']} slots, {len(t['alt_values'])} alternatives)"
                           for q, t in out["ties"][dom].items()) or "none"))
    OUT.write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
