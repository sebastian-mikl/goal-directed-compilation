#!/usr/bin/env python
"""Per-database like-for-like comparisons on Spider's out_of_scope_register tier, with Holm across all of them.

Like for like (§5.3, Appendix A.4) pairs GDC with the alternatives that carry the same decline instruction:
  GDC with its checks alone (instruction off)  vs  the planner and react, which carry none
  GDC told it may decline (instruction on)     vs  the code agent and text-to-SQL, which are told so
On each of the three development databases that is four comparisons per metric (answered anyway, i.e.
silent_wrong, and declined): twelve per metric. Each comparison is question_clustered_stats.analyse():
paired difference, 95% percentile interval from a question-level cluster bootstrap, exact sign-flip
permutation p. The instruction-off comparisons are the rows of reports/stats/comparisons.csv's
decline_spider_oosr_<db> sets (whose Holm runs over that set's four alternatives); the instruction-on ones
use GDC's cells of the s40on files (question_clustered_stats' S40ON set) on the resamples of the set named
like_for_like_<db>_s40on. Holm is then applied across the twelve comparisons of each metric.

Writes reports/stats/like_for_like/per_database.csv and summary.txt.

    python scripts/like_for_like_stats.py        # run_all.sh step 6, after question_clustered_stats.py
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

OUT = ROOT / "reports" / "stats" / "like_for_like"
COMPARISONS = ROOT / "reports" / "stats" / "comparisons.csv"
DATABASES = ("wta_1", "world_1", "bike_1")
OFF_ALTS = ("planner", "react")
ON_ALTS = ("code_agent", "text_to_sql")
METRICS = ("silent_wrong", "declined")


def compute(G, cells, comparisons: Path, out: Path, echo=print) -> list[dict]:
    import scripts.question_clustered_stats as Q
    from scripts.planner_info_stats import _pair
    with open(comparisons, newline="", encoding="utf-8") as fh:
        comp = {(r["set"], r["metric"], r["alt"]): r for r in csv.DictReader(fh)}
    rows = []
    for d in DATABASES:
        for alt in OFF_ALTS:
            for m in METRICS:
                r = comp[(f"decline_spider_oosr_{d}", m, alt)]
                rows.append({"database": d, "gdc": "instruction off", "alt": alt, "metric": m,
                             "gdc_pct": float(r["gdc_pct"]), "alt_pct": float(r["alt_pct"]),
                             "diff_pp": float(r["diff_pp"]), "ci_lo_pp": float(r["ci_lo_pp"]),
                             "ci_hi_pp": float(r["ci_hi_pp"]), "n_questions": int(r["n_questions"]),
                             "n_discordant_q": int(r["n_discordant_q"]), "p_perm": float(r["p_perm"]),
                             "p_holm_within_set": float(r["p_perm_holm"]),
                             "within_set": f"decline_spider_oosr_{d} (four alternatives)"})
        on = [c for c in cells[Q.S40ON] if c["dom"] == d]
        for alt in ON_ALTS:
            alts = [c for c in cells[f"decline_spider_oosr_{d}"] if c["system"] == alt]
            with _pair(Q, Q.GDC, alt):
                _, cc, _ = Q.analyse(f"like_for_like_{d}_s40on", on + alts, "decline")
            for c in cc:
                rows.append({"database": d, "gdc": "told it may decline", "alt": alt, "metric": c["metric"],
                             "gdc_pct": c["gdc_pct"], "alt_pct": c["alt_pct"], "diff_pp": c["diff_pp"],
                             "ci_lo_pp": c["ci_lo_pp"], "ci_hi_pp": c["ci_hi_pp"], "n_questions": c["n_questions"],
                             "n_discordant_q": c["n_discordant_q"], "p_perm": c["p_perm"],
                             "p_holm_within_set": c["p_perm_holm"], "within_set": "this comparison alone"})
    for m in METRICS:
        sel = [r for r in rows if r["metric"] == m]
        if len(sel) != 3 * (len(OFF_ALTS) + len(ON_ALTS)):
            raise SystemExit(f"{m}: {len(sel)} comparisons, expected twelve")
        for r, h in zip(sel, Q.holm([r["p_perm"] for r in sel])):
            r["p_holm_across_twelve"] = h
            r["resolved_across_twelve"] = bool((r["ci_lo_pp"] > 1e-12 or r["ci_hi_pp"] < -1e-12) and h < 0.05)
    out.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with open(out / "per_database.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()})
    L = ["Per-database like-for-like comparisons, Spider out_of_scope_register (GDC minus the alternative), "
         "Holm across the twelve per metric:"]
    for r in sorted(rows, key=lambda r: (r["metric"], r["p_perm"])):
        L.append(f"  {r['metric']:<12} {r['database']:<8} GDC {r['gdc']:<20} vs {r['alt']:<11} "
                 f"{r['diff_pp']:+6.1f} [{r['ci_lo_pp']:+6.1f}, {r['ci_hi_pp']:+6.1f}]  p {r['p_perm']:.4f}  "
                 f"Holm within set {r['p_holm_within_set']:.4f}  across twelve {r['p_holm_across_twelve']:.4f}"
                 + ("  resolved" if r["resolved_across_twelve"] else ""))
    (out / "summary.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    n = sum(r["resolved_across_twelve"] for r in rows)
    echo(f"like for like: {n} of {len(rows)} per-database comparisons resolved after Holm across the twelve; "
         f"outputs {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}/")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--comparisons", default=str(COMPARISONS))
    a = ap.parse_args(argv)
    os.environ["PARITY_TEMP0"] = "1"
    import scripts.question_clustered_stats as Q
    G = Q.run_builder()
    cells, spec = Q.derive(G)
    Q.verify(G, cells, spec)
    compute(G, cells, Path(a.comparisons), Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
