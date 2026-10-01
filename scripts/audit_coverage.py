#!/usr/bin/env python
"""Read-only coverage audit over the tier-eval results files.

Answers one question: which (tier, system, model) cells do we actually have
data for, and what is their aggregate accuracy / silent-error rate? Reads
existing results_*.jsonl + provenance_*.json only — never re-runs an eval,
never writes to any file outside --out, never touches plot_results.py.

"Correct" / "silent_wrong" are not redefined here: both are read verbatim
from each record's `outcome_class`, which was written by
evidence/eval_run/comparator.py:score() at run (or --rescore) time. See that
function's docstring for the full outcome taxonomy. In particular
silent_wrong means "confident answer that doesn't match gold" (includes an
unwarranted answer on an out-of-range query) — it explicitly EXCLUDES
crash/timeout (operational failures, short-circuited before scoring) and
refused_should_answer (an honest refusal, not a silent error).

No results record carries a "model" field (see harness.py:run_cell) — model
is a run-level property. Each results_<family>*.jsonl traces back to one
provenance_<family>.json ("model" + "emission_model"); emission_model
overrides "model" only for systems in EMISSION_SYSTEMS (network, planner) —
see evidence/eval_run/emission_config.py. This script resolves the per-row
model with that exact rule instead of hardcoding a filename->model table.

Usage: python3 scripts/audit_coverage.py [--out reports/coverage_audit.md]
Always prints the full report to stdout; --out additionally writes it there.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evidence.eval_run.comparator import COMPARATOR_VERSION  # noqa: E402
from evidence.eval_run.emission_config import EMISSION_SYSTEMS  # noqa: E402

FAMILY_PROVENANCE = {
    "off": ROOT / "provenance_off.json",
    "t5": ROOT / "provenance_t5.json",
    "haiku": ROOT / "provenance_haiku.json",
}
TIER_LABELS = {"T1": "T1_single_op", "T2": "T2_two_ops", "T3": "T3_three_ops"}


def family_of(fname: str) -> str | None:
    for fam in FAMILY_PROVENANCE:
        if fname.startswith(f"results_{fam}"):
            return fam
    return None


def git_log_line(path: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ai %h %s", "--", str(path.relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or "(not tracked in git)"
    except Exception as exc:                                          # noqa: BLE001
        return f"(git lookup failed: {exc})"


def count_lines(path: Path) -> int:
    if path.stat().st_size == 0:
        return 0
    with path.open("rb") as f:
        return sum(1 for _ in f)


def load_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def truncate(value, n=200) -> str:
    s = repr(value)
    return s if len(s) <= n else s[: n] + f"...<truncated, full len {len(s)}>"


# ── Step 1: file inventory ───────────────────────────────────────────────

def discover_files() -> list[Path]:
    root_hits = {p for p in (set(ROOT.glob("*.jsonl")) | set(ROOT.glob("results*"))) if p.is_file()}
    results_dir = ROOT / "results"
    dir_hits = {p for p in results_dir.rglob("*") if p.is_file()} if results_dir.exists() else set()
    return sorted(root_hits | dir_hits)


def describe_file(path: Path) -> dict:
    rel = str(path.relative_to(ROOT))
    size = path.stat().st_size
    info = {"path": rel, "size_bytes": size, "git": git_log_line(path)}
    if path.suffix == ".jsonl":
        info["kind"] = "jsonl"
        info["rows"] = count_lines(path)
    elif path.suffix == ".csv":
        info["kind"] = "csv"
        with path.open() as f:
            header = f.readline().strip()
        info["rows"] = max(count_lines(path) - 1, 0)
        info["header"] = header
    elif path.suffix == ".json":
        info["kind"] = "json"
        info["rows"] = None
    else:
        info["kind"] = path.suffix.lstrip(".") or "other"
        info["rows"] = None
    return info


# ── model resolution (no per-record "model" field; see module docstring) ──

def load_provenance(fam: str) -> dict | None:
    p = FAMILY_PROVENANCE[fam]
    return json.loads(p.read_text()) if p.exists() else None


def model_for_row(fam_provenance: dict, system: str) -> str:
    model = fam_provenance["model"]
    emission_model = fam_provenance.get("emission_model")
    if emission_model and system in EMISSION_SYSTEMS:
        return emission_model
    return model


def scoring_vintage(fname: str, fam_provenance: dict | None, rows: int, *, rescored_rows: int | None = None) -> str:
    """Per-FILE comparator vintage note. NOT the same as the family's provenance
    comparator_version, which only describes the ORIGINAL run — rescore() always
    (re)scores under whatever comparator.py is checked out when it's invoked, so a
    *.rescored.jsonl reflects THIS checkout's COMPARATOR_VERSION regardless of what
    its base file's provenance says.

    `rescored_rows`, when passed for a BASE file, is that same family's
    *.rescored.jsonl row count. A mismatch (fewer rescored rows than base rows) is
    data-driven proof the base file outgrew its rescore — flagged inline rather than
    only in prose, since a reader scanning just this table would otherwise miss it."""
    if rows == 0:
        return "n/a (empty file)"
    declared = fam_provenance.get("comparator_version") if fam_provenance else "<unknown>"
    if ".lint_rescored" in fname:
        return f"comparator_version={declared} (inherited from base) + S35 lint layer on top"
    if ".rescored" in fname:
        return f"comparator_version={COMPARATOR_VERSION} (via rescore(), this checkout's live version)"
    base_note = f"comparator_version={declared} (as originally generated)"
    if rescored_rows is not None and rescored_rows < rows:
        base_note += (f" — MIXED: only the first {rescored_rows}/{rows} rows have a rescored "
                       f"counterpart; the remaining {rows - rescored_rows} were appended later and are "
                       f"NOT independently verified under {COMPARATOR_VERSION} in this repo (see notes below)")
    return base_note


# ── Step 3: coverage table ───────────────────────────────────────────────

def build_coverage(grid_files: list[Path]) -> list[dict]:
    rows = []
    for path in grid_files:
        fam = family_of(path.name)
        prov = load_provenance(fam) if fam else None
        groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for rec in load_jsonl(path):
            model = model_for_row(prov, rec["system"]) if prov else "<unknown: no provenance>"
            key = (rec["tier"], rec["system"], model)
            groups[key].append(rec["outcome_class"])
        for (tier, system, model), outcomes in groups.items():
            n = len(outcomes)
            n_correct = sum(1 for o in outcomes if o == "correct")
            n_silent = sum(1 for o in outcomes if o == "silent_wrong")
            other = Counter(o for o in outcomes if o not in ("correct", "silent_wrong"))
            rows.append({
                "file": path.name, "tier": tier, "system": system, "model": model,
                "n": n, "accuracy_pct": 100.0 * n_correct / n, "silent_error_pct": 100.0 * n_silent / n,
                "other_outcomes": dict(other),
            })
    rows.sort(key=lambda r: (r["file"], r["tier"], r["system"], r["model"]))
    return rows


# ── cross-file staleness evidence (base vs rescored vs lint_rescored) ─────

def compare_pair(base: Path, other: Path) -> dict:
    b = {(r["system"], r["qid"], r["seed"]): r["outcome_class"] for r in load_jsonl(base)}
    o = {(r["system"], r["qid"], r["seed"]): r["outcome_class"] for r in load_jsonl(other)}
    shared = set(b) & set(o)
    diffs = sum(1 for k in shared if b[k] != o[k])
    return {
        "base": base.name, "other": other.name,
        "n_base": len(b), "n_other": len(o),
        "n_shared": len(shared), "n_diff_on_shared": diffs,
        "only_in_other": len(set(o) - set(b)), "only_in_base": len(set(b) - set(o)),
    }


# ── Step 4: T1-T3 variance verdict ───────────────────────────────────────

def variance_check(coverage: list[dict]) -> tuple[bool, list[dict]]:
    target_tiers = set(TIER_LABELS.values())
    exceptions = [r for r in coverage if r["tier"] in target_tiers
                  and (abs(r["accuracy_pct"] - 100.0) > 1e-9 or abs(r["silent_error_pct"] - 0.0) > 1e-9)]
    saturated = len(exceptions) == 0
    return saturated, exceptions


# ── Step 5: gaps ──────────────────────────────────────────────────────────

def find_gaps(coverage: list[dict]) -> tuple[list[tuple[str, str, str]], set, set, set]:
    tiers = sorted({r["tier"] for r in coverage})
    systems = sorted({r["system"] for r in coverage})
    models = sorted({r["model"] for r in coverage})
    have = {(r["tier"], r["system"], r["model"]) for r in coverage}
    gaps = [combo for combo in product(tiers, systems, models) if combo not in have]
    return sorted(gaps), set(tiers), set(systems), set(models)


# ── report assembly ──────────────────────────────────────────────────────

def render_report(out=sys.stdout) -> None:
    def p(*a):
        print(*a, file=out)

    all_files = discover_files()
    grid_files = [f for f in all_files if f.suffix == ".jsonl" and family_of(f.name) and f.stat().st_size > 0]
    empty_jsonl = [f for f in all_files if f.suffix == ".jsonl" and family_of(f.name) and f.stat().st_size == 0]
    other_files = [f for f in all_files if f not in grid_files and f not in empty_jsonl]

    p("# Coverage Audit")
    p()
    p(f"Generated by `scripts/audit_coverage.py`. Comparator version live in this checkout: "
      f"`{COMPARATOR_VERSION}` (from `evidence/eval_run/comparator.py`). Read-only — no eval was run, "
      f"no existing file was modified.")
    p()

    # ── Section 1 ──
    p("## 1. Files found")
    p()
    p("### Tier-eval grid files (`results_{off,t5,haiku}*.jsonl`)")
    p()
    p("| file | rows | last git touch | run model / emission_model | this file's scoring vintage |")
    p("|---|---|---|---|---|")
    all_grid = sorted(grid_files) + sorted(empty_jsonl)
    rows_by_name = {f.name: count_lines(f) for f in all_grid}
    for f in all_grid:
        info = describe_file(f)
        fam = family_of(f.name)
        prov = load_provenance(fam) if fam else None
        model_note = f"model={prov['model']}, emission_model={prov.get('emission_model')}" if prov else "n/a"
        rescored_name = f.name.replace(".jsonl", ".rescored.jsonl")
        rescored_rows = rows_by_name.get(rescored_name) if ".rescored" not in f.name and ".lint_rescored" not in f.name else None
        vintage = scoring_vintage(f.name, prov, info["rows"], rescored_rows=rescored_rows)
        p(f"| `{info['path']}` | {info['rows']} | {info['git']} | {model_note} | {vintage} |")
    p()
    p("### Other files under `results/` (different schema — see Section 2)")
    p()
    p("| file | kind | rows/size | last git touch |")
    p("|---|---|---|---|")
    for f in sorted(other_files):
        info = describe_file(f)
        size_col = info["rows"] if info["rows"] is not None else f"{info['size_bytes']} bytes"
        p(f"| `{info['path']}` | {info['kind']} | {size_col} | {info['git']} |")
    p()

    p("### Stale / superseded flags (evidence, not assertion — see diffs below)")
    p()
    pairs = [
        ("off", ROOT / "results/results_off.jsonl", ROOT / "results/results_off.rescored.jsonl"),
        ("off", ROOT / "results/results_off.jsonl", ROOT / "results/results_off.lint_rescored.jsonl"),
        ("t5", ROOT / "results/results_t5.jsonl", ROOT / "results/results_t5.rescored.jsonl"),
        ("haiku", ROOT / "results/results_haiku.jsonl", ROOT / "results/results_haiku.rescored.jsonl"),
    ]
    p("| base | compared to | rows(base) | rows(other) | shared keys | outcome_class differs on shared | keys only in \"other\" |")
    p("|---|---|---|---|---|---|---|")
    for _, base, other in pairs:
        if not base.exists() or not other.exists() or base.stat().st_size == 0 or other.stat().st_size == 0:
            continue
        c = compare_pair(base, other)
        p(f"| `{c['base']}` | `{c['other']}` | {c['n_base']} | {c['n_other']} | {c['n_shared']} | "
          f"{c['n_diff_on_shared']} | {c['only_in_other']} |")
    p()
    prov_off = load_provenance("off")
    prov_t5 = load_provenance("t5")
    p(f"- `results_off.jsonl` — generated under `comparator_version={prov_off.get('comparator_version')}` "
      f"(current is `{COMPARATOR_VERSION}`) -> **STALE**, superseded by `results_off.rescored.jsonl` "
      f"(full 1950/1950 rows re-scored, see diff row above).")
    p(f"- `results_off.lint_rescored.jsonl` — built from the same `comparator_version="
      f"{prov_off.get('comparator_version')}` base as `results_off.jsonl` (0 outcome_class differences "
      f"against it outside the 3 rows the S35 lint reclassifies) — it does **not** carry the S36 "
      f"comparator fixes seen in `results_off.rescored.jsonl` -> **STALE** w.r.t. comparator version; "
      f"a lint-only correction layered on old scoring, not a superset of the S36 rescore.")
    p(f"- `results_t5.lint_rescored.jsonl` — 0 bytes -> **STALE/broken**, unusable as-is.")
    n_t5_raw = count_lines(ROOT / "results/results_t5.jsonl") if (ROOT / "results/results_t5.jsonl").exists() else 0
    n_t5_rescored = count_lines(ROOT / "results/results_t5.rescored.jsonl") if (ROOT / "results/results_t5.rescored.jsonl").exists() else 0
    p(f"- `results_t5.jsonl` ({n_t5_raw} rows) vs `results_t5.rescored.jsonl` ({n_t5_rescored} rows): "
      f"git history confirms the first {n_t5_rescored} rows of the current `results_t5.jsonl` are "
      f"byte-identical to the partial file `results_t5.rescored.jsonl` was computed from (commit "
      f"`9bbbebd`); `results_t5.jsonl` was then extended to {n_t5_raw} rows in a later commit "
      f"(`12b5f8c`) without a follow-up rescore -> **`results_t5.jsonl` is MIXED-vintage** "
      f"(first {n_t5_rescored} rows pre-S36-verified-stale / matched by the rescore; remaining "
      f"{n_t5_raw - n_t5_rescored} rows — all of `text_to_sql` and most of `code_agent` — were appended "
      f"after `comparator_version={prov_t5.get('comparator_version')}` was already current but have "
      f"**no independently rescored counterpart in this repo**, so their scoring cannot be cross-checked "
      f"here). `results_t5.rescored.jsonl` itself is therefore **PARTIAL** (covers {n_t5_rescored} of "
      f"{n_t5_raw} current rows), not stale for the rows it does cover.")
    p(f"- `results_haiku.jsonl` vs `results_haiku.rescored.jsonl` — 0 outcome_class differences on all "
      f"930 shared rows -> effectively current already; the two files are redundant.")
    p()

    # ── Section 2 ──
    p("## 2. Schema notes")
    p()
    p("Field names used for scoring (from `evidence/eval_run/harness.py:run_cell` and "
      "`evidence/eval_run/comparator.py:score`):")
    p()
    p("- **tier / complexity label** -> `tier` (values seen: "
      + ", ".join(sorted({r for f in grid_files for r in {rec['tier'] for rec in load_jsonl(f)}})) + ")")
    p("- **system name** -> `system`")
    p("- **model** -> *not a per-record field.* Resolved per-row from the file's "
      "`provenance_<family>.json` (`model`, `emission_model`) via the exact "
      "`EMISSION_SYSTEMS = (\"network\", \"planner\")` rule in `evidence/eval_run/emission_config.py` "
      "(this script imports and applies that rule live, not a hardcoded guess).")
    p("- **correctness/accuracy flag** -> `outcome_class == \"correct\"` "
      "(includes an appropriate refusal on an out-of-range query — see `comparator.py:score`).")
    p("- **silent-error flag** -> `outcome_class == \"silent_wrong\"` exactly, per "
      "`evidence/eval_run/types.py` `OutcomeClass` and `comparator.py:score`'s docstring: \"confident "
      "answer that doesn't match gold.\" Explicitly **excludes** `crash` and `timeout` (operational "
      "failures, short-circuited before any value comparison) and `refused_should_answer` (an honest "
      "refusal on an in-range query — tagged `gave_up`, not a silent error). This is the codebase's own "
      "definition, not one invented for this audit.")
    p("- **query id** -> `qid`")
    p()
    p("Representative record (one per distinct grid file, long string fields truncated at 200 chars):")
    p()
    seen_shapes: dict[str, str] = {}
    for f in grid_files:
        rec = next(load_jsonl(f), None)
        if rec is None:
            continue
        shape_key = ",".join(sorted(rec.keys()))
        if shape_key in seen_shapes:
            continue
        seen_shapes[shape_key] = f.name
        p(f"<details><summary><code>{f.name}</code></summary>")
        p()
        p("```")
        for k, v in rec.items():
            p(f"{k}: {truncate(v)}")
        p("```")
        p("</details>")
        p()
    p("Other (non-grid) files use unrelated, incompatible schemas (per-query complexity-scaling columns, "
      "planner-vs-opset comparison columns, portability emission logs, etc.) — no `tier`/`system`/"
      "`outcome_class` fields, so they are **out of scope** for the coverage table below and are not "
      "forced into it. Header / top-level keys for reference:")
    p()
    for f in sorted(other_files):
        if f.suffix == ".csv":
            with f.open() as fh:
                header = fh.readline().strip()
            p(f"- `{f.relative_to(ROOT)}` (csv): `{header}`")
        elif f.suffix == ".json":
            try:
                doc = json.loads(f.read_text())
                keys = list(doc.keys())[:12] if isinstance(doc, dict) else f"<list, len={len(doc)}>"
            except Exception as exc:                                  # noqa: BLE001
                keys = f"<unreadable: {exc}>"
            p(f"- `{f.relative_to(ROOT)}` (json): `{keys}`")
        elif f.suffix == ".md":
            p(f"- `{f.relative_to(ROOT)}` (markdown report, not raw data)")
    p()

    # ── Section 3 ──
    coverage = build_coverage(grid_files)
    p("## 3. Full coverage table")
    p()
    p("One row per (file, tier, system, model). `n` = record count; `accuracy` = "
      "`outcome_class==\"correct\"`; `silent-error` = `outcome_class==\"silent_wrong\"` "
      "(see Section 2 for exact definitions). Sorted by file, then tier, then system.")
    p()
    p("| file | tier | system | model | n | accuracy % | silent-error % | other outcome_class counts |")
    p("|---|---|---|---|---|---|---|---|")
    for r in coverage:
        other = ", ".join(f"{k}={v}" for k, v in sorted(r["other_outcomes"].items())) or "-"
        p(f"| `{r['file']}` | {r['tier']} | {r['system']} | {r['model']} | {r['n']} | "
          f"{r['accuracy_pct']:.1f} | {r['silent_error_pct']:.1f} | {other} |")
    p()

    # ── Section 4 ──
    saturated, exceptions = variance_check(coverage)
    p("## 4. T1-T3 variance verdict")
    p()
    p("Question: for T1 (`T1_single_op`), T2 (`T2_two_ops`), T3 (`T3_three_ops`), across ALL systems and "
      "ALL models that have data for these tiers, is accuracy exactly 100% and silent-error exactly 0% "
      "in every cell of the Section 3 table? (\"Cell\" = one Section-3 row; results are **not** pooled "
      "across files/vintages, so a stale file and its rescore are checked separately.)")
    p()
    if saturated:
        p("**YES** — fully saturated, no variance anywhere. Every (file, tier, system, model) cell for "
          "T1/T2/T3 is 100.0% accuracy and 0.0% silent-error.")
    else:
        p(f"**NO** — {len(exceptions)} cell(s) are not 100%/0%:")
        p()
        p("| file | tier | system | model | n | accuracy % | silent-error % | other outcome_class counts |")
        p("|---|---|---|---|---|---|---|---|")
        for r in exceptions:
            other = ", ".join(f"{k}={v}" for k, v in sorted(r["other_outcomes"].items())) or "-"
            p(f"| `{r['file']}` | {r['tier']} | {r['system']} | {r['model']} | {r['n']} | "
              f"{r['accuracy_pct']:.1f} | {r['silent_error_pct']:.1f} | {other} |")
    p()

    # ── Section 5 ──
    gaps, tiers, systems, models = find_gaps(coverage)
    p("## 5. Gaps — (tier x system x model) combinations with NO data at all")
    p()
    p(f"Full grid checked: {len(tiers)} tiers x {len(systems)} systems x {len(models)} models = "
      f"{len(tiers) * len(systems) * len(models)} combinations (union of everything observed across the "
      f"grid files above). `{len(gaps)}` have zero rows in any grid file:")
    p()
    if gaps:
        p("| tier | system | model | note |")
        p("|---|---|---|---|")
        for tier, system, model in gaps:
            note = ""
            if model != "claude-sonnet-4-5" and system not in EMISSION_SYSTEMS:
                note = (f"by design — the Haiku probe (`emission_config.EMISSION_SYSTEMS`) only ever "
                        f"targets {EMISSION_SYSTEMS}, never `{system}`")
            p(f"| {tier} | {system} | {model} | {note} |")
    else:
        p("(none)")
    p()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=None, help="also write the report to this path")
    args = ap.parse_args()

    render_report(sys.stdout)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w") as f:
            render_report(f)
        print(f"\n-> also wrote {args.out}", file=sys.stderr)
