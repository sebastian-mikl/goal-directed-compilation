"""The cells behind reports/paper_numbers.csv, read one way and replayed one way.

scripts/build_paper_numbers.py builds every reported number from the functions
here. The analyses that explain those numbers (pass3_outcome_stats.py,
ablate_checks.py, uncaught_silent_extract.py, measure_search_cost.py,
replay_risk_employee.py) read their cells through the same functions, so an
analysis never runs on other emissions than the table it explains.

Inputs. `load()` reads a recorded results file (under results/) and applies the
temperature-0 overlays of scripts/parity_overlay.py (PARITY_TEMP0=1; a no-op
otherwise). `retier()` stamps each row with its tier of record, the gold
record's. The files per domain and system are the constants below. The risk
domain's data is withheld from this release: without its gold, HAS_RISK is
False and every risk row set is empty.

Replay. GDC (`network`) and the planner baseline are scored on what the current
engine does with their recorded emissions, not on the recorded raw_result. A GDC
Goal goes through replay_goals.run_deterministic (NetworkAdapter.run after the
API call); a planner reply goes through planner_upfront.execute_reply
(PlannerUpfrontAdapter.run after the API call: tools_decl._apply_one ->
gdc.primitives). The two systems share gdc/primitives.py, so they are scored on
one engine, in every domain. A cell with nothing to replay (a decline or parse
error at emission, so no Goal; a planner row with no reply text) keeps its
recorded status and result. react, code_agent and text_to_sql are scored on their
recorded raw_result: react's later calls depend on its earlier tool outputs, so
its trace cannot be replayed without the model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from evidence.eval_run.adapters.planner_upfront import execute_reply  # noqa: E402
from evidence.eval_run.types import REFUSAL, empty_run_result         # noqa: E402
from scripts.parity_overlay import apply_overlay                       # noqa: E402
from scripts.replay_goals import (DOMAIN_CONFIGS, DomainContext,       # noqa: E402
                                  rebuild_goal, rebuild_order, run_deterministic)

SPIDER = ["wta_1", "world_1", "bike_1"]
SYSTEMS = ["network", "planner", "react", "code_agent", "text_to_sql"]
REPLAYED = ("network", "planner")
GOLD = {dom: json.loads(cfg["gold"].read_text()) for dom, cfg in DOMAIN_CONFIGS.items()
        if cfg["gold"].exists()}   # a public checkout may lack a withheld domain
HAS_RISK = "risk" in GOLD


def scored(rows: list, gold: dict) -> list:
    """The rows a reported set scores: those whose question has a gold record. A question
    dropped from scoring (employee oor_03, D11) is in no gold, so no reported set holds its
    recorded cells; they stay in the results files."""
    return [r for r in rows if r.get("qid") in gold]


# risk: T1-T4, house_vocab and algebra_boundary from RISK_OFF_FILE; T5 from
# RISK_T5_FILES (results_t5 for network/planner/react, the live fill for
# code_agent/text_to_sql). results_t5 also holds non-T5 rows; they are not read.
RISK_OFF_FILE = "results_off.rescored.jsonl"
RISK_T5_FILES = ("results_t5.rescored.jsonl", "results_risk_t5_missing.jsonl")
T5 = "T5_high_complexity"
EMPLOYEE_FILE = "results_employee.jsonl"
HAIKU_FILE = "results_haiku.rescored.jsonl"


def spider_file(d: str, system: str, s40: str = "off") -> str:
    """Spider GDC = the post-fix Phase R run (table-coverage instruction off or on);
    the baselines = the recorded five-system run."""
    return f"results_spider_{d}_postfix_s40{s40}.jsonl" if system == "network" else f"results_spider_{d}.jsonl"


def load(p, overlay: bool = True) -> list:
    pp = RESULTS_DIR / p if not isinstance(p, Path) else p
    rows = [json.loads(l) for l in pp.read_text().splitlines() if l.strip()] if pp.exists() else []
    # PARITY_TEMP0=1 -> temp-0 overlays (baselines; re-run files, any system); else no-op
    return apply_overlay(pp.name, rows) if overlay else rows


def retier(rows: list, gold: dict) -> list:
    """Tier of record is the gold record's, not the tier stamped on the row at run
    time: risk's 10 out_of_range queries were reclassified to algebra_boundary."""
    return [dict(r, tier=gold[r["qid"]]["tier"]) if r.get("qid") in gold and r.get("tier") != gold[r["qid"]]["tier"]
            else r for r in rows]


_sets: dict = {}


def _held(name: str, overlay: bool, build) -> list:
    """One read per row set and overlay setting; every consumer gets the same rows."""
    if (name, overlay) not in _sets:
        _sets[(name, overlay)] = build()
    return _sets[(name, overlay)]


def risk_off(overlay: bool = True) -> list:
    if not HAS_RISK:
        return []
    return _held("risk_off", overlay, lambda: retier(load(RISK_OFF_FILE, overlay), GOLD["risk"]))


def risk_t5(overlay: bool = True) -> list:
    if not HAS_RISK:
        return []
    return _held("risk_t5", overlay,
                 lambda: retier(sum((load(f, overlay) for f in RISK_T5_FILES), []), GOLD["risk"]))


def employee() -> list:
    return _held("employee", True, lambda: retier(load(EMPLOYEE_FILE), GOLD["employee"]))


def network_cells(dom: str) -> list:
    """(file, row) for every GDC row a reported number is built from in `dom`:
    risk T1-T4/hv/algebra_boundary from results_off and T5 from results_t5,
    employee, and Spider s40off (every tier) plus s40on (out_of_scope_register)."""
    if dom == "risk":
        return ([(RISK_OFF_FILE, r) for r in risk_off() if r["system"] == "network" and r["tier"] != T5]
                + [(RISK_T5_FILES[0], r) for r in risk_t5() if r["system"] == "network" and r["tier"] == T5])
    if dom == "employee":
        return [(EMPLOYEE_FILE, r) for r in employee() if r["system"] == "network"]
    out = []
    for s40 in ("off", "on"):
        fn = spider_file(dom, "network", s40)
        out += [(fn, r) for r in retier(load(fn), GOLD[dom]) if r.get("system") == "network"
                and (s40 == "off" or r.get("tier") == "out_of_scope_register")]   # s40on: that tier only (D7: wta_26)
    return out


# ── replay (current engine) ─────────────────────────────────────────────────
# Keyed on the emission: replay is a pure function of it, and seeds of one query
# can emit different goals. (Was keyed on (dom, qid), which replayed the first
# seed's goal for every seed of a query.)
_net_cache: dict = {}
_plan_cache: dict = {}
_registers: dict = {}


def replay_network(dom: str, row: dict):
    """(result, status) of the current engine on the row's recorded Goal + OrderSpec."""
    art = row.get("artifact") or {}
    if not (isinstance(art, dict) and art.get("goal")):
        return row.get("raw_result"), row.get("status", "completed")  # emission-time refusal
    key = (dom, json.dumps(art["goal"], sort_keys=True), json.dumps(art.get("order") or {}, sort_keys=True))
    if key not in _net_cache:
        ctx = DomainContext.get(dom)
        goal = rebuild_goal(art["goal"]); order = rebuild_order(art.get("order") or {})
        status, res, _ = run_deterministic(ctx, goal, order)
        _net_cache[key] = (res, status)
    return _net_cache[key]


def register(dom: str) -> pd.DataFrame:
    """The raw register, as the harness hands it to every baseline adapter."""
    if dom not in _registers:
        _registers[dom] = pd.read_pickle(DOMAIN_CONFIGS[dom]["register"])
    return _registers[dom]


def replay_planner(dom: str, row: dict):
    """(result, status) of the current executor on the row's recorded plan reply."""
    art = row.get("artifact") or {}
    text = art.get("raw_text") if isinstance(art, dict) else None
    if text is None:
        return row.get("raw_result"), row.get("status", "completed")  # no reply recorded
    key = (dom, text)
    if key not in _plan_cache:
        try:
            rec = execute_reply(text, register(dom), empty_run_result())
            _plan_cache[key] = (rec["result"], rec["status"])
        except Exception:                                  # noqa: BLE001
            _plan_cache[key] = (REFUSAL, "crashed")        # escaped run(): harness.run_cell records a crash
    return _plan_cache[key]


def scored_input(dom: str, row: dict):
    """(result, status) the comparator scores for one row: the replay for GDC and
    planner, the recorded raw_result for the other three systems."""
    if row.get("system") == "network":
        return replay_network(dom, row)
    if row.get("system") == "planner":
        return replay_planner(dom, row)
    return row.get("raw_result"), row.get("status", "completed")
