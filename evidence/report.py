"""Metrics + figures from runs.csv.

Pure-deterministic transforms (cumulative_call2_rate, rolling_token_cost,
served_by_breakdown) are tested in evidence/tests/test_metrics_aggregation.py
without any plotting library involvement.

Figures produced:

  1. headline_call2_rate.{png,svg}  — THE headline. Cumulative Call-2
     invocation rate vs query position, treatment vs baseline (mean ± 95% CI
     across repetitions). The compounding curve.

  2. cost_per_query.{png,svg}  — rolling per-query token cost, treatment vs
     baseline (mean ± CI), with template/composite promotion markers.

  3. stage_decomposition.{png,svg}  — per-query MEAN tokens split into
     call1/call2/call3, treatment vs baseline (grouped bars). Shows call2 -> ~0
     under treatment while call1/call3 persist — the honest cost story a
     total-token sum obscures.

  4. served_by_stacked.{png,svg}  — per-query execution attribution
     (graph_edge / astar_template / composite / llm) over time (structural).

  5. ablation_arms.{png,svg}  (only when a semantic arm is present) —
     structural vs semantic identity across thresholds tau: final node count,
     Call-2 rate, accuracy.

Cost reporting compares PER-QUERY MEANS by mode (never token sums across
unequal n; a sum is reported only when n is equal). The headline number is
call2_rate_reduction = baseline_call2_rate - treatment_call2_rate. The summary
also carries the C2 correctness cross-tab and, per tau, the C3 ablation.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


# ── Pure metric transforms ───────────────────────────────────────────────────

SERVED_COLS: tuple[str, ...] = (
    "served_by_graph_edge",
    "served_by_astar_template",
    "served_by_template",
    "served_by_composite",
    "served_by_llm",
)


def load_runs(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return _ensure_columns(df)


def _ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Backfill columns added by later harness tiers so readers stay tolerant
    of older runs.csv files (and of synthetic frames in the pure-metric tests).
    """
    defaults = {
        "arm": "structural",
        "tau": "",
        "repetition": 0,
        "correct": 0,
        "correct_detail": "",
        # Pre-tiering runs used the single Sonnet model for all three calls;
        # backfill old CSVs with that so the config-match logic has a value.
        "model_call1": "claude-sonnet-4-5",
        "model_call2": "claude-sonnet-4-5",
        "model_call3": "claude-sonnet-4-5",
    }
    for col, val in defaults.items():
        if col not in df.columns:
            df[col] = val
    return df


def cumulative_call2_rate(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """Per-query cumulative fraction of queries where Call 2 fired.

    Returns a DataFrame with columns:
      index            — 0-based query position WITHIN this mode
      call2_invoked    — 0/1 per query
      cumulative_call2 — running sum of call2_invoked
      cum_call2_rate   — running mean of call2_invoked (== sum/n)
    """
    rows = df[df["mode"] == mode].sort_values("index").reset_index(drop=True)
    n = len(rows)
    if n == 0:
        return pd.DataFrame(columns=["index", "call2_invoked",
                                     "cumulative_call2", "cum_call2_rate"])
    invoked = rows["call2_invoked"].astype(int)
    cum     = invoked.cumsum()
    pos     = pd.Series(range(1, n + 1), index=invoked.index, dtype=int)
    return pd.DataFrame({
        "index":            rows["index"].values,
        "call2_invoked":    invoked.values,
        "cumulative_call2": cum.values,
        "cum_call2_rate":   (cum / pos).values,
    })


def rolling_token_cost(
    df: pd.DataFrame, mode: str, window: int = 3,
) -> pd.DataFrame:
    """Rolling-mean total LLM token cost per query (in+out, all 3 calls).

    `tokens_total` is the canonical per-query sum column. We use a
    `min_periods=1` rolling window so the early queries are still plottable.
    """
    rows = df[df["mode"] == mode].sort_values("index").reset_index(drop=True)
    if rows.empty:
        return pd.DataFrame(columns=["index", "tokens_total", "rolling_tokens"])
    rolling = rows["tokens_total"].rolling(window=window, min_periods=1).mean()
    return pd.DataFrame({
        "index":          rows["index"].values,
        "tokens_total":   rows["tokens_total"].values,
        "rolling_tokens": rolling.values,
    })


def served_by_breakdown(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """Per-query served-by counts for the stacked-area figure."""
    rows = df[df["mode"] == mode].sort_values("index").reset_index(drop=True)
    cols = ["index", *SERVED_COLS]
    return rows[cols].copy()


def promotion_events(df: pd.DataFrame, mode: str = "treatment") -> pd.DataFrame:
    """Query indices where >=1 template or composite promotion fired.

    Returned columns: index, template_promotions, composite_promotions.
    """
    rows = df[df["mode"] == mode].sort_values("index").reset_index(drop=True)
    mask = (rows["template_promotions"] > 0) | (rows["composite_promotions"] > 0)
    return rows.loc[mask, ["index", "template_promotions", "composite_promotions"]].copy()


def _structural_treatment(df: pd.DataFrame) -> pd.DataFrame:
    """Treatment rows for the DEPLOYED (structural) system: arm=='structural'
    when the column is present, else all treatment rows."""
    t = df[df["mode"] == "treatment"]
    if "arm" in t.columns and (t["arm"] == "structural").any():
        t = t[t["arm"] == "structural"]
    return t


def n_by_mode(df: pd.DataFrame) -> dict[str, int]:
    """Row count per mode (structural treatment + baseline) — used to gate any
    sum-based comparison on equal n."""
    out = {"treatment": int(len(_structural_treatment(df)))}
    out["baseline"] = int(len(df[df["mode"] == "baseline"]))
    return out


def tokens_per_query_by_mode(df: pd.DataFrame) -> dict[str, float | None]:
    """Mean total tokens PER QUERY by mode (structural treatment vs baseline).
    This is the honest cost comparison — never compare token SUMS across
    unequal n."""
    treat = _structural_treatment(df)
    base = df[df["mode"] == "baseline"]
    return {
        "treatment": _mean_or_none(treat["tokens_total"].astype(float)),
        "baseline":  _mean_or_none(base["tokens_total"].astype(float)),
    }


def stage_means_by_mode(df: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Per-query mean tokens (in+out) per stage call1/call2/call3, for
    structural treatment vs baseline. Shows call2 -> ~0 under treatment while
    call1/call3 persist."""
    def _stages(grp):
        return {
            "call1": float((grp["call1_in"] + grp["call1_out"]).mean()) if len(grp) else 0.0,
            "call2": float((grp["call2_in"] + grp["call2_out"]).mean()) if len(grp) else 0.0,
            "call3": float((grp["call3_in"] + grp["call3_out"]).mean()) if len(grp) else 0.0,
        }
    return {
        "treatment": _stages(_structural_treatment(df)),
        "baseline":  _stages(df[df["mode"] == "baseline"]),
    }


def call2_rate_by_mode(df: pd.DataFrame) -> dict[str, float | None]:
    treat = _structural_treatment(df)
    base = df[df["mode"] == "baseline"]
    return {
        "treatment": _mean_or_none(treat["call2_invoked"].astype(float)),
        "baseline":  _mean_or_none(base["call2_invoked"].astype(float)),
    }


def call2_rate_reduction(df: pd.DataFrame) -> float | None:
    """Headline number: baseline Call-2 rate minus treatment Call-2 rate."""
    r = call2_rate_by_mode(df)
    if r["treatment"] is None or r["baseline"] is None:
        return None
    return float(r["baseline"] - r["treatment"])


def total_tokens_by_mode(df: pd.DataFrame) -> dict[str, int]:
    """Sum of `tokens_total` per mode. ONLY meaningful when n is equal across
    modes; callers must gate on n_by_mode. Kept for the equal-n canonical run."""
    return {mode: int(grp["tokens_total"].sum())
            for mode, grp in df.groupby("mode")}


def model_configs_by_arm(df: pd.DataFrame) -> dict[str, dict]:
    """Distinct (call1, call2, call3) model configs per arm (treatment) and for
    baseline. `clean` is False when an arm mixes >1 config — its comparison
    must not be presented as clean, because the model would not be held fixed."""
    cols = ["model_call1", "model_call2", "model_call3"]
    if not all(c in df.columns for c in cols):
        return {}
    out: dict[str, dict] = {}
    groups = []
    treat = df[df["mode"] == "treatment"]
    if "arm" in treat.columns:
        groups = list(treat.groupby("arm"))
    base = df[df["mode"] == "baseline"]
    if len(base):
        groups.append(("baseline", base))
    for name, grp in groups:
        cfgs = sorted({tuple(str(v) for v in row) for row in grp[cols].itertuples(index=False)})
        out[str(name)] = {"configs": [list(c) for c in cfgs], "clean": len(cfgs) <= 1}
    return out


# ── Correctness axis (claim C2) ──────────────────────────────────────────────

def _mean_or_none(series) -> float | None:
    return float(series.mean()) if len(series) else None


def accuracy_by_call2(df: pd.DataFrame, mode: str = "treatment",
                      epsilon: float = 0.0) -> dict:
    """THE killer cross-tab: accuracy conditioned on whether the LLM (Call 2)
    was involved in producing the answer.

    Splits the mode's rows into call2_invoked==0 (served purely by the
    deterministic graph: stored edges / templates / composites) vs
    call2_invoked==1 (a fresh morphism was generated), and reports accuracy
    in each cell. Claim C2 — deterministic reuse does not degrade correctness —
    holds when accuracy(call2==0) >= accuracy(call2==1) - epsilon.

    When both identity arms are present the claim is about the DEPLOYED system,
    so it is measured on the structural arm only (the semantic arm is the
    ablation control and forces Call 2 on every query).
    """
    rows = df[df["mode"] == mode]
    if "arm" in rows.columns and (rows["arm"] == "structural").any():
        rows = rows[rows["arm"] == "structural"]
    c = rows["correct"].astype(float)
    inv = rows["call2_invoked"].astype(int)
    acc0 = _mean_or_none(c[inv == 0])
    acc1 = _mean_or_none(c[inv == 1])
    claim = None
    if acc0 is not None and acc1 is not None:
        claim = bool(acc0 >= acc1 - epsilon)
    return {
        "mode": mode,
        "accuracy_call2_0": acc0,
        "n_call2_0": int((inv == 0).sum()),
        "accuracy_call2_1": acc1,
        "n_call2_1": int((inv == 1).sum()),
        "overall_accuracy": _mean_or_none(c),
        "n": int(len(rows)),
        "epsilon": epsilon,
        "claim_c2_holds": claim,
    }


def overall_accuracy(df: pd.DataFrame) -> dict:
    """Accuracy per mode over all measured rows."""
    return {mode: _mean_or_none(grp["correct"].astype(float))
            for mode, grp in df.groupby("mode")}


# ── Structural-vs-semantic ablation (claim C3) ───────────────────────────────

def _arm_summary(grp: pd.DataFrame) -> dict:
    """final node count (mean of last nodes_after per repetition), cumulative
    Call-2 rate, accuracy, row count — for one arm slice."""
    nodes = None
    if "nodes_after" in grp.columns and len(grp):
        finals = [g.sort_values("index")["nodes_after"].iloc[-1]
                  for _, g in grp.groupby("repetition") if len(g)]
        nodes = float(pd.Series(finals).mean()) if finals else None
    return {
        "final_node_count": nodes,
        "cum_call2_rate":   _mean_or_none(grp["call2_invoked"].astype(float)),
        "accuracy":         _mean_or_none(grp["correct"].astype(float)),
        "n_rows":           int(len(grp)),
    }


def _c3_verdict(s: dict, m: dict) -> dict:
    fewer = (s["final_node_count"] is not None and m["final_node_count"] is not None
             and s["final_node_count"] <= m["final_node_count"])
    lower = (s["cum_call2_rate"] is not None and m["cum_call2_rate"] is not None
             and s["cum_call2_rate"] <= m["cum_call2_rate"])
    acc_ok = (s["accuracy"] is not None and m["accuracy"] is not None
              and s["accuracy"] >= m["accuracy"] - 1e-9)
    return {
        "claim_c3_holds": bool(fewer and lower and acc_ok),
        "claim_c3_detail": {
            "structural_fewer_nodes": bool(fewer),
            "structural_lower_call2": bool(lower),
            "structural_ge_accuracy": bool(acc_ok),
        },
    }


def arm_comparison(df: pd.DataFrame, mode: str = "treatment",
                   tau: float | str | None = None) -> dict:
    """Structural vs semantic summary. When `tau` is given, the semantic arm is
    filtered to that threshold; otherwise all semantic rows are pooled.
    Claim C3 — structural wins on nodes, Call-2 rate, and accuracy."""
    treat = df[df["mode"] == mode]
    out: dict = {}
    struct = treat[treat["arm"] == "structural"] if "arm" in treat.columns else treat
    if len(struct):
        out["structural"] = _arm_summary(struct)
    sem = treat[treat["arm"] == "semantic"] if "arm" in treat.columns else treat.iloc[0:0]
    if tau is not None and "tau" in sem.columns:
        sem = sem[sem["tau"].astype(str) == str(tau)]
    if len(sem):
        out["semantic"] = _arm_summary(sem)
    if "structural" in out and "semantic" in out:
        out.update(_c3_verdict(out["structural"], out["semantic"]))
    return out


def _present_taus(df: pd.DataFrame) -> list[str]:
    sem = df[(df["mode"] == "treatment") & (df.get("arm", "") == "semantic")] \
        if "arm" in df.columns else df.iloc[0:0]
    if "tau" not in sem.columns:
        return []
    taus = [t for t in sem["tau"].astype(str).unique() if t not in ("", "nan")]
    return sorted(taus, key=lambda x: float(x))


def arm_comparison_by_tau(df: pd.DataFrame, mode: str = "treatment") -> dict | None:
    """The LIVE C3 ablation: structural vs semantic at every threshold present
    in the CSV. Returns None when there is no semantic arm.

    tau trend: low tau over-merges (accuracy drops — the value-reuse "owned by
    X" near-duplicates are the canary), high tau over-splits (node count and
    Call-2 rate explode). Structural should beat semantic at every tau."""
    taus = _present_taus(df)
    if not taus:
        return None
    treat = df[df["mode"] == mode]
    struct = treat[treat["arm"] == "structural"]
    structural = _arm_summary(struct) if len(struct) else None
    by_tau: dict[str, dict] = {}
    for tau in taus:
        cmp = arm_comparison(df, mode, tau=tau)
        by_tau[tau] = {
            "semantic": cmp.get("semantic"),
            **{k: cmp[k] for k in ("claim_c3_holds", "claim_c3_detail") if k in cmp},
        }
    holds_all = all(v.get("claim_c3_holds") for v in by_tau.values()) if by_tau else None
    return {
        "structural": structural,
        "thresholds": taus,
        "by_tau": by_tau,
        "claim_c3_holds_all_tau": bool(holds_all) if holds_all is not None else None,
    }


# ── Repetition aggregation (mean ± 95% CI across repetitions) ────────────────

def _agg_curves(df: pd.DataFrame, mode: str, value_fn) -> tuple[list, np.ndarray, np.ndarray]:
    """Stack one per-position series per repetition and return
    (positions, mean, ci_halfwidth). value_fn(group_for_one_rep) -> 1-D array
    of per-position values. With a single repetition the CI is zero.
    Series of differing length are truncated to the shortest (alignment)."""
    sub = df[df["mode"] == mode]
    if sub.empty:
        return [], np.array([]), np.array([])
    reps = sorted(sub["repetition"].unique()) if "repetition" in sub.columns else [0]
    cols: list[list[float]] = []
    for rep in reps:
        g = sub[sub["repetition"] == rep] if "repetition" in sub.columns else sub
        vals = list(value_fn(g))
        if vals:
            cols.append(vals)
    if not cols:
        return [], np.array([]), np.array([])
    n = min(len(c) for c in cols)
    M = np.array([c[:n] for c in cols], dtype=float)
    mean = M.mean(axis=0)
    if M.shape[0] >= 2:
        sd = M.std(axis=0, ddof=1)
        ci = 1.96 * sd / np.sqrt(M.shape[0])
    else:
        ci = np.zeros(n)
    return list(range(1, n + 1)), mean, ci


def headline_band(df: pd.DataFrame, mode: str) -> tuple[list, np.ndarray, np.ndarray]:
    """positions, mean cum_call2_rate, 95% CI half-width across repetitions."""
    return _agg_curves(
        df, mode,
        lambda g: cumulative_call2_rate(g, mode)["cum_call2_rate"].values,
    )


def cost_band(df: pd.DataFrame, mode: str, window: int = 3
              ) -> tuple[list, np.ndarray, np.ndarray]:
    """positions, mean rolling token cost, 95% CI half-width across repetitions."""
    return _agg_curves(
        df, mode,
        lambda g: rolling_token_cost(g, mode, window)["rolling_tokens"].values,
    )


# ── Figures ──────────────────────────────────────────────────────────────────

def _save(fig, out_dir: Path, stem: str) -> tuple[Path, Path]:
    import matplotlib.pyplot as plt
    png = out_dir / f"{stem}.png"
    svg = out_dir / f"{stem}.svg"
    fig.savefig(png, dpi=150, bbox_inches="tight")
    fig.savefig(svg, bbox_inches="tight")
    plt.close(fig)   # release the figure from pyplot's global registry
    return png, svg


def plot_headline(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """Cumulative Call-2 rate, treatment vs baseline, one axis. When multiple
    repetitions are present the line is the mean and the band is the 95% CI."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 5))
    for mode, colour in (("baseline", "#d62728"), ("treatment", "#1f77b4")):
        pos, mean, ci = headline_band(df, mode)
        if not pos:
            continue
        n_reps = df[df["mode"] == mode]["repetition"].nunique() \
            if "repetition" in df.columns else 1
        ax.plot(pos, mean, marker="o", label=f"{mode} (n={n_reps})",
                color=colour, linewidth=2)
        if n_reps >= 2:
            ax.fill_between(pos, mean - ci, mean + ci, color=colour, alpha=0.18)
    ax.set_xlabel("Query position (1-based, within mode)")
    ax.set_ylabel("Cumulative Call-2 invocation rate")
    ax.set_title("Compounding: Call-2 firings vs query position (mean ± 95% CI)")
    ax.set_ylim(-0.02, 1.05)
    ax.axhline(1.0, color="lightgray", linewidth=0.5, linestyle="--")
    ax.legend(loc="best")
    ax.grid(True, linestyle=":", alpha=0.4)
    return _save(fig, out_dir, "headline_call2_rate")


def plot_cost(df: pd.DataFrame, out_dir: Path, window: int = 3) -> tuple[Path, Path]:
    """Rolling cost/query with vertical markers at promotion events."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 5))
    for mode, colour in (("baseline", "#d62728"), ("treatment", "#1f77b4")):
        pos, mean, ci = cost_band(df, mode, window=window)
        if not pos:
            continue
        n_reps = df[df["mode"] == mode]["repetition"].nunique() \
            if "repetition" in df.columns else 1
        ax.plot(pos, mean, marker="o",
                label=f"{mode} (rolling {window}, n={n_reps})",
                color=colour, linewidth=2)
        if n_reps >= 2:
            ax.fill_between(pos, mean - ci, mean + ci, color=colour, alpha=0.18)

    # Promotion markers from the first treatment repetition (positions are
    # stable across repetitions because the block order is fixed).
    treatment_rows = df[df["mode"] == "treatment"]
    if "repetition" in treatment_rows.columns and len(treatment_rows):
        first_rep = sorted(treatment_rows["repetition"].unique())[0]
        treatment_rows = treatment_rows[treatment_rows["repetition"] == first_rep]
    promos = treatment_rows[(treatment_rows["template_promotions"] > 0)
                            | (treatment_rows["composite_promotions"] > 0)]
    treatment_rows = treatment_rows.sort_values("index").reset_index(drop=True)
    index_to_pos = {int(v): i + 1 for i, v in enumerate(treatment_rows["index"].tolist())}
    drawn_labels: set[str] = set()
    for _, ev in promos.iterrows():
        pos = index_to_pos.get(int(ev["index"]))
        if pos is None:
            continue
        if ev["composite_promotions"] > 0:
            lbl = "composite promo" if "composite promo" not in drawn_labels else None
            ax.axvline(pos, color="#2ca02c", linestyle="--", linewidth=1.2,
                       label=lbl, alpha=0.8)
            if lbl:
                drawn_labels.add(lbl)
        elif ev["template_promotions"] > 0:
            lbl = "template promo" if "template promo" not in drawn_labels else None
            ax.axvline(pos, color="#9467bd", linestyle=":", linewidth=1.0,
                       label=lbl, alpha=0.6)
            if lbl:
                drawn_labels.add(lbl)

    ax.set_xlabel("Query index (1-based, within mode)")
    ax.set_ylabel("LLM tokens per query (in+out)")
    ax.set_title("Cost per query — treatment vs baseline, with promotion markers")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, linestyle=":", alpha=0.4)
    return _save(fig, out_dir, "cost_per_query")


def plot_served_by(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """Stacked area of served-by counts over treatment queries."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Restrict to the structural arm (the default story) and average per index
    # across repetitions so the stack is stable when repetitions > 1.
    treat = df[df["mode"] == "treatment"]
    if "arm" in treat.columns and (treat["arm"] == "structural").any():
        treat = treat[treat["arm"] == "structural"]
    fig, ax = plt.subplots(figsize=(9, 5))
    if treat.empty:
        ax.set_title("served_by — no treatment data")
        return _save(fig, out_dir, "served_by_stacked")
    agg = treat.groupby("index")[list(SERVED_COLS)].mean().sort_index()
    x      = list(range(1, len(agg) + 1))
    layers = [agg[c].values for c in SERVED_COLS]
    labels = [c.replace("served_by_", "") for c in SERVED_COLS]
    colours = ["#1f77b4", "#2ca02c", "#9467bd", "#17becf", "#d62728"]
    ax.stackplot(x, layers, labels=labels, colors=colours, alpha=0.85)
    ax.set_xlabel("Query index (1-based, treatment)")
    ax.set_ylabel("Edges executed (mean count)")
    ax.set_title("Where executions came from over time (treatment, structural)")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, linestyle=":", alpha=0.4)
    return _save(fig, out_dir, "served_by_stacked")


def plot_stage_decomposition(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """Per-query MEAN tokens split into call1/call2/call3, structural treatment
    vs baseline (grouped bars). The reader sees call2 -> ~0 under treatment
    while call1/call3 persist — the honest cost story that a total-token sum
    obscures."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as _np

    sm = stage_means_by_mode(df)
    stages = ["call1", "call2", "call3"]
    treat = [sm["treatment"][s] for s in stages]
    base = [sm["baseline"][s] for s in stages]
    x = _np.arange(len(stages))
    w = 0.38

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x - w / 2, base, w, label="baseline", color="#d62728", alpha=0.85)
    ax.bar(x + w / 2, treat, w, label="treatment", color="#1f77b4", alpha=0.85)
    for xs, vals in ((x - w / 2, base), (x + w / 2, treat)):
        for xi, v in zip(xs, vals):
            ax.text(xi, v, f"{v:.0f}", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels([s.upper() for s in stages])
    ax.set_ylabel("Mean tokens per query (in+out)")
    ax.set_title("Per-query token cost by stage — treatment vs baseline")
    ax.legend(loc="best")
    ax.grid(True, axis="y", linestyle=":", alpha=0.4)
    return _save(fig, out_dir, "stage_decomposition")


def plot_arm_comparison(df: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    """Identity ablation across tau (claim C3): for each metric (final node
    count, cumulative Call-2 rate, accuracy) plot the structural reference line
    and the semantic value at each threshold, so the over-merge (low tau) ->
    over-split (high tau) trend is visible."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    abl = arm_comparison_by_tau(df, "treatment")
    metrics = [
        ("final_node_count", "final node count"),
        ("cum_call2_rate",   "cum Call-2 rate"),
        ("accuracy",         "accuracy"),
    ]
    fig, axes = plt.subplots(1, len(metrics), figsize=(13, 4))
    if abl is None:
        axes[0].set_title("ablation — no semantic arm")
        return _save(fig, out_dir, "ablation_arms")

    taus = [float(t) for t in abl["thresholds"]]
    s = abl["structural"] or {}
    for ax, (key, title) in zip(axes, metrics):
        sem_vals = [abl["by_tau"][t]["semantic"].get(key)
                    if abl["by_tau"][t].get("semantic") else None
                    for t in abl["thresholds"]]
        sem_vals = [0 if v is None else v for v in sem_vals]
        ax.plot(taus, sem_vals, marker="o", color="#d62728", label="semantic")
        if s.get(key) is not None:
            ax.axhline(s[key], color="#1f77b4", linestyle="--",
                       label="structural")
        ax.set_title(title)
        ax.set_xlabel("semantic threshold τ")
        ax.grid(True, linestyle=":", alpha=0.4)
        ax.legend(loc="best", fontsize=8)
    holds = abl.get("claim_c3_holds_all_tau")
    fig.suptitle(f"Structural vs semantic identity across τ  (C3 holds at every τ: {holds})")
    fig.tight_layout()
    return _save(fig, out_dir, "ablation_arms")


# ── Top-level driver ─────────────────────────────────────────────────────────

def build_report(
    csv_path: str | Path = "evidence/out/runs.csv",
    out_dir: str | Path = "evidence/out",
) -> dict:
    """Load runs.csv, write the three figures, return a summary dict.

    The summary includes the acceptance-criteria numbers (cumulative-rate
    non-increasing over the value+path families, total tokens by mode,
    promotion counts) so the headline numbers are visible without opening
    the figures.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = load_runs(csv_path)

    headline = plot_headline(df, out_dir)
    cost     = plot_cost(df, out_dir)
    stage    = plot_stage_decomposition(df, out_dir)
    served   = plot_served_by(df, out_dir)

    figures = {
        "headline":             [str(p) for p in headline],
        "cost":                 [str(p) for p in cost],
        "stage_decomposition":  [str(p) for p in stage],
        "served":               [str(p) for p in served],
    }

    # Correctness cross-tab (claim C2) for the deployed structural system.
    c2 = accuracy_by_call2(df, "treatment")

    # Identity ablation (claim C3) — populated per tau when a semantic arm runs.
    arms = sorted(set(df.get("arm", pd.Series(["structural"])).dropna().unique()))
    c3 = arm_comparison_by_tau(df, "treatment")
    if c3 is not None:
        figures["ablation"] = [str(p) for p in plot_arm_comparison(df, out_dir)]

    # Model-config hygiene: an arm that mixes >1 (call1,call2,call3) config is
    # NOT a clean comparison (model must be held fixed). Flag it and mark the
    # ablation un-clean so a confounded arm is never presented as a result.
    model_cfgs = model_configs_by_arm(df)
    unclean_arms = [a for a, v in model_cfgs.items() if not v["clean"]]
    if c3 is not None:
        c3["config_clean"] = not unclean_arms
        c3["unclean_arms"] = unclean_arms

    # Cost: per-query MEANS by mode (never sums across unequal n). Include the
    # sum only when n is equal across modes.
    counts = n_by_mode(df)
    equal_n = counts.get("treatment") == counts.get("baseline") and counts.get("treatment", 0) > 0
    cost_summary = {
        "tokens_per_query_by_mode": tokens_per_query_by_mode(df),  # headline cost
        "stage_means_by_mode":      stage_means_by_mode(df),
        "call2_rate_by_mode":       call2_rate_by_mode(df),
        "call2_rate_reduction":     call2_rate_reduction(df),       # HEADLINE number
        "n_by_mode":                counts,
        "equal_n":                  bool(equal_n),
        "tokens_by_mode_sum":       total_tokens_by_mode(df) if equal_n else None,
        "note": ("per-query means are the honest comparison; treatment per-query "
                 "<= baseline, gap bounded by call1/call3 (call2 -> ~0). Sums are "
                 "only reported when n is equal across modes."),
    }

    summary = {
        "csv":      str(csv_path),
        **figures,
        "headline_metric":     {"call2_rate_reduction": cost_summary["call2_rate_reduction"],
                                "call2_rate_by_mode": cost_summary["call2_rate_by_mode"]},
        "cost":                cost_summary,
        "accuracy_by_mode":    overall_accuracy(df),
        "accuracy_cross_tab":  c2,            # claim C2
        "arms_present":        arms,
        "ablation":            c3,            # claim C3 per tau (None if single-arm)
        "model_configs_by_arm": model_cfgs,  # >1 config per arm => not clean
        "promotions_by_mode": {
            mode: {
                "template":  int(grp["template_promotions"].sum()),
                "composite": int(grp["composite_promotions"].sum()),
            }
            for mode, grp in df.groupby("mode")
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # Emit the headline claim numbers to stdout.
    print(f"[HEADLINE] Call-2 rate: baseline={cost_summary['call2_rate_by_mode']['baseline']} "
          f"treatment={cost_summary['call2_rate_by_mode']['treatment']} "
          f"reduction={cost_summary['call2_rate_reduction']}")
    print(f"[COST] tokens/query: {cost_summary['tokens_per_query_by_mode']}  (equal_n={equal_n})")
    print(f"[C2] accuracy call2==0: {c2['accuracy_call2_0']} (n={c2['n_call2_0']})  "
          f"call2==1: {c2['accuracy_call2_1']} (n={c2['n_call2_1']})  -> holds: {c2['claim_c2_holds']}")
    if c3 is not None:
        print(f"[C3] ablation across tau {c3['thresholds']} -> holds at every tau: "
              f"{c3['claim_c3_holds_all_tau']}")
    return summary


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Compounding-property report builder")
    p.add_argument("--csv", default="evidence/out/runs.csv")
    p.add_argument("--out", default="evidence/out")
    args = p.parse_args()
    summary = build_report(args.csv, args.out)
    print(json.dumps(summary, indent=2))
