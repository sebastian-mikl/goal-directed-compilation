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
    return _network_replay(dom, row)[:2]


def _network_replay(dom: str, row: dict):
    """(result, status, error label) of the replay; the recorded ones for a row with no Goal."""
    art = row.get("artifact") or {}
    if not (isinstance(art, dict) and art.get("goal")):                 # emission-time refusal
        return row.get("raw_result"), row.get("status", "completed"), row.get("error")
    key = (dom, json.dumps(art["goal"], sort_keys=True), json.dumps(art.get("order") or {}, sort_keys=True))
    if key not in _net_cache:
        ctx = DomainContext.get(dom)
        goal = rebuild_goal(art["goal"]); order = rebuild_order(art.get("order") or {})
        status, res, err = run_deterministic(ctx, goal, order)
        _net_cache[key] = (res, status, err)
    return _net_cache[key]


def register(dom: str) -> pd.DataFrame:
    """The raw register, as the harness hands it to every baseline adapter."""
    if dom not in _registers:
        _registers[dom] = pd.read_pickle(DOMAIN_CONFIGS[dom]["register"])
    return _registers[dom]


def replay_planner(dom: str, row: dict):
    """(result, status) of the current executor on the row's recorded plan reply."""
    return _planner_replay(dom, row)[:2]


def _planner_replay(dom: str, row: dict):
    """(result, status, error, refuse envelope?) of the replay; the recorded ones for a
    row with no reply."""
    art = row.get("artifact") or {}
    text = art.get("raw_text") if isinstance(art, dict) else None
    if text is None:                                                      # no reply recorded
        return (row.get("raw_result"), row.get("status", "completed"), row.get("error"),
                isinstance(art, dict) and "refusal_reason" in art)
    key = (dom, text)
    if key not in _plan_cache:
        try:
            rec = execute_reply(text, register(dom), empty_run_result())
            _plan_cache[key] = (rec["result"], rec["status"], rec.get("error"),
                                "refusal_reason" in (rec.get("artifact") or {}))
        except Exception:                                  # noqa: BLE001
            _plan_cache[key] = (REFUSAL, "crashed", None, False)   # escaped run(): harness.run_cell records a crash
    return _plan_cache[key]


def scored_input(dom: str, row: dict):
    """(result, status) the comparator scores for one row: the replay for GDC and
    planner, the recorded raw_result for the other three systems."""
    if row.get("system") == "network":
        return replay_network(dom, row)
    if row.get("system") == "planner":
        return replay_planner(dom, row)
    return row.get("raw_result"), row.get("status", "completed")


# ── decline channel (D8) ────────────────────────────────────────────────────
# One decline rule for every system. A decline is an explicit decline statement or a
# typed refusal; SQL errors (the 60 s wall-clock interrupt among them), unparseable
# plans with no decline text, empty plans and empty results are failures. The
# comparator scores every one of them as a refusal (on a decline tier, `correct`);
# this says which it is, from the same replay or record the comparator scored.
#   explicit: GDC's emission-time decline (EmissionDecline); the planner's refuse
#             envelope, or an unparseable reply with decline text (the adapter's own
#             is_decline_text test); a react/code_agent run that ends in a statement
#             and no answer; a text-to-SQL reply with no SQL.
#   typed:    a refusal of the engine's or the executor's typed family: every refusal
#             the GDC replay returns (ContractError, GoalLint, Unreachable, the
#             FilterTypeError family incl. the type guards, OrderApplicationError,
#             PlanExecutionDivergence), a GDC ParseError raised by the schema check on a
#             reply that parsed as JSON ("missing required field", ...), and a planner
#             step's FilterTypeError family.
#   failed:   a text-to-SQL SQL error; a GDC ParseError on a reply that is not valid JSON;
#             a planner reply that does not parse and has no decline text, a malformed or
#             empty plan; a run that completed with no result (REFUSAL from an empty or
#             NaN frame).
# Crash and timeout keep their own classes (also failures, never declines).
DECLINE_CHANNELS = ("explicit", "typed", "failed")

# The schema line (decision of 3 Oct 2026, "draw the line at schema"), for every system: a model
# output that parses as JSON but fails GDC's Goal schema check is a typed refusal; output
# that is not valid JSON is a failure. A GDC ParseError's recorded artifact holds only the
# message (NetworkAdapter.run records {"kind", "error"}, no reply text), so the message is
# the signal. Every ParseError the engine raises (llm.py, contract.py), by where it is raised:
#   not valid JSON (failed): before the reply has parsed
#     llm.py _extract_json       "LLM1 output contained no JSON object"
#                                "LLM1 output had an unbalanced JSON object"
#     llm.py llm1                "LLM1 output was not valid JSON: <json.loads error>"
#   schema (typed): after json.loads accepted the reply
#     llm.py llm1                "LLM1 output missing 'goal'"
#     contract.py parse_goal     "not valid JSON: ..." (the goal value is a string that does
#                                not parse), "goal must be an object, got ...", "missing
#                                required field: ...", "'columns' / 'where' / 'derive' must
#                                be a list", "each where conjunct needs col/op/val: ...",
#                                "each derive entry needs name/expr: ...", "could not build
#                                Goal: ..."
#     llm.py _parse_order        "'order' must be an object, got ...", "'order.order_by'
#                                must be a list", "each order_by entry needs a 'col': ...",
#                                "'order.limit' must be an int or null, got ...", "could not
#                                build OrderSpec: ..."
# A message in neither list raises, so a new ParseError cannot fall into a class unread.
# The planner's "JSON parse error: ..." (json.loads rejected the reply) stays a failure.
GDC_PARSE_NOT_JSON = ("LLM1 output contained no JSON object",
                      "LLM1 output had an unbalanced JSON object",
                      "LLM1 output was not valid JSON")
GDC_PARSE_SCHEMA = ("LLM1 output missing 'goal'",
                    "not valid JSON:", "goal must be an object", "missing required field:",
                    "'columns' must be a list", "'where' must be a list", "'derive' must be a list",
                    "each where conjunct needs col/op/val", "each derive entry needs name/expr",
                    "could not build Goal:",
                    "'order' must be an object", "'order.order_by' must be a list",
                    "each order_by entry needs a 'col'", "'order.limit' must be an int or null",
                    "could not build OrderSpec:")


def gdc_parse_channel(err: str) -> str:
    """Channel of a GDC ParseError, from its recorded message: `failed` if the reply was not
    valid JSON, `typed` if it parsed and the Goal (or OrderSpec) schema check refused it."""
    msg = err[len("ParseError:"):].strip() if err.startswith("ParseError:") else err.strip()
    if msg.startswith(GDC_PARSE_NOT_JSON):
        return "failed"
    if msg.startswith(GDC_PARSE_SCHEMA):
        return "typed"
    raise ValueError(f"unclassified GDC ParseError message: {msg[:80]!r}")

# Hand readings (D8, Seb 2 Oct 2026: "statement-only endings that say the data cannot answer the
# question are explicit declines, for every system"). reports/decline_readings.json holds, for each
# no-answer cell the rule below would class `explicit` or `failed` on text alone (the planner's
# regex, react and code_agent's "ended in a statement", text-to-SQL's "no SQL"), the class the
# final text earns when read: `explicit` if it concludes that the data cannot answer the question
# and returns no answer, `failed` if it is an SQL error, a truncated reply, or a revised or
# substitute plan. decline_channel applies a reading before the rule; a cell with no reading keeps
# the rule's class. Keys are domain|system|qid|seed. The file is data, not code: the paper's list
# of per-cell classes ships from it.
DECLINE_READINGS_FILE = ROOT / "reports" / "decline_readings.json"
READING_CLASSES = ("explicit", "failed")


def load_decline_readings(path: Path = DECLINE_READINGS_FILE) -> dict:
    if not path.exists():
        return {}
    cells = json.loads(path.read_text(encoding="utf-8"))["cells"]
    bad = {k: v.get("class") for k, v in cells.items() if v.get("class") not in READING_CLASSES}
    if bad:
        raise ValueError(f"{path.name}: class must be one of {READING_CLASSES}: {sorted(bad.items())[:3]}")
    return cells


DECLINE_READINGS = load_decline_readings()


def reading_key(dom: str, row: dict) -> str:
    return f"{dom}|{row.get('system')}|{row.get('qid')}|{row.get('seed')}"


def decline_channel(dom: str, row: dict) -> tuple[str, str] | None:
    """(channel, detail) for a cell whose scored input is a no-answer refusal; None for an
    answer, a crash or a timeout. A hand reading (DECLINE_READINGS) overrides the rule's
    explicit/failed class; typed refusals are never read."""
    ch = rule_channel(dom, row)
    if ch is None:
        return None
    rd = DECLINE_READINGS.get(reading_key(dom, row))
    if rd is None:
        return ch
    if ch[0] == "typed":
        raise ValueError(f"decline reading for a typed refusal: {reading_key(dom, row)}")
    return rd["class"], "read: " + rd.get("reading", "")


def rule_channel(dom: str, row: dict) -> tuple[str, str] | None:
    """The m2 rule, with no reading applied: (channel, detail) or None."""
    sysname = row.get("system")
    if sysname == "network":
        res, status, err = _network_replay(dom, row)
    elif sysname == "planner":
        res, status, err, envelope = _planner_replay(dom, row)
    else:
        res, status, err = row.get("raw_result"), row.get("status", "completed"), row.get("error")
    if res != REFUSAL or status in ("crashed", "timeout"):
        return None
    err = str(err or "")
    if status == "completed":
        return "failed", "completed with no result"
    art = row.get("artifact") if isinstance(row.get("artifact"), dict) else {}
    if sysname == "network":
        if art.get("goal"):
            return "typed", err.split(":")[0]
        if err.startswith("declined:") or art.get("declined"):
            return "explicit", "EmissionDecline"
        if err.startswith("ParseError:"):                 # the schema line: typed or failed
            return gdc_parse_channel(err), "ParseError"
        return "failed", err.split(":")[0] or "no Goal"
    if sysname == "planner":
        if envelope:
            return "explicit", "refuse envelope"
        if err.startswith("plan parse-fail with decline text"):
            return "explicit", "unparseable, decline text"
        if err.startswith("step "):
            return "typed", err.split(": ")[1] if ": " in err else err
        return "failed", err.split(":")[0] or "no reply"
    if sysname in ("react", "code_agent"):
        text = art.get("last_text")
        return ("explicit", "statement, no answer") if (text or "").strip() else ("failed", "no answer, no text")
    if sysname == "text_to_sql":
        if err.startswith("no SQL emitted") and (art.get("raw_text") or "").strip():
            return "explicit", "statement, no SQL"
        if err.startswith("sql error"):
            return "failed", "SQL error (60 s wall clock)" if err.rstrip().endswith("interrupted") else "SQL error"
        return "failed", err.split(":")[0] or "no SQL, no text"
    return "failed", err.split(":")[0]
