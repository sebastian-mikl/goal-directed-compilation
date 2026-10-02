#!/usr/bin/env python
"""INDEPENDENT gold key for eval_corpus_employee.json — the employee-domain
second-domain benchmark, mirroring the discipline of the risk domain's gold builder (withheld
from this release).

HARD RULE: this script imports ONLY pandas + stdlib. ZERO imports from gdc/,
network/, contract, primitives, or any engine module — otherwise the eval is circular.
It reads eval_corpus_employee.json (the frozen corpus, 90 queries since D11 dropped
oor_03 on 2026-10-01: it has a defensible answering reading),
evaluation/domains/employee/employees.pkl, and evaluation/domains/employee/schema.json
(used ONLY to ground the logical->actual
column map).

Each answerable query has its OWN explicit pandas block implementing its `intent`
(NO generic interpreter — that would risk rebuilding the engine's logic and a single
shared bug). 81 answerable (T1-T5 + house_vocab) get a computed result; 9
out_of_range are recorded as {expected_behavior:"refuse", value:null}.

Outputs: evidence/gold/gold_employee.json (one normalized record per id) + evidence/
gold/gold_employee_digest.txt (id + question + computed gold, for an eyeball
hand-check) + a printed sanity summary. Discipline: FLAG anomalies, never guess or
edit the corpus.

    python evidence/gold/build_gold_employee.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "evaluation/domains/employee/eval_corpus_employee.json"
REGISTER = ROOT / "evaluation/domains/employee" / "employees.pkl"
SCHEMA = ROOT / "evaluation/domains/employee" / "schema.json"
OUT_DIR = Path(__file__).resolve().parent

# logical name (used in the corpus intents) -> actual dataframe column. Verified
# directly against evaluation/domains/employee/employees.pkl + schema.json before finalizing.
COL = {
    "employee_id": "Employee ID", "name": "Name", "department": "Department",
    "role": "Role", "status": "Status", "location": "Location",
    "salary": "Annual Salary", "bonus": "Bonus Target", "tenure": "Tenure (Years)",
    "manager_id": "Manager ID", "performance": "Performance Score",
    "market_rate": "Market Rate", "equity": "Equity Grant Value",
    "time_in_role": "Time in Current Role (Years)",
}
# Stable per-row key. 'Employee ID' is 150/150 unique ("E1000"...).
# Row-returning queries (filter/list rows; per-row top-k) are projected with this key
# as 'employee_id' so set/sequence comparisons in the eval comparator are unambiguous.
ID = "Employee ID"
SEPARATED_SET = ["Terminated"]         # business_rules.current.exclude, schema.json
FLAGS: list[str] = []


# ── shared helpers (copied verbatim from the risk domain's gold builder — generic
# pandas+stdlib, no risk-specific logic; verified by reading it in full before reuse) ──

def C(logical):
    return COL[logical]


def current(df):
    return df[~df[C("status")].isin(SEPARATED_SET)]


def _recs(df):
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            df[c] = df[c].astype(float).round(6)
    return [dict(sorted(r.items())) for r in df.to_dict("records")]


def SCALAR(v, is_count=False):
    val = int(v) if is_count else round(float(v), 6)
    return {"result_kind": "scalar", "ordered": False, "value": val, "row_count": 1}


def _attach_rid(slice_df, id_src):
    """Prepend stable per-row 'employee_id' (= Employee ID) to a row-returning slice.
    The slice's index must still be the source register's row index (most pandas
    operations preserve it; we never reset before this attach)."""
    out = slice_df.copy()
    out.insert(0, "employee_id", id_src.loc[out.index, ID].values)
    return out


def TABLE(df, sort_by, *, id_src=None):         # unordered -> deterministic sort
    if id_src is not None:
        df = _attach_rid(df, id_src)
    df = df.sort_values(list(sort_by), kind="mergesort").reset_index(drop=True)
    return {"result_kind": "table", "ordered": False, "value": _recs(df), "row_count": len(df)}


def OLIST(df, *, id_src=None):                  # ordered -> preserve order
    if id_src is not None:
        df = _attach_rid(df, id_src)
    df = df.reset_index(drop=True)
    return {"result_kind": "ordered_list", "ordered": True, "value": _recs(df), "row_count": len(df)}


def topk(df, by, k, qid, ascending=False):
    """Deterministic top-k by `by`; flags a boundary tie (kth vs (k+1)th equal).
    Does NOT reset the index — the source register's row index must SURVIVE so that
    OLIST/_attach_rid can stamp the TRUE per-row Employee ID."""
    s = df.sort_values(by, ascending=ascending, kind="mergesort")
    if len(s) > k:
        boundary = s.iloc[k - 1][by]
        nxt = s.iloc[k][by]
        if pd.notna(boundary) and boundary == nxt:
            FLAGS.append(f"{qid}: top-{k} boundary tie on {by!r} (value {boundary}); "
                         "deterministic by input order — architect may wish to confirm.")
    return s.head(k)


def grouped(df, by, valcol, agg, out_name):
    g = df.groupby(C(by))[valcol].agg(agg).reset_index().rename(columns={valcol: out_name})
    return g                                    # columns: [actual group col, out_name]


# ── per-query gold (explicit block each) ─────────────────────────────────────

def build(df):
    G = {}
    name = C("name")
    salary, bonus, tenure = C("salary"), C("bonus"), C("tenure")
    performance, market_rate, equity = C("performance"), C("market_rate"), C("equity")
    time_in_role = C("time_in_role")

    # T1 — single op
    G["t1_01"] = TABLE(current(df)[[name, C("status")]], [name, C("status")], id_src=df)
    G["t1_02"] = TABLE(df[df[C("department")] == "Engineering"][[name, C("department")]], [name], id_src=df)
    G["t1_03"] = TABLE(df[df[C("role")] == "Director"][[name, C("role")]], [name], id_src=df)
    G["t1_04"] = TABLE(df[df[C("status")] == "Sabbatical"][[name, C("status")]], [name], id_src=df)
    G["t1_05"] = TABLE(df[df[C("location")] == "SF"][[name, C("location")]], [name], id_src=df)
    G["t1_06"] = SCALAR(len(df), is_count=True)
    G["t1_07"] = SCALAR(df[salary].sum())
    G["t1_08"] = SCALAR(df[performance].mean())
    G["t1_09"] = SCALAR(df[salary].max())
    G["t1_10"] = TABLE(df[[name, C("manager_id")]], [name, C("manager_id")], id_src=df)
    G["t1_11"] = TABLE(df[[C("status"), C("role")]], [C("status"), C("role")], id_src=df)
    d = df[[name]].copy(); d["diff"] = df[salary] - df[bonus]
    G["t1_12"] = TABLE(d, [name, "diff"], id_src=df)
    d = df[[name]].copy(); d["total"] = df[market_rate] + df[equity]
    G["t1_13"] = TABLE(d, [name, "total"], id_src=df)
    G["t1_14"] = OLIST(topk(df, salary, 5, "t1_14")[[name, salary]], id_src=df)
    G["t1_15"] = OLIST(topk(df, performance, 10, "t1_15")[[name, performance]], id_src=df)

    # T2 — two ops
    G["t2_01"] = SCALAR(len(current(df)), is_count=True)
    G["t2_02"] = SCALAR(len(df[df[C("department")] == "Engineering"]), is_count=True)
    G["t2_03"] = SCALAR(df[df[C("role")] == "Director"][performance].mean())
    G["t2_04"] = TABLE(grouped(df, "department", salary, "mean", "avg_salary"), [C("department")])
    G["t2_05"] = TABLE(df.groupby(C("role")).size().reset_index(name="count"), [C("role")])
    G["t2_06"] = TABLE(grouped(df, "role", equity, "sum", "sum_equity"), [C("role")])
    c = current(df); d = c[[name]].copy(); d["diff"] = c[salary] - c[market_rate]
    G["t2_07"] = TABLE(d, [name, "diff"], id_src=df)
    t = df[df[C("department")] == "Sales"]; d = t[[name]].copy(); d["total"] = t[bonus] + t[equity]
    G["t2_08"] = TABLE(d, [name, "total"], id_src=df)
    G["t2_09"] = SCALAR((df[salary] - df[market_rate]).sum())
    G["t2_10"] = SCALAR((df[bonus] + df[equity]).mean())
    G["t2_11"] = OLIST(topk(current(df), salary, 5, "t2_11")[[name, salary]], id_src=df)
    G["t2_12"] = OLIST(topk(df[df[C("department")] == "Engineering"], performance, 3, "t2_12")[[name, performance]], id_src=df)
    # t2_13/t2_14 intents are `select [status, department]` / `select [status, role]`
    # — Name is NOT requested. Earlier gold erroneously projected Name (an unrequested
    # column), causing ALL FIVE systems to score silent_wrong for the same reason.
    # Corrected here to the intent's projection (employee_id stays as the row key via
    # id_src); mirrors the t1_11 pattern df[[status, role]].
    m = current(df); m = m[m[C("role")] == "Manager"]
    G["t2_13"] = TABLE(m[[C("status"), C("department")]], [C("status"), C("department")], id_src=df)
    m = current(df); m = m[m[C("department")] == "Sales"]
    G["t2_14"] = TABLE(m[[C("status"), C("role")]], [C("status"), C("role")], id_src=df)
    G["t2_15"] = SCALAR(current(df)[salary].max())

    # T3 — three ops
    c = current(df)
    G["t3_01"] = TABLE(grouped(c, "department", salary, "mean", "avg_salary"), [C("department")])
    G["t3_02"] = TABLE(grouped(c, "role", equity, "sum", "sum_equity"), [C("role")])
    G["t3_03"] = OLIST(topk(grouped(df, "department", salary, "sum", "sum_salary"), "sum_salary", 3, "t3_03"))
    G["t3_04"] = OLIST(topk(df.groupby(C("manager_id")).size().reset_index(name="count"), "count", 5, "t3_04"))
    c = current(df); c = c[c[C("department")] == "Engineering"]
    G["t3_05"] = SCALAR(c[performance].mean())
    c = current(df); c = c[c[C("role")] == "Director"]
    G["t3_06"] = SCALAR(c[salary].sum())
    d = df.assign(sv=df[salary] - df[market_rate])
    G["t3_07"] = OLIST(grouped(d, "role", "sv", "sum", "sum_salary_variance").sort_values("sum_salary_variance", ascending=False).reset_index(drop=True))
    d = df.assign(tvc=df[bonus] + df[equity])
    G["t3_08"] = OLIST(grouped(d, "department", "tvc", "mean", "avg_total_variable_comp").sort_values("avg_total_variable_comp", ascending=False).reset_index(drop=True))
    c = current(df); d = c.assign(salary_variance=c[salary] - c[market_rate])
    G["t3_09"] = OLIST(topk(d, "salary_variance", 5, "t3_09")[[name, "salary_variance"]], id_src=df)
    c = current(df); d = c.assign(total_variable_comp=c[bonus] + c[equity])
    G["t3_10"] = OLIST(topk(d, "total_variable_comp", 3, "t3_10")[[name, "total_variable_comp"]], id_src=df)
    t = df[df[C("department")] == "Engineering"]
    G["t3_11"] = OLIST(topk(grouped(t, "role", equity, "sum", "sum_equity"), "sum_equity", 3, "t3_11"))
    c = current(df)
    G["t3_12"] = OLIST(topk(grouped(c, "department", salary, "mean", "avg_salary"), "avg_salary", 3, "t3_12"))
    t = df[df[C("role")] == "Manager"]
    G["t3_13"] = OLIST(topk(t, performance, 5, "t3_13")[[name, performance]], id_src=df)
    t = df[df[C("status")] == "Sabbatical"]
    G["t3_14"] = OLIST(grouped(t, "department", salary, "sum", "sum_salary").sort_values("sum_salary", ascending=False).reset_index(drop=True))
    c = current(df)
    G["t3_15"] = TABLE(grouped(c, "role", equity, "max", "max_equity"), [C("role")])

    # T4 — business-rule multi-op ('active'/'current staff'/'still with the company' -> current())
    c = current(df)
    G["t4_01"] = TABLE(grouped(c, "department", salary, "mean", "avg_salary"), [C("department")])
    G["t4_02"] = TABLE(grouped(c, "role", equity, "sum", "sum_equity"), [C("role")])
    G["t4_03"] = TABLE(grouped(c, "department", performance, "mean", "avg_performance"), [C("department")])
    G["t4_04"] = TABLE(grouped(c, "role", salary, "sum", "sum_salary"), [C("role")])
    c = current(df); c = c[c[salary] > 150000]; d = c.assign(sv=c[salary] - c[market_rate])
    G["t4_05"] = TABLE(grouped(d, "role", "sv", "mean", "avg_salary_variance"), [C("role")])
    c = current(df); c = c[c[performance] > 4.0]
    G["t4_06"] = TABLE(grouped(c, "department", equity, "sum", "sum_equity"), [C("department")])
    c = current(df); c = c[c[C("department")].isin(["Engineering", "Sales"])]
    G["t4_07"] = SCALAR((c[bonus] + c[equity]).mean())
    c = current(df); c = c[c[C("role")] == "Director"]
    G["t4_08"] = SCALAR(c[market_rate].mean())
    c = current(df); c = c[c[C("status")] != "Sabbatical"]
    G["t4_09"] = TABLE(grouped(c, "department", salary, "sum", "sum_salary"), [C("department")])
    c = current(df)
    G["t4_10"] = OLIST(topk(grouped(c, "department", performance, "mean", "avg_performance"), "avg_performance", 3, "t4_10"))
    c = current(df); d = c.assign(sv=c[salary] - c[market_rate])
    G["t4_11"] = OLIST(topk(grouped(d, "role", "sv", "sum", "sum_salary_variance"), "sum_salary_variance", 3, "t4_11"))
    c = current(df); c = c[c[tenure] > 5]
    G["t4_12"] = TABLE(grouped(c, "role", equity, "mean", "avg_equity"), [C("role")])
    c = current(df)
    G["t4_13"] = TABLE(grouped(c, "location", salary, "mean", "avg_salary"), [C("location")])
    G["t4_14"] = TABLE(grouped(c, "department", time_in_role, "mean", "avg_time_in_role"), [C("department")])
    c = current(df); c = c[c[C("role")].isin(["VP", "Director"])]
    G["t4_15"] = TABLE(grouped(c, "location", equity, "sum", "sum_equity"), [C("location")])

    # house_vocab (loose phrasing; hv_05/hv_06 of the original 8-item draft DROPPED --
    # confirmed genuinely undefined against real data, see FLAGS / gold_employee_digest.txt)
    G["hv_01"] = TABLE(current(df)[[name, C("status")]], [name, C("status")], id_src=df)
    t = df[df[C("department")] == "Engineering"]
    G["hv_02"] = SCALAR(t[salary].mean())
    G["hv_03"] = OLIST(topk(df, salary, 5, "hv_03")[[name, salary]], id_src=df)
    G["hv_04"] = SCALAR(len(df[df[C("department")] == "Sales"]), is_count=True)
    G["hv_05"] = TABLE(df.groupby(C("department")).size().reset_index(name="count"), [C("department")])
    G["hv_06"] = SCALAR(current(df)[tenure].max())

    # T5 — high-complexity in-range (complexity_ops 5-8). Populations and thresholds
    # were verified against the real augmented employee register before
    # being finalized, not guessed.
    def _grp_topk(d, by, valcol, agg, k, *, asc=False):
        s = d.groupby(C(by))[valcol].agg(agg).sort_values(ascending=asc).head(k).reset_index()
        return s                                            # columns: [actual_group_col, valcol]

    # 5-op
    c = current(df); d = c.assign(salary_variance=c[salary] - c[market_rate]); d = d[d["salary_variance"] > 0]
    G["t5_01"] = OLIST(_grp_topk(d, "department", "salary_variance", "sum", 3))
    c = current(df); c = c[c[C("role")] == "Manager"]
    d = c.assign(total_variable_comp=c[bonus] + c[equity])
    G["t5_02"] = OLIST(_grp_topk(d, "department", "total_variable_comp", "mean", 5))
    c = current(df); c = c[c[salary] > 150000]
    d = c.assign(salary_variance=c[salary] - c[market_rate])
    G["t5_03"] = OLIST(_grp_topk(d, "role", "salary_variance", "mean", 3))
    c = current(df); c = c[c[performance] > 4.0]
    d = c.assign(total_variable_comp=c[bonus] + c[equity])
    G["t5_04"] = OLIST(_grp_topk(d, "department", "total_variable_comp", "sum", 3))

    # 6-op
    c = current(df); c = c[(c[performance] > 4.0) & (c[C("department")] == "Engineering")]
    d = c.assign(salary_variance=c[salary] - c[market_rate])
    G["t5_05"] = OLIST(_grp_topk(d, "role", "salary_variance", "sum", 3))
    c = current(df)
    d = c.assign(salary_variance=c[salary] - c[market_rate], total_variable_comp=c[bonus] + c[equity])
    d = d[d["salary_variance"] < 0]
    G["t5_06"] = OLIST(_grp_topk(d, "role", "total_variable_comp", "mean", 3))
    c = current(df); c = c[(c[C("department")] == "Sales") & (c[C("role")] == "Manager")]
    d = c.assign(salary_variance=c[salary] - c[market_rate])
    G["t5_07"] = OLIST(_grp_topk(d, "location", "salary_variance", "sum", 5))
    c = current(df); d = c.assign(total_comp=c[salary] + c[bonus] + c[equity]); d = d[d["total_comp"] > 230000]
    G["t5_08"] = OLIST(_grp_topk(d, "department", "total_comp", "mean", 3))

    # 7-op
    c = current(df); c = c[c[C("role")] == "Manager"]
    d = c.assign(salary_variance=c[salary] - c[market_rate], total_variable_comp=c[bonus] + c[equity])
    d = d[d["total_variable_comp"] > 64056]
    G["t5_09"] = OLIST(_grp_topk(d, "department", "salary_variance", "mean", 3))
    c = current(df)
    c = c[(c[performance] > 4.0) & (c[C("department")] == "Engineering") & (c[salary] > 120000)]
    d = c.assign(salary_variance=c[salary] - c[market_rate])
    G["t5_10"] = OLIST(_grp_topk(d, "role", "salary_variance", "mean", 3))
    c = current(df); d = c.assign(salary_variance=c[salary] - c[market_rate]); d = d[d["salary_variance"] > 0]
    d = d.assign(total_variable_comp=d[bonus] + d[equity]); d = d[d["total_variable_comp"] > 30000]
    G["t5_11"] = OLIST(_grp_topk(d, "role", "salary_variance", "mean", 3))
    c = current(df); d = c.assign(tenure_ratio=c[time_in_role] / c[tenure]); d = d[d["tenure_ratio"] < 0.5]
    G["t5_12"] = OLIST(_grp_topk(d, "department", "tenure_ratio", "mean", 3))

    # 8-op
    c = current(df); d = c.assign(salary_variance=c[salary] - c[market_rate]); d = d[d["salary_variance"] > 0]
    d = d.assign(total_comp=d[salary] + d[bonus] + d[equity]); d = d[d["total_comp"] < 510000]
    G["t5_13"] = OLIST(_grp_topk(d, "role", "total_comp", "mean", 3))
    c = current(df); c = c[c[C("department")].isin(["Sales", "Marketing"])]
    d = c.assign(salary_variance=c[salary] - c[market_rate]); d = d[d["salary_variance"] > 0]
    d = d.assign(total_variable_comp=d[bonus] + d[equity]); d = d[d["total_variable_comp"] > 45000]
    G["t5_14"] = OLIST(_grp_topk(d, "role", "salary_variance", "mean", 3))
    c = current(df); c = c[c[salary] > 150000]
    d = c.assign(salary_variance=c[salary] - c[market_rate]); d = d[d["salary_variance"] > 0]
    d = d.assign(total_variable_comp=d[bonus] + d[equity]); d = d[d["total_variable_comp"] < 205077]
    G["t5_15"] = OLIST(_grp_topk(d, "role", "total_variable_comp", "mean", 3))

    return G


# ── driver: assemble, sanity-check, write ────────────────────────────────────

def main():
    schema = json.loads(SCHEMA.read_text())                 # used only to ground the column map
    schema_cols = set(schema.get("columns", {}))
    for lg, ac in COL.items():
        if ac not in schema_cols:
            FLAGS.append(f"column map: {lg}->{ac!r} not found in schema.json columns")

    corpus = json.loads(CORPUS.read_text())
    queries = corpus["queries"]
    df = pd.read_pickle(REGISTER)

    computed = build(df)
    gold = {}
    for q in queries:
        qid, tier = q["id"], q["tier"]
        cops = q.get("complexity_ops")
        if tier == "out_of_range":
            gold[qid] = {"id": qid, "tier": tier, "result_kind": None, "ordered": False,
                         "value": None, "expected_behavior": "refuse", "row_count": 0,
                         "complexity_ops": cops}
        elif qid in computed:
            r = computed[qid]
            gold[qid] = {"id": qid, "tier": tier, "result_kind": r["result_kind"],
                         "ordered": r["ordered"], "value": r["value"],
                         "expected_behavior": "answer", "row_count": r["row_count"],
                         "complexity_ops": cops}
        else:
            FLAGS.append(f"{qid}: NO gold block authored (intent: {q['intent'][:60]})")

    # ── sanity checks ──
    sane = []
    answers = [g for g in gold.values() if g["expected_behavior"] == "answer"]
    refusals = [g for g in gold.values() if g["expected_behavior"] == "refuse"]
    n_total = len(df)

    def chk(name, ok, detail=""):
        sane.append((name, ok, detail))
        if not ok:
            FLAGS.append(f"SANITY {name}: {detail}")

    chk("ids_present", len(gold) == 90, f"{len(gold)}/90")        # D11 (2026-10-01): oor_03 dropped
    chk("81_answer", len(answers) == 81, f"{len(answers)} answers")
    chk("9_refuse", len(refusals) == 9, f"{len(refusals)} refusals")  # was 10; -oor_03 (D11)
    for qid in [f"t5_{i:02d}" for i in range(1, 16)]:
        k = 5 if qid in ("t5_02", "t5_07") else 3
        chk(f"{qid}_topk", gold[qid]["row_count"] <= k, f"{gold[qid]['row_count']} <= {k}")
        chk(f"{qid}_nonempty", gold[qid]["row_count"] > 0, f"row_count={gold[qid]['row_count']}")
    cnt_ok = all(isinstance(g["value"], int) and g["value"] >= 0
                 for g in answers if g["result_kind"] == "scalar" and isinstance(g["value"], int))
    chk("counts_nonneg_int", cnt_ok)
    chk("current_le_total", gold["t2_01"]["value"] <= n_total, f"{gold['t2_01']['value']} <= {n_total}")
    chk("t1_06_eq_total", gold["t1_06"]["value"] == n_total, f"{gold['t1_06']['value']} vs {n_total}")
    chk("total_le_150", n_total == 150, f"register has {n_total} rows, expected 150")
    KS = {"t1_14": 5, "t1_15": 10, "t2_11": 5, "t2_12": 3, "t3_03": 3, "t3_04": 5, "t3_09": 5,
          "t3_10": 3, "t3_11": 3, "t3_12": 3, "t3_13": 5, "t4_10": 3, "t4_11": 3,
          "hv_03": 5}
    topk_ok = all(gold[q]["row_count"] <= k for q, k in KS.items())
    chk("topk_le_k", topk_ok, str({q: gold[q]["row_count"] for q, k in KS.items() if gold[q]["row_count"] > k}))

    def ngroups(filtered, by):
        return filtered[C(by)].nunique()
    grp_checks = {
        "t2_04": ngroups(df, "department"), "t2_05": ngroups(df, "role"),
        "t2_06": ngroups(df, "role"), "t3_01": ngroups(current(df), "department"),
        "t3_15": ngroups(current(df), "role"), "hv_05": ngroups(df, "department"),
    }
    grp_ok = all(gold[q]["row_count"] == n for q, n in grp_checks.items())
    chk("grouped_ngroups", grp_ok, str({q: (gold[q]["row_count"], n) for q, n in grp_checks.items() if gold[q]["row_count"] != n}))

    def has_nan(v):
        if v is None: return True
        if isinstance(v, float): return v != v
        if isinstance(v, list):
            return any(has_nan(x) for rec in v for x in (rec.values() if isinstance(rec, dict) else [rec]))
        return False
    nan_ok = not any(has_nan(g["value"]) for g in answers)
    chk("no_nan_in_answers", nan_ok)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "gold_employee.json").write_text(json.dumps(gold, indent=2, default=str))

    # ── digest (eyeball hand-check) ──
    qmap = {q["id"]: q for q in queries}
    lines = ["GOLD DIGEST — independent answer key for eval_corpus_employee.json",
             "(computed by evidence/gold/build_gold_employee.py — pandas + stdlib only, zero engine imports)", ""]
    lines += ["AUTHORING NOTES (conventions applied):",
              "  - 'active' / 'current staff' / 'still with the company' / 'staff currently employed'",
              "    all map to the current() business rule: Status not in ['Terminated'].",
              "  - two originally-drafted house_vocab queries ('typical bonus for managers', 'way more",
              "    than market rate') were DROPPED before this script was written -- confirmed genuinely",
              "    undefined against real data (see report). house_vocab is 6 queries, renumbered",
              "    hv_01..hv_06 sequentially (fresh corpus, no prior frozen IDs to preserve gaps for).",
              "  - row-returning queries (filter/list rows; per-row top-k) carry 'employee_id'",
              "    (= Employee ID, 150/150 unique) as the stable per-row key.",
              "  - unordered tables sorted by all columns (grouped tables by the group key) for",
              "    determinism; ordered_list preserves the order/limit; scalars rounded to 6 dp,",
              "    counts kept as ints; derived/aggregate columns given descriptive names.", ""]
    for qid in [q["id"] for q in queries]:
        g, q = gold[qid], qmap[qid]
        lines.append(f"[{qid}] ({g['tier']})  {q['question']}")
        if g["expected_behavior"] == "refuse":
            lines.append(f"    GOLD: REFUSE — {q.get('reason','out of range')}")
        else:
            v = g["value"]
            vs = (json.dumps(v) if g["result_kind"] == "scalar"
                  else json.dumps(v[:6]) + (f" … (+{len(v)-6} more)" if len(v) > 6 else ""))
            lines.append(f"    GOLD ({g['result_kind']}, ordered={g['ordered']}, rows={g['row_count']}): {vs}")
        lines.append("")
    if FLAGS:
        lines += ["", "================ FLAGS (architect to resolve; NOT auto-fixed) ================"] + [f"  - {f}" for f in FLAGS]
    else:
        lines += ["", "FLAGS: none"]
    (OUT_DIR / "gold_employee_digest.txt").write_text("\n".join(lines))

    # ── printed summary ──
    print("=== gold_employee sanity summary ===")
    for name, ok, detail in sane:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} {('('+detail+')') if detail else ''}")
    print(f"\n  ids={len(gold)}  answers={len(answers)}  refusals={len(refusals)}")
    print(f"  FLAGS: {len(FLAGS)}")
    for f in FLAGS:
        print(f"    - {f}")
    print(f"\n  -> {OUT_DIR}/gold_employee.json, gold_employee_digest.txt")


if __name__ == "__main__":
    main()
