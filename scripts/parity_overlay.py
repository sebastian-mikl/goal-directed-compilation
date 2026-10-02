"""Temperature-parity overlay for the paper pipeline.

When the environment variable PARITY_TEMP0=1 is set, `apply_overlay` replaces the
three default-temperature baselines' (react, code_agent, text_to_sql) recorded
in-contract emissions with their temperature-0 re-emissions produced by
scripts/rerun_temp0_parity.py. GDC (`network`), planner, every out-of-contract
row, and the Haiku model-robustness rows are left untouched by this baseline
overlay (they are not in the overlay map, or are not baseline systems; the
re-run files below are the exception). With the flag unset, `apply_overlay`
returns the recorded rows unchanged, so the default `_load` behaviour — and the
committed reports/paper_numbers.csv — is byte-identical to before this module
existed.

Matching is by the exact (system, qid, seed) key, so a temp-0 re-emission drops
in for precisely the recorded cell it replaces. The overlay files hold ONLY the
in-contract cells that were re-run; anything not present (e.g. out_of_range) is
left at its recorded default temperature, which `coverage_report()` surfaces so a
partial run cannot silently masquerade as a full parity re-run.

Re-run files (RERUN_MAP). Their rows replace recorded rows of ANY system by key.
  - results/results_temp0_batch2_<domain>.jsonl, one per domain (employee, wta_1,
    world_1, bike_1): the second temperature-0 batch, run under each cell's
    recorded prompts. A file that is absent changes nothing, so the build is
    identical to one without it. The batch's alias arm
    (results_temp0_batch2_employee_alias.jsonl) replaces no reported cell and is
    not read. The batch was planned from the cell manifest of a provenance audit
    that covers the withheld risk domain, so its script is not in this release;
    each file's provenance records how it was run.

Conflicts. A key held by two overlay files for the same recorded file aborts the
load, unless SUPERSEDES declares which of the two wins. The second batch re-runs
cells that already have a temperature-0 row (every code_agent cell, the react
cells whose tool outputs change under the fixed engine), so each batch2 file is
declared to supersede its domain's parity file.

Every present overlay file must have its `.provenance.json`, recording
temperature 0. Every key a present re-run file holds must replace a recorded
cell the builder reads (READ_SYSTEMS: not the pre-fix GDC rows of the Spider
five-system files): `check_overlays()` raises on any that did not (a re-run of a
cell no table reads, or a key typo, would otherwise be dropped without a trace). The Haiku
panel's Sonnet side reads its files without any overlay (build_paper_numbers.py,
section G).

A present batch2 file is used only whole, clean and on this engine
(`check_batch2`, run when the file is read and again by `check_overlays()`):
  (a) it holds exactly the recorded-arm cells the batch planned for its domain,
      which the batch writes into its provenance (`batch2.plan_keys`). A batch
      stopped by its spend cap holds fewer; a build from it would mix re-run,
      parity and recorded cells in one reported group;
  (b) no row is a cell the run did not complete: a prompt-guard refusal
      ("refusing to send"), a credit or billing error, or a crash the harness
      logged for an exception out of the adapter;
  (c) it was produced on the engine this build runs, the files the builder
      replays GDC and planner on and the baselines ran on. Otherwise GDC and
      the baselines would be scored on two engines. The batch ran in the
      development tree, where the GDC package was laid out as gdc/primitives.py
      plus planner/*.py and some comments differ, so its recorded
      `batch2.fixed_code.sha256` cannot equal this tree's hashes file for file.
      PORTED_ENGINE pins the pair instead: the recorded engine a batch2 file
      must carry (engine_sha of its fixed_code.sha256) and the engine this tree
      must have (engine_sha of engine_hashes()). An engine change on either
      side fails the check until the pin is renewed.
PARTIAL_BATCH2=1 lets a file that holds a strict subset of its plan through (a)
only, for inspection; every value that reads one says so in its source label.

Provenance: every replaced row carries `overlay_source` (the overlay file it came
from), and `provenance_rows()` lists every unique replaced cell with its recorded
source file and overlay file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"
PARITY = os.environ.get("PARITY_TEMP0") == "1"
PARTIAL_BATCH2 = os.environ.get("PARTIAL_BATCH2") == "1"
BASELINE_SYS = {"react", "code_agent", "text_to_sql"}

# The engine: the files GDC and the planner are replayed on and the baselines run
# on (the GDC package with the shared executor, the tool layers, the harness and
# every adapter). The builder records engine_hashes() beside the CSV it writes.
ENGINE_GLOBS = ("gdc/*.py", "evidence/eval_run/harness.py",
                "evidence/eval_run/tools_code.py", "evidence/eval_run/tools_decl.py",
                "evidence/eval_run/tools_sql.py", "evidence/eval_run/adapters/*.py")


def engine_files(root: Path = ROOT) -> list[str]:
    return sorted({str(p.relative_to(root)) for g in ENGINE_GLOBS for p in Path(root).glob(g)})


def engine_hashes(root: Path = ROOT) -> dict[str, str]:
    return {f: hashlib.sha256((Path(root) / f).read_bytes()).hexdigest() for f in engine_files(root)}


def engine_sha(hashes: dict[str, str]) -> str:
    """One sha256 over the engine files' hashes (sorted by path)."""
    return hashlib.sha256(json.dumps(sorted(hashes.items())).encode()).hexdigest()


# Check (c) for the second batch (module docstring). `recorded_engine_sha256` is the
# development tree's engine at the commit the batch ran on; `engine_sha256` is this
# tree's port of it. Renew `engine_sha256` alone only for a change to this tree's
# engine files that leaves their code as it was (comments, docstrings). After any
# other change the batch no longer ran on this engine.
PORTED_ENGINE = {
    "recorded_engine_sha256": "50ef511db5675acabca0e1d1ed8f2bd54cde367bd8344f3cb1375d79bcbfcf33",
    "engine_sha256": "2ef059df8ff443236150be214c18f10eb36c6bfd1862f8ebb5f34e3a2bab7bf6",
}


GUARD = "refusing to send"
_BILLING = re.compile(r"credit balance|billing|insufficient (?:credit|funds|quota)|payment required",
                      re.IGNORECASE)


def incomplete_row(r: dict) -> str | None:
    """Why a batch2 row is not a completed cell, or None. A prompt-guard refusal and a
    credit or billing error are not outcomes of the cell; neither is a crash the
    harness logged for an exception out of the adapter (run_cell's own record)."""
    err = str(r.get("error") or "")
    if GUARD in err:
        return "prompt-guard refusal"
    if _BILLING.search(err):
        return "credit/billing error"
    art = r.get("artifact")
    if r.get("status") == "crashed" and isinstance(art, dict) and "exception" in art:
        return "harness-logged crash"
    return None

# recorded source file (basename) -> per-domain temp-0 overlay file (basename).
# The three risk source files share one risk overlay; each picks up only the
# (system, qid, seed) keys it actually contains.
OVERLAY_MAP = {
    "results_spider_wta_1.jsonl":   "results_temp0_parity_wta_1.jsonl",
    "results_spider_world_1.jsonl": "results_temp0_parity_world_1.jsonl",
    "results_spider_bike_1.jsonl":  "results_temp0_parity_bike_1.jsonl",
    "results_off.rescored.jsonl":       "results_temp0_parity_risk.jsonl",
    "results_t5.rescored.jsonl":        "results_temp0_parity_risk.jsonl",
    "results_risk_t5_missing.jsonl":    "results_temp0_parity_risk.jsonl",
    "results_employee.jsonl":       "results_temp0_parity_employee.jsonl",
}

# domain -> the recorded files whose cells a re-run of that domain replaces. Spider
# GDC cells live in the postfix_s40off/on files and are never re-run, so a Spider
# re-run maps onto the baselines' file only.
DOMAIN_SOURCES = {
    "employee": ("results_employee.jsonl",),
    "wta_1":    ("results_spider_wta_1.jsonl",),
    "world_1":  ("results_spider_world_1.jsonl",),
    "bike_1":   ("results_spider_bike_1.jsonl",),
}
PARITY_FILE = {dom: OVERLAY_MAP[srcs[0]] for dom, srcs in DOMAIN_SOURCES.items()}
# recorded file -> the systems the builder reads from it (default: all). The Spider
# five-system files also hold the pre-fix GDC rows, which no number reads.
READ_SYSTEMS = {f"results_spider_{d}.jsonl": {"planner"} | BASELINE_SYS
                for d in ("wta_1", "world_1", "bike_1")}


def batch2_file(domain: str) -> str:
    return f"results_temp0_batch2_{domain}.jsonl"


# recorded source file (basename) -> re-run files (basenames) whose rows replace
# recorded rows of ANY system by exact key.
RERUN_MAP: dict[str, tuple[str, ...]] = {
    src: (batch2_file(dom),) for dom, srcs in DOMAIN_SOURCES.items() for src in srcs
}

BATCH2_FILES = frozenset(batch2_file(dom) for dom in DOMAIN_SOURCES)

# overlay file -> the overlay files it wins over when both hold a key.
SUPERSEDES: dict[str, tuple[str, ...]] = {
    batch2_file(dom): (PARITY_FILE[dom],) for dom in DOMAIN_SOURCES
}


def provenance_path(overlay_fn: str, root: Path = RESULTS_DIR) -> Path:
    return root / overlay_fn.replace(".jsonl", ".provenance.json")


class Overlay:
    """The overlay state of one build: rows per overlay file, and which recorded
    cells each one replaced. The module functions below use one instance."""

    def __init__(self, root: Path = RESULTS_DIR, enabled: bool = PARITY,
                 overlay_map: dict = OVERLAY_MAP, rerun_map: dict = RERUN_MAP,
                 supersedes: dict = SUPERSEDES, read_systems: dict = READ_SYSTEMS,
                 partial_ok: bool = PARTIAL_BATCH2, engine_root: Path = ROOT,
                 batch2_files: frozenset = None, ported_engine: dict = PORTED_ENGINE):
        self.root, self.enabled = Path(root), enabled
        self.partial_ok, self.engine_root = partial_ok, Path(engine_root)
        self.batch2_files = BATCH2_FILES if batch2_files is None else frozenset(batch2_files)
        self._partial: dict[str, tuple[int, int]] = {}   # batch2 file -> (held, planned)
        self.overlay_map, self.rerun_map, self.supersedes = overlay_map, rerun_map, supersedes
        self.read_systems = read_systems
        self.ported_engine = ported_engine
        self._rows: dict[str, dict] = {}
        self._stats: dict[str, dict] = {}        # parity: source file -> replaced / left_default keys
        self._placed: dict[str, set] = {}        # overlay file -> keys it replaced
        self._superseded: dict[str, set] = {}    # overlay file -> keys it held but another file won
        self._provenance: dict[tuple, str] = {}  # (source_fn, system, qid, seed) -> overlay file

    def rows(self, overlay_fn: str) -> dict:
        if overlay_fn not in self._rows:
            rows: dict = {}
            p = self.root / overlay_fn
            if p.exists():
                prov = provenance_path(overlay_fn, self.root)
                if not prov.exists():
                    raise RuntimeError(f"{overlay_fn} has no {prov.name}: an overlay must record how it was run")
                temp = json.loads(prov.read_text()).get("temperature")
                if temp != 0:
                    raise RuntimeError(f"{prov.name} records temperature {temp!r}; overlays are temperature 0")
                for line in p.read_text().splitlines():
                    if line.strip():
                        r = json.loads(line)
                        rows[(r["system"], r["qid"], r["seed"])] = r
                if overlay_fn in self.batch2_files:
                    self.check_batch2(overlay_fn, rows, json.loads(prov.read_text()))
            self._rows[overlay_fn] = rows
        return self._rows[overlay_fn]

    def check_batch2(self, fn: str, rows: dict, prov: dict) -> None:
        """Raise unless a present batch2 file is whole (a), clean (b) and from this
        engine (c); see the module docstring. With partial_ok, (a) admits a strict
        subset of the plan and the file is recorded as partial."""
        b2 = prov.get("batch2") or {}
        planned = {tuple(k) for k in b2.get("plan_keys") or ()}
        if not planned:
            raise RuntimeError(f"{fn}: its provenance records no batch2.plan_keys, so the "
                               f"builder cannot tell a whole batch from a partial one")
        held = set(rows)
        extra, missing = held - planned, planned - held
        if extra:
            raise RuntimeError(f"{fn}: {len(extra)} cells the batch did not plan: {sorted(extra)[:3]}")
        if missing and not self.partial_ok:
            raise RuntimeError(f"{fn}: {len(held)} of {len(planned)} planned cells; a partial batch "
                               f"would mix re-run, parity and recorded cells in one reported group "
                               f"(missing {sorted(missing)[:3]}). Finish the batch, or set "
                               f"PARTIAL_BATCH2=1 to build from it anyway.")
        bad = sorted((k, why) for k, r in rows.items() if (why := incomplete_row(r)))
        if bad:
            raise RuntimeError(f"{fn}: {len(bad)} rows are not completed cells, e.g. {bad[:3]}; "
                               f"delete them and resume the batch")
        recorded = (b2.get("fixed_code") or {}).get("sha256") or {}
        rec_sha, cur_sha = engine_sha(recorded), engine_sha(engine_hashes(self.engine_root))
        pin = self.ported_engine
        if rec_sha != pin["recorded_engine_sha256"]:
            raise RuntimeError(f"{fn} was produced on engine {rec_sha[:12]}, not the engine this "
                               f"tree is a port of ({pin['recorded_engine_sha256'][:12]}; "
                               f"parity_overlay.PORTED_ENGINE)")
        if cur_sha != pin["engine_sha256"]:
            raise RuntimeError(f"this tree's engine is {cur_sha[:12]}, not the port pinned for "
                               f"{fn} ({pin['engine_sha256'][:12]}; parity_overlay.PORTED_ENGINE): "
                               f"an engine file changed since the batch ran")
        if missing:
            self._partial[fn] = (len(held), len(planned))

    def _layers(self, source_fn: str) -> dict:
        """overlay file -> {key: row} it offers for `source_fn`, lowest precedence first."""
        layers = {}
        if source_fn in self.overlay_map:
            fn = self.overlay_map[source_fn]
            layers[fn] = {k: r for k, r in self.rows(fn).items() if k[0] in BASELINE_SYS}
        read = self.read_systems.get(source_fn)
        for fn in self.rerun_map.get(source_fn, ()):
            layers[fn] = {k: r for k, r in self.rows(fn).items() if read is None or k[0] in read}
        return layers

    def _wins(self, a: str, b: str) -> bool:
        return b in self.supersedes.get(a, ())

    def apply(self, source_fn: str, rows: list) -> list:
        if not self.enabled or (source_fn not in self.overlay_map and source_fn not in self.rerun_map):
            return rows
        layers = self._layers(source_fn)
        names = list(layers)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                both = set(layers[a]) & set(layers[b])
                if both and not (self._wins(a, b) or self._wins(b, a)):
                    raise RuntimeError(f"{len(both)} cells of {source_fn} have two temperature-0 sources "
                                       f"({a}, {b}) and no declared precedence: {sorted(both)[:3]}")
        parity_fn = self.overlay_map.get(source_fn)
        # track UNIQUE (system, qid, seed) keys — build_paper_numbers loads each file
        # several times, so cumulative counters would over-report; sets do not.
        st = self._stats.setdefault(source_fn, {"replaced": set(), "left_default": set()})
        out = []
        for r in rows:
            key = (r.get("system"), r.get("qid"), r.get("seed"))
            holders = [fn for fn in names if key in layers[fn]]
            if not holders:
                if parity_fn and r.get("system") in BASELINE_SYS:
                    st["left_default"].add(key)  # not re-run (e.g. out_of_range) -> stays default temp
                out.append(r)
                continue
            win = next((fn for fn in holders if all(fn == o or self._wins(fn, o) for o in holders)), None)
            if win is None:
                raise RuntimeError(f"{source_fn} {key}: no overlay file of {holders} supersedes the others")
            for fn in holders:
                (self._placed if fn == win else self._superseded).setdefault(fn, set()).add(key)
            if parity_fn in holders:
                st["replaced"].add(key)
            self._provenance[(source_fn,) + key] = win
            out.append(dict(layers[win][key], overlay_source=win))
        return out

    def rerun_files(self) -> list:
        return sorted({fn for fns in self.rerun_map.values() for fn in fns})

    def rerun_note(self, rows) -> str:
        fns = sorted({r.get("overlay_source") for r in rows} & set(self.rerun_files()))
        part = [f"{fn} {self._partial[fn][0]}/{self._partial[fn][1]} planned cells"
                for fn in fns if fn in self._partial]
        return ((f" [temp-0 re-run: {', '.join(fns)}]" if fns else "")
                + (f" [PARTIAL_BATCH2=1: {'; '.join(part)}]" if part else ""))

    def provenance_rows(self) -> list:
        return sorted((k[0], fn) + k[1:] for k, fn in self._provenance.items())

    def unplaced(self, overlay_fn: str) -> set:
        held = self._placed.get(overlay_fn, set()) | self._superseded.get(overlay_fn, set())
        return set(self.rows(overlay_fn)) - held

    def check(self) -> None:
        """Raise if a present re-run file holds a key that replaced no recorded cell, or
        a present batch2 file is not whole, clean and from this engine."""
        if not self.enabled:
            return
        for fn in sorted(self.batch2_files):
            if (self.root / fn).exists():
                self.rows(fn)                    # check_batch2 runs on read
        for fn in self.rerun_files():
            miss = self.unplaced(fn)
            if miss:
                raise RuntimeError(f"{fn}: {len(miss)} re-run cells match no recorded cell of "
                                   f"{sorted(s for s, fns in self.rerun_map.items() if fn in fns)}: "
                                   f"{sorted(miss)[:3]}")

    def coverage_report(self) -> str:
        if not self.enabled:
            return ""
        lines = ["PARITY_TEMP0 overlay coverage (unique baseline cells replaced with temperature-0 re-emissions):"]
        if not self._stats:
            lines.append("  (no overlaid source files were read)")
        by_overlay: dict[str, dict] = {}
        for fn, st in self._stats.items():
            if fn not in self.overlay_map:
                continue
            agg = by_overlay.setdefault(self.overlay_map[fn], {"replaced": set(), "left_default": set()})
            agg["replaced"] |= st["replaced"]
            agg["left_default"] |= st["left_default"]
        total_replaced = 0
        for ov, st in sorted(by_overlay.items()):
            present = "present" if (self.root / ov).exists() else "MISSING"
            rep, ld = len(st["replaced"]), len(st["left_default"] - st["replaced"])
            total_replaced += rep
            sup = len(self._superseded.get(ov, ()))
            lines.append(f"  {ov:<34} [{present}] replaced={rep:>4} left_default={ld:>4}"
                         + (f"  (superseded by a re-run: {sup})" if sup else ""))
        lines.append(f"  TOTAL replaced (unique baseline cells) = {total_replaced}"
                     "   (expect 1134 for the domains in this release: spider 405, employee 729; the full"
                     " 2379 also counts the withheld risk domain's 1245 cells)")
        lines.append("  (left_default = baseline cells with no temp-0 re-emission: expected for out_of_range /")
        lines.append("   any out-of-contract tier; should be 0 across the in-contract answer tiers.)")
        absent = []
        for rf in self.rerun_files():
            if not (self.root / rf).exists():
                absent.append(rf)
                continue
            placed = self._placed.get(rf, set())
            by_sys: dict[str, int] = {}
            for k in placed:
                by_sys[k[0]] = by_sys.get(k[0], 0) + 1
            sup = len(self._superseded.get(rf, ()))
            lines.append(f"  {rf:<34} [present] replaced={len(placed):>4} "
                         f"(any system: {', '.join(f'{s} {n}' for s, n in sorted(by_sys.items()))})"
                         + (f"  superseded={sup}" if sup else "") + f"  unplaced={len(self.unplaced(rf))}")
        lines.append("  (re-run rows replace recorded rows of every system by key; unplaced should be 0)")
        for fn, (n, m) in sorted(self._partial.items()):
            lines.append(f"  PARTIAL_BATCH2=1: {fn} holds {n} of its {m} planned cells")
        if absent:
            lines.append(f"  not present (no effect): {', '.join(absent)}")
        return "\n".join(lines)


_OVERLAY = Overlay()


def apply_overlay(source_fn: str, rows: list) -> list:
    """Return `rows` with baseline in-contract cells replaced by temp-0 versions
    (and the re-run files' cells, any system) when PARITY_TEMP0=1; otherwise
    return `rows` unchanged."""
    return _OVERLAY.apply(source_fn, rows)


def rerun_note(rows) -> str:
    """Provenance suffix naming the re-run file(s) any of `rows` came from ('' if none)."""
    return _OVERLAY.rerun_note(rows)


def provenance_rows() -> list:
    """Every unique replaced cell: (source_file, overlay_file, system, qid, seed)."""
    return _OVERLAY.provenance_rows()


def check_overlays() -> None:
    """Raise if a present re-run file holds cells that replaced no recorded cell, or a
    present batch2 file is partial (unless PARTIAL_BATCH2=1), holds rows that are not
    completed cells, or was produced on other engine code."""
    _OVERLAY.check()


def coverage_report() -> str:
    """Human-readable overlay coverage (unique baseline cells); empty when PARITY off."""
    return _OVERLAY.coverage_report()
