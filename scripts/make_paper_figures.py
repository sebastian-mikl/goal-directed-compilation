#!/usr/bin/env python
"""Render the paper's data figures, and the auxiliary tables, from reports/paper_numbers.csv.

Every plotted value is one row of the numbers CSV, named by its full key
(fig_table, domain, system, tier_group, scoring, metric). A key that matches no row
stops the run, and the CSV may not hold one key twice. No plotted value, cell count
or comparator version is typed in here: all of them are read from the CSV. Every
value a figure plots is written to <out>/figures_manifest.csv with the key it came
from, its numerator/denominator, comparator and source, so a caption can be checked
against the manifest line by line.

Paper figure -> file (md links reports/figures/<file>; tex \\graphicspath{figures/}):
  Figure 4  fig2_silent_error_tolerant.png  Spider in-scope silent-error, tie-tolerant
  Figure 5  fig1_accuracy.png               Spider in-scope accuracy, strict
  Figure 6  fig2_silent_error.png           Spider in-scope silent-error, strict
  Figure 7  fig4_s40_refusal.png            GDC out-of-scope-register refusal accuracy,
                                            table-coverage instruction off vs on
  Figure 8  fig8_cost_frontier.png          risk: accuracy and silent-error vs $/query
Figure 1 (pipeline.png) and Figure 3 (spider_data_scope.png) are diagrams with no
data in them and are not drawn here. There is no Figure 2.

Also drawn (not in the paper; --no-extras skips them): fig1_2_combined,
fig3_network_tiers, fig_strict_vs_tolerant_silent, fig6_haiku_robustness.

Risk figures (Figure 8, fig6_haiku_robustness) and the risk tables are skipped when
the CSV holds no risk rows, as in the public release, which withholds the risk
domain, or when --no-risk is given. Their data slices carry `_risk` in the file
name so the withheld-path patterns catch them.

Usage:
  python scripts/make_paper_figures.py [--csv reports/paper_numbers.csv]
         [--out reports/figures] [--risk-group in_contract_within_vocab]
         [--no-extras] [--no-tables] [--no-risk]
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402
import matplotlib.ticker as mticker                  # noqa: E402
from matplotlib.patches import Patch                 # noqa: E402
from matplotlib.text import Text                     # noqa: E402
from matplotlib.transforms import Bbox               # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# ── palette (Okabe-Ito subset; validated colorblind-safe) ───────────────────
NETWORK = "#0072B2"      # deep blue: GDC in every figure
BASELINE = "#b8b8b8"     # muted gray: baselines
OUTCOME = {"correct": "#009E73", "silent_wrong": "#D55E00", "refused": "#56B4E9"}
S40 = {"off": "#b8b8b8", "on": "#0072B2"}
INK, MUTED = "#1a1a1a", "#5a5a5a"
F8_GRAY, F8_LABEL_GRAY = "#B0B0B0", "#4D4D4D"     # Figure 8 markers / labels

BASE_RC = {
    "font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"],
    "font.size": 10, "axes.titlesize": 11, "axes.labelsize": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#888888", "axes.linewidth": 0.8,
    "axes.grid": True, "axes.axisbelow": True, "axes.grid.axis": "y",
    "grid.color": "#e8e8e8", "grid.linewidth": 0.8,
    "xtick.color": INK, "ytick.color": INK, "text.color": INK,
    "figure.dpi": 150,
}
# Figure 8 is drawn on matplotlib's default style with these changes (the style of
# the paper's Figure 8 since 30 Aug).
FIG8_RC = {"font.size": 13, "axes.spines.top": False, "axes.spines.right": False}

LABELS = {"network": "GDC", "planner": "planner", "react": "react",
          "code_agent": "code-agent", "text_to_sql": "text-to-SQL"}
SYS_ALL = ["network", "planner", "react", "code_agent", "text_to_sql"]

# The paper's name for the tau(q) answer tier. The CSV's tier_group keeps the
# builder's identifier (in_contract_answer); only the rendered text uses this.
SCOPE = "in-scope"

KEY = ("fig_table", "domain", "system", "tier_group", "scoring", "metric")


# ════════════════════════════ the numbers CSV ════════════════════════════════
class Numbers:
    def __init__(self, path: Path):
        self.path = Path(path)
        with open(self.path, encoding="utf-8") as fh:
            self.rows = list(csv.DictReader(fh))
        self.by_key: dict[tuple, dict] = {}
        for r in self.rows:
            k = tuple(r[c] for c in KEY)
            if k in self.by_key:
                raise LookupError(f"{self.path.name}: key {dict(zip(KEY, k))} appears twice")
            self.by_key[k] = r
        self.label = f"reports/{self.path.name}"

    def row(self, sel: dict) -> dict:
        k = tuple(sel[c] for c in KEY)          # every key column must be named
        try:
            return self.by_key[k]
        except KeyError:
            raise LookupError(f"no row of {self.path.name} has key {sel}") from None

    def value(self, sel: dict) -> float:
        v = self.row(sel)["value_pct"]
        if v in ("", None):
            raise LookupError(f"row {sel} of {self.path.name} has no value")
        return float(v)

    def has_domain(self, domain: str) -> bool:
        return any(r["domain"] == domain for r in self.rows)

    # partial-key helpers, used only by the tables
    def q(self, **kw):
        return [r for r in self.rows if all(r.get(k) == v for k, v in kw.items())]

    def maybe(self, **kw):
        r = self.q(**kw)
        if len(r) > 1:
            raise LookupError(f"{len(r)} rows of {self.path.name} match {kw}")
        return r[0] if r else None

    def one(self, **kw):
        r = self.maybe(**kw)
        if r is None:
            raise LookupError(f"no row of {self.path.name} matches {kw}")
        return r

    def val(self, **kw):
        r = self.one(**kw)
        return float(r["value_pct"]), int(r["numerator"]), int(r["denominator"])

    def versions(self) -> tuple[str, str]:
        """(strict, tolerant) comparator versions, read from the CSV. The tolerant
        version is the comparator of the tolerant rows; the strict one is the
        comparator every other strict row carries. (The builder labels the strict
        rows of the Spider five-system block with the tolerant scorer's version.)"""
        tol = {r["comparator"] for r in self.rows if r["scoring"] == "tolerant"}
        if len(tol) != 1:
            raise LookupError(f"tolerant rows carry comparators {sorted(tol)}")
        (t,) = tol
        strict = {r["comparator"] for r in self.rows if r["scoring"] == "strict"} - tol
        if len(strict) != 1:
            raise LookupError(f"strict rows carry comparators {sorted(strict)}")
        (s,) = strict
        if t != s + "t":
            raise LookupError(f"tolerant comparator {t} is not strict {s} + 't'")
        return s, t


# ════════════════════════════ named selections ═══════════════════════════════
def S(fig_table, domain, system, tier_group, scoring, metric) -> dict:
    return dict(fig_table=fig_table, domain=domain, system=system,
                tier_group=tier_group, scoring=scoring, metric=metric)


def spider_in_scope(system, scoring, metric):
    """Spider answer tier pooled over wta_1/world_1/bike_1 (Figures 4-6)."""
    return S("Fig1/Fig2/Table3", "spider_pooled", system, "in_contract_answer", scoring, metric)


SPIDER_DOMAINS = ["wta_1", "world_1", "bike_1"]


def gdc_spider_tier(domain, tier_group, metric):
    """GDC per Spider database and tier (Figure 7, fig3_network_tiers)."""
    return S("Fig3/Fig4", domain, "network", tier_group, "strict", metric)


def oos_register(domain, instruction):
    """GDC refusal accuracy on the out-of-scope-register tier, table-coverage
    instruction `off` or `on` (Figure 7)."""
    return gdc_spider_tier(domain, f"out_of_scope_register_s40{instruction}", "correct")


# Risk in-scope sets the CSV carries (Table2 and CostFrontier). Figure 8 plots one.
RISK_GROUPS = {
    "in_contract_within_vocab": "within GDC's vocabulary (T1-T5, house-vocab)",
    "in_contract_answer": "every in-scope tier (within + algebra_boundary)",
    "in_contract_beyond_vocab": "beyond GDC's vocabulary (algebra_boundary)",
}
# The paper's Figure 8 caption quotes the within-vocabulary set.
RISK_GROUP_DEFAULT = "in_contract_within_vocab"


def risk_cost(system, metric, group):
    return S("CostFrontier", "risk", system, group, "strict", metric)


def risk_outcome(system, metric, group):
    return S("Table2", "risk", system, group, "strict", metric)


H_TIERS = [("T1_single_op", "T1"), ("T2_two_ops", "T2"), ("T3_three_ops", "T3"),
           ("T4_business_rule_multi_op", "T4"), ("T5_high_complexity", "T5"), ("house_vocab", "hv")]


def haiku_panel(model, system, tier, metric):
    return S(f"Haiku_{model}", "risk", system, tier, "strict", metric)


# ════════════════════════════ manifest ═══════════════════════════════════════
class Manifest:
    """Fetches each plotted row and records it beside the figure that plots it."""
    FIELDS = ["figure", "file", "panel", "series", *KEY, "value_pct", "numerator",
              "denominator", "comparator", "source"]

    def __init__(self, nums: Numbers):
        self.nums, self.records = nums, []

    def take(self, figure, file, panel, series, sel) -> dict:
        r = self.nums.row(sel)
        if r["value_pct"] in ("", None):
            raise LookupError(f"{figure}: row {sel} has no value")
        self.records.append({"figure": figure, "file": file, "panel": panel, "series": series,
                             **{c: r[c] for c in KEY}, "value_pct": r["value_pct"],
                             "numerator": r["numerator"], "denominator": r["denominator"],
                             "comparator": r["comparator"], "source": r["source"]})
        return r

    def write(self, out: Path):
        risk = [x for x in self.records if x["domain"] == "risk"]
        rest = [x for x in self.records if x["domain"] != "risk"]
        for name, recs in (("figures_manifest.csv", rest), ("figures_manifest_risk.csv", risk)):
            if not recs:
                continue
            with open(out / name, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=self.FIELDS)
                w.writeheader()
                w.writerows(recs)


def fv(r):
    return float(r["value_pct"])


def common_denominator(rows, what):
    dens = {r["denominator"] for r in rows}
    if len(dens) != 1:
        raise LookupError(f"{what}: rows have different denominators {sorted(dens)}")
    return int(dens.pop())


def write_slice(out, name, header, records):
    with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(records)


def footnote(fig, text):
    fig.text(0.005, 0.005, text, fontsize=6.2, color=MUTED, ha="left", va="bottom")


def save(fig, out, name, **kw):
    # metadata Software=None: no matplotlib version string in the PNG, so the bytes do not
    # depend on the matplotlib patch release.
    fig.savefig(out / f"{name}.png", **({"bbox_inches": "tight", "dpi": 200, "metadata": {"Software": None}} | kw))
    plt.close(fig)


# ════════════════════════════ Spider in-scope bars (Figures 4-6) ═════════════
# Bars run by strict accuracy, highest first; ties by strict silent-error, lowest
# first; then this list's order. All three Spider figures use the one order.
SPIDER_TIEBREAK = ["text_to_sql", "network", "react", "code_agent", "planner"]


def spider_order(nums):
    key = []
    for i, s in enumerate(SPIDER_TIEBREAK):
        a = nums.value(spider_in_scope(s, "strict", "accuracy"))
        w = nums.value(spider_in_scope(s, "strict", "silent_wrong"))
        key.append((-a, w, i, s))
    return [k[-1] for k in sorted(key)]


def spider_bars(ax, man, figure, file, scoring, metric, ylabel, title_fmt, order, panel=""):
    rows = [man.take(figure, file, panel, s, spider_in_scope(s, scoring, metric)) for s in order]
    n = common_denominator(rows, f"{figure} {metric} {scoring}")
    heights = [fv(r) for r in rows]
    xs = list(range(len(order)))
    ax.bar(xs, heights, width=0.66,
           color=[NETWORK if s == "network" else BASELINE for s in order],
           edgecolor=[("#004c77" if s == "network" else "#9a9a9a") for s in order],
           linewidth=1.0, zorder=3)
    for x, h, s in zip(xs, heights, order):
        ax.text(x, h + 1.3, f"{h:.1f}", ha="center", va="bottom",
                fontsize=9, fontweight=("bold" if s == "network" else "normal"),
                color=(NETWORK if s == "network" else INK))
    ax.set_xticks(xs)
    ax.set_xticklabels([LABELS[s] for s in order], rotation=0)
    ax.set_ylim(0, max(heights) * 1.18 if max(heights) > 0 else 1)
    ax.set_ylabel(ylabel)
    ax.set_title(title_fmt.format(n=n), fontweight="bold")
    return [[s, metric, fv(r), int(r["numerator"]), int(r["denominator"])] for s, r in zip(order, rows)]


def fig_spider(nums, man, out, figure, file, scoring, metric, ylabel, title_fmt, foot):
    fig, ax = plt.subplots(figsize=(6.4, 3.9))
    rec = spider_bars(ax, man, figure, file, scoring, metric, ylabel, title_fmt, spider_order(nums))
    footnote(fig, foot)
    save(fig, out, file)
    write_slice(out, file, ["system", "metric", "value_pct", "numerator", "denominator"], rec)


def fig4_tolerant(nums, man, out):
    fig_spider(nums, man, out, "Figure 4", "fig2_silent_error_tolerant", "tolerant", "silent_wrong",
               "silent-error rate (%)", f"Spider {SCOPE} silent-error, tie-tolerant scoring",
               f"source: {nums.label}  ·  tie-tolerant scoring   ·  lower is better")


def fig5_accuracy(nums, man, out):
    fig_spider(nums, man, out, "Figure 5", "fig1_accuracy", "strict", "accuracy",
               f"{SCOPE} accuracy (%)", f"Spider {SCOPE} accuracy (pooled, {{n}} cells)",
               f"source: {nums.label}  ·  strict scoring")


def fig6_strict_silent(nums, man, out):
    fig_spider(nums, man, out, "Figure 6", "fig2_silent_error", "strict", "silent_wrong",
               "silent-error rate (%)", f"Spider {SCOPE} silent-error (pooled, {{n}} cells)",
               f"source: {nums.label}  ·  strict scoring   ·  lower is better")


# ════════════════════════════ Figure 7: table-coverage instruction ═══════════
# Tick text under each database. A description of each database's briefing, not a
# number; it is not in the CSV.
BRIEFING = {"wta_1": "phantom-table\nbriefing", "world_1": "phantom-table\nbriefing",
            "bike_1": "honest single-\ntable briefing"}


def fig7_s40(nums, man, out):
    figure, file = "Figure 7", "fig4_s40_refusal"
    fig, ax = plt.subplots(figsize=(6.6, 3.9))
    w, rec, tops = 0.36, [], []
    for i, dom in enumerate(SPIDER_DOMAINS):
        r_off = man.take(figure, file, dom, "instruction off", oos_register(dom, "off"))
        r_on = man.take(figure, file, dom, "instruction on", oos_register(dom, "on"))
        off, on = fv(r_off), fv(r_on)
        lift = round(on - off, 1)       # the difference of the two printed values
        exact = 100 * (int(r_on["numerator"]) / int(r_on["denominator"])
                       - int(r_off["numerator"]) / int(r_off["denominator"]))
        ax.bar(i - w / 2, off, w, color=S40["off"], edgecolor="#9a9a9a", linewidth=0.8, zorder=3)
        ax.bar(i + w / 2, on, w, color=S40["on"], edgecolor="#004c77", linewidth=0.8, zorder=3)
        ax.text(i - w / 2, off + 1.3, f"{off:.1f}", ha="center", va="bottom", fontsize=8.5, color=INK)
        ax.text(i + w / 2, on + 1.3, f"{on:.1f}", ha="center", va="bottom", fontsize=8.5,
                color=NETWORK, fontweight="bold")
        ax.annotate(f"{lift:+.1f}", xy=(i, max(off, on) + 7), ha="center", fontsize=8.5, color=MUTED)
        tops.append(max(off, on) + 7)
        rec.append([dom, off, on, lift, round(exact, 2),
                    f"{r_off['numerator']}/{r_off['denominator']}", f"{r_on['numerator']}/{r_on['denominator']}"])
    ax.set_xticks(range(len(SPIDER_DOMAINS)))
    ax.set_xticklabels([f"{d}\n{BRIEFING[d]}" for d in SPIDER_DOMAINS], fontsize=8.2)
    ax.set_ylabel("out-of-scope refusal accuracy (%)")
    ax.set_ylim(0, max(90, 10 * math.ceil((max(tops) + 6) / 10)))
    ax.set_title("Out-of-scope refusal: table-coverage instruction off vs on, per Spider domain",
                 fontweight="bold")
    ax.legend([Patch(color=S40["off"]), Patch(color=S40["on"])], ["instruction off", "instruction on"],
              frameon=False, loc="upper left")
    fig.subplots_adjust(bottom=0.30)
    footnote(fig, f"source: {nums.label}  ·  strict scoring")
    save(fig, out, file)
    write_slice(out, file, ["domain", "s40off_pct", "s40on_pct", "lift_pts", "lift_pts_exact",
                            "s40off_n", "s40on_n"], rec)


# ════════════════════════════ Figure 8: risk cost frontier ═══════════════════
F8_ORDER = ["network", "react", "planner", "code_agent", "text_to_sql"]

# Preferred label position per system and panel: (dx pt, dy pt, ha, va, leader).
# Layout only. auto placement tries these first, then CANDIDATES, and takes the
# first position whose text box stays inside the axes and clear of every marker
# and every label already placed.
F8_PREFS = {
    "acc": {
        "network": (0, -22, "center", "top", True),
        "react": (-12, 0, "right", "center", False),
        "planner": (10, 5, "left", "bottom", False),
        "code_agent": (0, 14, "center", "bottom", False),
        "text_to_sql": (10, 0, "left", "center", False),
    },
    "silent": {
        "network": (16, -26, "left", "top", True),
        "react": (-12, 0, "right", "center", False),
        "planner": (12, 0, "left", "center", False),
        "code_agent": (0, 14, "center", "bottom", False),
        "text_to_sql": (-10, 22, "left", "bottom", True),
    },
}
CANDIDATES = [
    (12, 6, "left", "bottom", False), (-12, 6, "right", "bottom", False),
    (12, -6, "left", "top", False), (-12, -6, "right", "top", False),
    (0, 14, "center", "bottom", False), (0, -14, "center", "top", False),
    (16, 22, "left", "bottom", True), (-16, 22, "right", "bottom", True),
    (16, -26, "left", "top", True), (-16, -26, "right", "top", True),
    (0, 32, "center", "bottom", True), (0, -34, "center", "top", True),
    (28, 0, "left", "center", True), (-28, 0, "right", "center", True),
]
# Axis ranges, widened only when a value falls outside them.
F8_XLIM, F8_ACC_YLIM, F8_SIL_YLIM = (0.0025, 0.05), (80.0, 101.0), (0.0, 7.5)


def cost_frontier_points(nums, group, man=None, figure="Figure 8", file="fig8_cost_frontier"):
    """{system: dict(cost, acc, silent)}. Cost from CostFrontier, accuracy and silent-error
    from Table2, both over `group`. The two tables' accuracy must agree."""
    take = (lambda panel, s, sel: man.take(figure, file, panel, s, sel)) if man else (lambda p, s, sel: nums.row(sel))
    pts = {}
    for s in F8_ORDER:
        cost = take("x: cost", s, risk_cost(s, "mean_cost_usd_per_query", group))
        acc = take("left: accuracy", s, risk_outcome(s, "accuracy", group))
        sil = take("right: silent-error", s, risk_outcome(s, "silent_wrong", group))
        cf_acc = nums.row(risk_cost(s, "accuracy", group))
        if (cf_acc["value_pct"], cf_acc["numerator"], cf_acc["denominator"]) != \
                (acc["value_pct"], acc["numerator"], acc["denominator"]):
            raise LookupError(f"{figure}: {s} accuracy differs between CostFrontier "
                              f"({cf_acc['value_pct']} {cf_acc['numerator']}/{cf_acc['denominator']}) and Table2 "
                              f"({acc['value_pct']} {acc['numerator']}/{acc['denominator']}) for {group}")
        pts[s] = dict(cost=fv(cost), acc=fv(acc), silent=fv(sil), n=int(acc["denominator"]),
                      tokens=nums.value(risk_cost(s, "mean_tokens_per_query", group)),
                      calls=nums.value(risk_cost(s, "mean_llm_calls_per_query", group)))
    return pts


def _widen(lim, values, pad, log=False):
    lo, hi = lim
    vmin, vmax = min(values), max(values)
    if log:
        if vmin < lo:
            lo = vmin / (1 + pad)
        if vmax > hi:
            hi = vmax * (1 + pad)
    else:
        if vmin < lo:
            lo = math.floor(vmin - pad)
        if vmax > hi:
            hi = math.ceil(vmax + pad)
    return lo, hi


def _annotate(ax, name, xy, spec, is_gdc):
    dx, dy, ha, va, leader = spec
    color = NETWORK if is_gdc else F8_LABEL_GRAY
    return ax.annotate(
        LABELS[name], xy=xy, xytext=(dx, dy), textcoords="offset points", ha=ha, va=va,
        fontsize=11, color=color, fontweight="bold" if is_gdc else "normal",
        arrowprops=dict(arrowstyle="-", color=color, linewidth=0.8, shrinkA=3, shrinkB=4) if leader else None,
        zorder=4)


def _text_box(ann, renderer):
    """The label's text box in display units, without its leader line."""
    ann.update_positions(renderer)
    return Text.get_window_extent(ann, renderer)


def _place_labels(fig, ax, key, pts, marker_r):
    """Deterministic greedy placement: GDC first, then F8_ORDER."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    axbox = ax.get_window_extent(renderer)
    obstacles = []
    for s in F8_ORDER:
        x, y = ax.transData.transform((pts[s]["cost"], pts[s][key]))
        r = marker_r[s] * fig.dpi / 72
        obstacles.append(Bbox([[x - r, y - r], [x + r, y + r]]))
    placed = []
    for s in F8_ORDER:
        xy = (pts[s]["cost"], pts[s][key])
        tried = [F8_PREFS[key][s]] + [c for c in CANDIDATES if c != F8_PREFS[key][s]]
        chosen = None
        for spec in tried:
            ann = _annotate(ax, s, xy, spec, s == "network")
            bb = _text_box(ann, renderer).padded(2)
            inside = (bb.x0 >= axbox.x0 and bb.x1 <= axbox.x1 and bb.y0 >= axbox.y0 and bb.y1 <= axbox.y1)
            if inside and not any(bb.overlaps(o) for o in obstacles + placed):
                chosen = bb
                break
            ann.remove()
        if chosen is None:
            print(f"  warning: Figure 8 {key} panel: no clear spot for {LABELS[s]}; preferred position used",
                  file=sys.stderr)
            ann = _annotate(ax, s, xy, F8_PREFS[key][s], s == "network")
            chosen = _text_box(ann, renderer)
        placed.append(chosen)


def draw_cost_frontier(pts, path, legacy=False):
    """Two panels against mean $/query (log x): accuracy and silent-error. legacy=True
    reproduces the 30 Aug render (fixed offsets, draw order, no GDC-on-top) and exists
    only so a check can compare against that image byte for byte."""
    with plt.rc_context():
        matplotlib.rcdefaults()
        plt.rcParams.update(FIG8_RC)
        fig, (ax_acc, ax_sil) = plt.subplots(1, 2, figsize=(11, 4.8))
        size = {s: (170 if s == "network" else 130) for s in F8_ORDER}
        for ax, key in ((ax_acc, "acc"), (ax_sil, "silent")):
            for s in F8_ORDER:
                is_gdc = s == "network"
                ax.scatter(pts[s]["cost"], pts[s][key], s=size[s], c=NETWORK if is_gdc else F8_GRAY,
                           edgecolors="black" if is_gdc else ("none" if legacy else "white"),
                           linewidths=1.3 if (is_gdc or legacy) else 0.8,
                           zorder=3 if (legacy or not is_gdc) else 5)
                if legacy:
                    _annotate(ax, s, (pts[s]["cost"], pts[s][key]), F8_PREFS_LEGACY[key][s], is_gdc)
            ax.set_xscale("log")
            ax.set_xlabel("mean cost per query ($, log scale)")
            ax.grid(True, which="major", axis="both", linestyle="-", linewidth=0.6, alpha=0.35, zorder=0)
            ax.set_xlim(*(F8_XLIM if legacy else _widen(F8_XLIM, [p["cost"] for p in pts.values()], 0.15, log=True)))
            ax.xaxis.set_minor_locator(mticker.NullLocator())
            ax.xaxis.set_major_locator(mticker.FixedLocator([0.003, 0.01, 0.03]))
            ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:g}"))
        ax_acc.set_title("accuracy (higher is better)", fontsize=13, pad=10)
        ax_acc.set_ylabel("accuracy (%)")
        ax_acc.set_ylim(*(F8_ACC_YLIM if legacy else _widen(F8_ACC_YLIM, [p["acc"] for p in pts.values()], 2)))
        ax_sil.set_title("silent-error (lower is better)", fontsize=13, pad=10)
        ax_sil.set_ylabel("silent-error (%)")
        ax_sil.set_ylim(*(F8_SIL_YLIM if legacy else _widen(F8_SIL_YLIM, [p["silent"] for p in pts.values()], 1)))
        fig.tight_layout()
        if not legacy:
            marker_r = {s: math.sqrt(size[s]) / 2 + 1 for s in F8_ORDER}
            _place_labels(fig, ax_acc, "acc", pts, marker_r)
            _place_labels(fig, ax_sil, "silent", pts, marker_r)
        fig.savefig(path, dpi=200, metadata={"Software": None})
        plt.close(fig)


# The 30 Aug label offsets (scratch_figures/render_fig8.py), for the legacy check only.
F8_PREFS_LEGACY = {
    "acc": {"network": (0, -22, "center", "top", True), "react": (-12, 0, "right", "center", False),
            "planner": (10, 5, "left", "bottom", False), "code_agent": (0, 14, "center", "bottom", False),
            "text_to_sql": (10, 0, "left", "center", False)},
    "silent": {"network": (16, -26, "left", "top", True), "react": (-12, 0, "right", "center", False),
               "planner": (12, 6, "left", "bottom", False), "code_agent": (0, 14, "center", "bottom", False),
               "text_to_sql": (-12, 13, "left", "bottom", True)},
}


def fig8_cost_frontier(nums, man, out, group, file="fig8_cost_frontier"):
    pts = cost_frontier_points(nums, group, man, file=file)
    draw_cost_frontier(pts, out / f"{file}.png")
    write_slice(out, f"{file}_risk", ["system", "risk_group", "cells", "accuracy_pct", "silent_wrong_pct",
                                      "mean_usd_per_q", "mean_tokens_per_q", "mean_calls_per_q"],
                [[s, group, p["n"], p["acc"], p["silent"], p["cost"], p["tokens"], p["calls"]]
                 for s, p in pts.items()])


# ════════════════════════════ extras (not in the paper) ══════════════════════
def extra_combined(nums, man, out, versions):
    figure, file = "extra", "fig1_2_combined"
    order = spider_order(nums)
    fig, (axa, axb) = plt.subplots(1, 2, figsize=(10.2, 3.9))
    spider_bars(axa, man, figure, file, "strict", "accuracy", "accuracy (%)", "Accuracy (higher better)",
                order, panel="accuracy")
    spider_bars(axb, man, figure, file, "strict", "silent_wrong", "silent-error (%)",
                "Silent-error (lower better)", order, panel="silent-error")
    n = common_denominator([nums.row(spider_in_scope(s, "strict", "accuracy")) for s in order], file)
    for ax in (axa, axb):
        for lab in ax.get_xticklabels():
            lab.set_rotation(18)
            lab.set_ha("right")
    fig.suptitle(f"Spider {SCOPE}: accuracy vs silent-error, {len(order)} systems ({n} cells, {versions[0]})",
                 fontweight="bold", fontsize=11)
    fig.subplots_adjust(bottom=0.22, top=0.86, wspace=0.22)
    footnote(fig, f"source: {nums.label}  ·  comparator {versions[0]} (strict)")
    save(fig, out, file)


def extra_network_tiers(nums, man, out, versions):
    figure, file = "extra", "fig3_network_tiers"
    tiers = [("answer", "answer"), ("out_of_scope_register_s40off", "out-of-scope\n(instr. off)"),
             ("out_of_contract_aggregate", "aggregate\nboundary")]
    fig, axes = plt.subplots(1, 3, figsize=(10.6, 4.1), sharey=True)
    rec = []
    for ax, dom in zip(axes, SPIDER_DOMAINS):
        xs = list(range(len(tiers)))
        get = {m: [fv(man.take(figure, file, dom, f"{tg} {m}", gdc_spider_tier(dom, tg, m))) for tg, _ in tiers]
               for m in ("correct", "silent_wrong", "refused_should_answer")}
        corr, sil, ref = get["correct"], get["silent_wrong"], get["refused_should_answer"]
        ax.bar(xs, corr, 0.62, color=OUTCOME["correct"], zorder=3)
        ax.bar(xs, sil, 0.62, bottom=corr, color=OUTCOME["silent_wrong"], zorder=3)
        ax.bar(xs, ref, 0.62, bottom=[c + s for c, s in zip(corr, sil)], color=OUTCOME["refused"], zorder=3)
        for x, (tg, _) in zip(xs, tiers):
            if corr[x] >= 8:
                ax.text(x, corr[x] / 2, f"{corr[x]:.0f}", ha="center", va="center", color="white",
                        fontsize=8, fontweight="bold")
            rec.append([dom, tg, corr[x], sil[x], ref[x]])
        ax.set_xticks(xs)
        ax.set_xticklabels([lab for _, lab in tiers], fontsize=8.5)
        ax.set_title(dom, fontweight="bold")
        ax.set_ylim(0, 108)
        if ax is axes[0]:
            ax.set_ylabel("GDC outcome share (%)")
    axes[0].legend(handles=[Patch(color=OUTCOME["correct"], label="correct  (answer, or correct refusal on boundary tiers)"),
                            Patch(color=OUTCOME["silent_wrong"], label="silent-wrong  (confident wrong answer)"),
                            Patch(color=OUTCOME["refused"], label="refused")],
                   loc="upper center", bbox_to_anchor=(1.75, -0.13), ncol=1, frameon=False, fontsize=8.2)
    fig.suptitle("GDC outcome composition by tier, per Spider domain", fontweight="bold", fontsize=11.5, y=1.0)
    fig.subplots_adjust(bottom=0.30)
    footnote(fig, f"source: {nums.label}  ·  comparator {versions[0]} (strict)")
    save(fig, out, file)
    write_slice(out, file, ["domain", "tier", "correct_pct", "silent_wrong_pct", "refused_pct"], rec)


def extra_strict_vs_tolerant(nums, man, out, versions):
    figure, file = "extra", "fig_strict_vs_tolerant_silent"
    order = spider_order(nums)
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    w, rec = 0.38, []
    for i, s in enumerate(order):
        rs = man.take(figure, file, "strict", s, spider_in_scope(s, "strict", "silent_wrong"))
        rt = man.take(figure, file, "tolerant", s, spider_in_scope(s, "tolerant", "silent_wrong"))
        strict, tol = fv(rs), fv(rt)
        ax.bar(i - w / 2, strict, w, color=BASELINE, edgecolor="#9a9a9a", linewidth=0.8, zorder=3)
        ax.bar(i + w / 2, tol, w, color=NETWORK, edgecolor="#004c77", linewidth=0.8, zorder=3)
        ax.text(i - w / 2, strict + 0.5, f"{strict:.1f}", ha="center", va="bottom", fontsize=8.2, color=INK)
        ax.text(i + w / 2, tol + 0.5, f"{tol:.1f}", ha="center", va="bottom", fontsize=8.2,
                color=NETWORK, fontweight=("bold" if s == "network" else "normal"))
        rec.append([s, strict, tol, int(rs["numerator"]), int(rt["numerator"]), int(rs["denominator"])])
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels([LABELS[s] for s in order])
    for lab, s in zip(ax.get_xticklabels(), order):
        if s == "network":
            lab.set_fontweight("bold")
            lab.set_color(NETWORK)
    n = rec[0][-1]
    ax.set_ylabel(f"{SCOPE} silent-error (%)")
    ax.set_ylim(0, max(max(r[1], r[2]) for r in rec) * 1.22 or 1)
    ax.set_title(f"Spider {SCOPE} silent-error: strict vs tie-tolerant ({n} cells)", fontweight="bold")
    ax.legend([Patch(color=BASELINE, ec="#9a9a9a"), Patch(color=NETWORK, ec="#004c77")],
              [f"strict ({versions[0]})", f"tie-tolerant ({versions[1]})"], frameon=False, loc="upper left")
    fig.subplots_adjust(bottom=0.20)
    gdc = next(r for r in rec if r[0] == "network")
    footnote(fig, f"source: {nums.label}  ·  {versions[0]} vs {versions[1]}  ·  lower is better  ·  "
                  f"GDC strict - tolerant = {gdc[3] - gdc[4]} of {n} cells")
    save(fig, out, file)
    write_slice(out, file, ["system", "silent_strict_pct", "silent_tolerant_pct", "silent_strict_n",
                            "silent_tolerant_n", "cells"], rec)


def extra_haiku(nums, man, out, versions):
    figure, file = "extra", "fig6_haiku_robustness"
    xs = list(range(len(H_TIERS)))
    fig, (axa, axb) = plt.subplots(1, 2, figsize=(11, 4.3), sharex=True)
    styles = [("network", "sonnet", NETWORK, "-", "o", "GDC · Sonnet"),
              ("network", "haiku", NETWORK, "--", "o", "GDC · Haiku"),
              ("planner", "sonnet", "#888888", "-", "s", "planner · Sonnet"),
              ("planner", "haiku", "#888888", "--", "s", "planner · Haiku")]
    rec, sources = [], set()
    for metric, ax, ylab in [("accuracy", axa, "accuracy (%)"), ("silent_wrong", axb, "silent-error (%)")]:
        for sys_, model, color, ls, mk, lab in styles:
            rows = [man.take(figure, file, metric, f"{sys_} {model}", haiku_panel(model, sys_, t, metric))
                    for t, _ in H_TIERS]
            sources |= {r["source"] for r in rows}
            ys = [fv(r) for r in rows]
            ax.plot(xs, ys, ls=ls, marker=mk, color=color, linewidth=2, markersize=6,
                    markerfacecolor=(color if model == "sonnet" else "white"), markeredgecolor=color,
                    label=lab if metric == "accuracy" else None, zorder=3)
            for (t, _), r in zip(H_TIERS, rows):
                rec.append([sys_, model, t, metric, fv(r), int(r["numerator"]), int(r["denominator"])])
        ax.set_xticks(xs)
        ax.set_xticklabels([lab for _, lab in H_TIERS])
        ax.set_ylabel(ylab)
        ax.set_title(("Accuracy" if metric == "accuracy" else "Silent-error") + " by tier (Sonnet solid, Haiku dashed)",
                     fontsize=10.5, fontweight="bold")
        ax.set_ylim(-4, 104)
    axa.legend(frameon=False, fontsize=8.2, loc="lower left")
    fig.suptitle("Model robustness under a weaker emitter: risk, GDC vs planner, Sonnet vs Haiku",
                 fontweight="bold", fontsize=11.5)
    fig.subplots_adjust(bottom=0.12, top=0.86, wspace=0.18)
    footnote(fig, f"source: {nums.label}  ·  comparator {versions[0]} (strict)  ·  " + "; ".join(sorted(sources)))
    save(fig, out, file)
    write_slice(out, f"{file}_risk", ["system", "model", "tier", "metric", "value_pct", "numerator", "denominator"], rec)


# ════════════════════════════ tables (markdown + latex) ══════════════════════
def write_tables(nums, out, versions, risk, risk_group):
    cap = f"Source: reports/paper\\_numbers.csv; comparator {versions[0]} (strict)."
    order = spider_order(nums)

    def emit_table(name, headers, rows, caption, label, align=None):
        align = align or ("l" + "r" * (len(headers) - 1))
        md = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
        md += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
        (out / f"{name}.md").write_text("\n".join(md) + f"\n\n*{caption}*\n")
        tex = [r"\begin{table}[t]", r"\centering", f"\\caption{{{caption}}}", f"\\label{{{label}}}",
               f"\\begin{{tabular}}{{{align}}}", r"\toprule",
               " & ".join(h.replace("%", r"\%").replace("_", r"\_") for h in headers) + r" \\", r"\midrule"]
        for r in rows:
            tex.append(" & ".join(str(c).replace("%", r"\%").replace("_", r"\_") for c in r) + r" \\")
        tex += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
        (out / f"{name}.tex").write_text("\n".join(tex) + "\n")

    # Table 1: per-tier accuracy / silent-error, five systems
    syn = [("T1_single_op", "T1"), ("T2_two_ops", "T2"), ("T3_three_ops", "T3"),
           ("T4_business_rule_multi_op", "T4"), ("T5_high_complexity", "T5"), ("house_vocab", "house-vocab")]
    tier_rows = {"risk": syn + [("algebra_boundary", "algebra-boundary")],
                 "employee": syn + [("out_of_range", "out-of-range")]}
    flat = []
    for dom in (("risk", "employee") if risk else ("employee",)):
        rows_md = []
        for tg, tlab in tier_rows[dom]:
            cells = [tlab]
            for s in SYS_ALL:
                a = nums.maybe(fig_table="Table1", domain=dom, system=s, tier_group=tg, scoring="strict", metric="accuracy")
                sw = nums.maybe(fig_table="Table1", domain=dom, system=s, tier_group=tg, scoring="strict", metric="silent_wrong")
                if a and a["denominator"] not in ("0", "", None):
                    cells.append(f"{a['value_pct']} / {sw['value_pct'] if sw else '-'}")
                else:
                    cells.append("--")
                flat.append([dom, tg, s, a["value_pct"] if a else None, sw["value_pct"] if sw else None,
                             a["numerator"] if a else 0, a["denominator"] if a else 0])
            rows_md.append(cells)
        emit_table(f"table1_{dom}", ["tier"] + [LABELS[s] for s in SYS_ALL], rows_md,
                   f"{dom} per-tier accuracy \\% / silent-error \\%, five systems. {cap}", f"tab:table1_{dom}")
    write_slice(out, "table1_synthetic" + ("_risk" if risk else ""),
                ["domain", "tier", "system", "accuracy_pct", "silent_wrong_pct", "numerator", "denominator"], flat)

    # Table 2: cross-domain in-scope summary, GDC vs the strongest baseline
    t2 = []
    doms = ([("risk", "Table2", risk_group)] if risk else []) + \
        [("employee", "Table2", "in_contract_answer"), ("spider_pooled", "Fig1/Fig2/Table3", "in_contract_answer")]
    for dom, ft, group in doms:
        sel = dict(fig_table=ft, domain=dom, tier_group=group, scoring="strict")
        na, _, nd = nums.val(system="network", metric="accuracy", **sel)
        ns = nums.val(system="network", metric="silent_wrong", **sel)[0]
        best = None
        for s in SYS_ALL:
            if s == "network":
                continue
            r = nums.maybe(system=s, metric="accuracy", **sel)
            if r and r["value_pct"] not in (None, "") and (best is None or float(r["value_pct"]) > best[1]):
                best = (s, float(r["value_pct"]))
        bs = nums.val(system=best[0], metric="silent_wrong", **sel)[0]
        domlab = {"risk": "risk", "employee": "employee", "spider_pooled": "Spider"}[dom] + f" ({nd})"
        t2.append([domlab, f"{na}", f"{ns}", f"{LABELS[best[0]]} ({best[1]} / {bs})"])
    emit_table("table2_crossdomain", ["domain (cells)", "GDC acc %", "GDC silent %", "strongest baseline (acc / silent)"],
               t2, f"Cross-domain {SCOPE}: GDC vs the strongest baseline per domain. {cap}", "tab:crossdomain")

    # Table 3: Spider in-scope, five systems, strict and tolerant
    t3 = []
    for s in order:
        a = nums.val(fig_table="Fig1/Fig2/Table3", system=s, metric="accuracy", scoring="strict")
        ss = nums.val(fig_table="Fig1/Fig2/Table3", system=s, metric="silent_wrong", scoring="strict")[0]
        st = nums.val(fig_table="Fig1/Fig2/Table3", system=s, metric="silent_wrong", scoring="tolerant")[0]
        t3.append([LABELS[s], f"{a[0]}", f"{a[1]}/{a[2]}", f"{ss}", f"{st}"])
    emit_table("table3_spider_incontract",
               ["system", "accuracy %", "n/N", "silent-error % (strict)", "silent-error % (tolerant)"], t3,
               f"Spider {SCOPE}, five systems: accuracy and silent-error (strict {versions[0]} and tie-tolerant "
               f"{versions[1]}). {cap}", "tab:spider")

    if not risk:
        return
    # Table 4: risk accuracy vs cost; Table 5: Haiku per-tier
    pts = cost_frontier_points(nums, risk_group)
    t4 = [[LABELS[s], p["acc"], f"{p['cost']:.5f}", f"{p['tokens']:.0f}", f"{p['calls']:g}"] for s, p in pts.items()]
    emit_table("table4_cost_frontier", ["system", "accuracy %", "mean \\$/query", "mean tokens/query",
                                        "mean LLM calls/query"], t4,
               f"Accuracy vs cost, risk {SCOPE} ({RISK_GROUPS[risk_group]}, Sonnet). {cap}", "tab:cost")

    def hk(model, system, tier, metric):
        r = nums.row(haiku_panel(model, system, tier, metric))
        return r["value_pct"]
    t5 = [[tl] + [f"{hk(m, s, tg, 'accuracy')} / {hk(m, s, tg, 'silent_wrong')}"
                  for s, m in (("network", "sonnet"), ("network", "haiku"), ("planner", "sonnet"), ("planner", "haiku"))]
          for tg, tl in H_TIERS]
    emit_table("table5_haiku_robustness", ["tier", "GDC Sonnet", "GDC Haiku", "plan Sonnet", "plan Haiku"], t5,
               f"Model robustness (risk, acc\\%/silent\\%): GDC and planner emissions replayed on the current "
               f"engine. {cap}", "tab:haiku")


# ════════════════════════════ main ═══════════════════════════════════════════
PAPER_FIGURES = [fig4_tolerant, fig5_accuracy, fig6_strict_silent, fig7_s40]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--csv", type=Path, default=ROOT / "reports" / "paper_numbers.csv")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "figures")
    ap.add_argument("--risk-group", choices=sorted(RISK_GROUPS), default=RISK_GROUP_DEFAULT,
                    help="risk in-scope set Figure 8 and Tables 2/4 plot (default: %(default)s)")
    ap.add_argument("--no-extras", action="store_true", help="skip the figures the paper does not use")
    ap.add_argument("--no-tables", action="store_true")
    ap.add_argument("--no-risk", action="store_true", help="skip every risk figure and table")
    a = ap.parse_args(argv)

    nums = Numbers(a.csv)
    a.out.mkdir(parents=True, exist_ok=True)
    versions = nums.versions()
    risk = nums.has_domain("risk") and not a.no_risk
    man = Manifest(nums)
    plt.rcParams.update(BASE_RC)

    for f in PAPER_FIGURES:
        f(nums, man, a.out)
    if risk:
        fig8_cost_frontier(nums, man, a.out, a.risk_group)
    else:
        print("risk rows absent or --no-risk: Figure 8 not drawn")
    if not a.no_extras:
        extra_combined(nums, man, a.out, versions)
        extra_network_tiers(nums, man, a.out, versions)
        extra_strict_vs_tolerant(nums, man, a.out, versions)
        if risk:
            extra_haiku(nums, man, a.out, versions)
    man.write(a.out)
    if not a.no_tables:
        write_tables(nums, a.out, versions, risk, a.risk_group)
    print(f"from {a.csv} (comparators {versions[0]}/{versions[1]}) into {a.out}:")
    for p in sorted(a.out.glob("*")):
        if p.suffix in (".png", ".csv", ".md", ".tex") and p.name != "README.md":
            print("  ", p.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
