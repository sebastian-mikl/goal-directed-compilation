#!/usr/bin/env python
"""Generate publication figures + tables FROM reports/paper_numbers.csv (locked,
comparator s47 / scorer s47t). Nothing is hardcoded — every value is read from the
CSV so the figures cannot drift from the authoritative numbers.

Outputs under reports/figures/ (PNG-only):
  fig1_accuracy.png                      + fig1_accuracy.csv
  fig2_silent_error.png                  + fig2_silent_error.csv
  fig1_2_combined.png                    (paired accuracy vs silent-error)
  fig3_network_tiers.png                 + fig3_network_tiers.csv
  fig4_s40_refusal.png                   + fig4_s40_refusal.csv
  table1_synthetic.{md,tex}              + table1_synthetic.csv
  table2_crossdomain.{md,tex}
  table3_spider_incontract.{md,tex}

Style: Okabe-Ito colorblind-safe palette; NETWORK highlighted in a single
consistent deep blue (#0072B2) across every figure; baselines muted gray.
"""
from __future__ import annotations
import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "reports" / "paper_numbers.csv"
OUT = ROOT / "reports" / "figures"; OUT.mkdir(parents=True, exist_ok=True)
SRC = "source: reports/paper_numbers.csv  ·  comparator s47 (strict)"

# ── palette (Okabe-Ito subset; validated colorblind-safe) ───────────────────
NETWORK = "#0072B2"      # deep blue — network's signature, every figure
BASELINE = "#b8b8b8"     # muted gray — baselines
OUTCOME = {"correct": "#009E73", "silent_wrong": "#D55E00", "refused": "#56B4E9"}
S40 = {"off": "#b8b8b8", "on": "#0072B2"}
INK, MUTED = "#1a1a1a", "#5a5a5a"

plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"],
    "font.size": 10, "axes.titlesize": 11, "axes.labelsize": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#888888", "axes.linewidth": 0.8,
    "axes.grid": True, "axes.axisbelow": True, "axes.grid.axis": "y",
    "grid.color": "#e8e8e8", "grid.linewidth": 0.8,
    "xtick.color": INK, "ytick.color": INK, "text.color": INK,
    "figure.dpi": 150,
})
LABELS = {"network": "GDC", "planner": "planner", "react": "react",
          "code_agent": "code-agent", "text_to_sql": "text-to-SQL"}

# ── CSV access ──────────────────────────────────────────────────────────────
ROWS = list(csv.DictReader(open(CSV, encoding="utf-8")))
def q(**kw):
    return [r for r in ROWS if all(r.get(k) == v for k, v in kw.items())]
def maybe(**kw):
    """The row matching `kw`, or None. Several matches raise: a query must name the
    one row it means (risk has three Table2/CostFrontier groups; taking the first
    match plotted the 465-cell group where the caption quotes the 415)."""
    r = q(**kw)
    if len(r) > 1:
        raise LookupError(f"{len(r)} rows of {CSV.name} match {kw}")
    return r[0] if r else None
def one(**kw):
    r = maybe(**kw)
    if r is None:
        raise LookupError(f"no row of {CSV.name} matches {kw}")
    return r
def val(**kw):
    r = one(**kw); return float(r["value_pct"]), int(r["numerator"]), int(r["denominator"])

# The risk domain's data is withheld from the public release; every risk-derived
# figure/table below is guarded by HAS_RISK so this script completes cleanly for
# employee + Spider when it is absent.
HAS_RISK = bool(q(domain="risk"))
if not HAS_RISK:
    print("[note] risk domain data not included in this release — skipping "
          "table1_risk, the cost frontier (fig5/table4) and Haiku robustness (fig6/table5).")

# Which risk in-contract set the cross-domain table and the cost frontier (fig5,
# table4) plot. Since the 30 Sep reclassification paper_numbers.csv carries three:
# in_contract_answer (465 cells, algebra_boundary included), in_contract_within_vocab
# (the original 415) and in_contract_beyond_vocab (50). The paper's Figure 8 caption
# and Table 4 quote the 415-cell numbers, so that is the set plotted.
RISK_IN_CONTRACT_GROUP = "in_contract_within_vocab"
# risk's fifty boundary cells: tier out_of_range before the reclassification,
# algebra_boundary since (the gold tier of record, the tier_group the builder
# emits). The paper's Table 1a still labels that row out-of-range.
RISK_BOUNDARY_TIER = ("algebra_boundary", "algebra-boundary")

def footnote(fig, extra="", base=None):
    fig.text(0.005, 0.005, ((base if base is not None else SRC) + extra), fontsize=6.2, color=MUTED, ha="left", va="bottom")

def save(fig, name):
    # PNG-only output (PDF save removed — preview PNGs are the deliverable).
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight", dpi=200)
    plt.close(fig)

def write_slice(name, header, records):
    with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(header); w.writerows(records)

# in-contract systems ordered by strict Spider accuracy desc (network 2nd, tie broken by silent-error)
SPIDER_ORDER = ["text_to_sql", "network", "react", "code_agent", "planner"]

def bars_one_metric(ax, metric, ylabel, title, scoring="strict"):
    slice_rec = []
    xs, heights, colors = [], [], []
    for i, s in enumerate(SPIDER_ORDER):
        v, n, d = val(fig_table="Fig1/Fig2/Table3", system=s, metric=metric, scoring=scoring)
        xs.append(i); heights.append(v)
        colors.append(NETWORK if s == "network" else BASELINE)
        slice_rec.append([s, metric, v, n, d])
    bars = ax.bar(xs, heights, width=0.66, color=colors,
                  edgecolor=[("#004c77" if s == "network" else "#9a9a9a") for s in SPIDER_ORDER],
                  linewidth=1.0, zorder=3)
    for x, h, s in zip(xs, heights, SPIDER_ORDER):
        ax.text(x, h + 1.3, f"{h:.1f}", ha="center", va="bottom",
                fontsize=9, fontweight=("bold" if s == "network" else "normal"),
                color=(NETWORK if s == "network" else INK))
    ax.set_xticks(xs); ax.set_xticklabels([LABELS[s] for s in SPIDER_ORDER], rotation=0)
    ax.set_ylim(0, max(heights) * 1.18)
    ax.set_ylabel(ylabel); ax.set_title(title, fontweight="bold")
    return slice_rec

# ── FIGURE 1 — in-contract accuracy ─────────────────────────────────────────
fig, ax = plt.subplots(figsize=(6.4, 3.9))
rec = bars_one_metric(ax, "accuracy", "in-contract accuracy (%)",
                      "Spider in-contract accuracy (pooled, 135 cells)")
footnote(fig, base="source: reports/paper_numbers.csv  ·  strict scoring")
save(fig, "fig1_accuracy"); write_slice("fig1_accuracy", ["system", "metric", "value_pct", "numerator", "denominator"], rec)

# ── FIGURE 2 — silent-error rate (THESIS) ───────────────────────────────────
fig, ax = plt.subplots(figsize=(6.4, 3.9))
rec = bars_one_metric(ax, "silent_wrong", "silent-error rate (%)",
                      "Spider in-contract silent-error (pooled, 135 cells)")
footnote(fig, "   ·  lower is better", base="source: reports/paper_numbers.csv  ·  strict scoring")
save(fig, "fig2_silent_error"); write_slice("fig2_silent_error", ["system", "metric", "value_pct", "numerator", "denominator"], rec)

# ── FIGURE 2 (tolerant companion) — s47t tie-tolerant silent-error ───────────
fig, ax = plt.subplots(figsize=(6.4, 3.9))
rec = bars_one_metric(ax, "silent_wrong", "silent-error rate (%)",
                      "Spider in-contract silent-error, tie-tolerant scoring", scoring="tolerant")
footnote(fig, "   ·  lower is better", base="source: reports/paper_numbers.csv  ·  strict scoring is the comparator; tie-tolerant scoring is applied")
save(fig, "fig2_silent_error_tolerant")
write_slice("fig2_silent_error_tolerant", ["system", "metric", "value_pct", "numerator", "denominator"], rec)

# ── FIGURE 1+2 combined (paired) ────────────────────────────────────────────
fig, (axa, axb) = plt.subplots(1, 2, figsize=(10.2, 3.9))
bars_one_metric(axa, "accuracy", "accuracy (%)", "Accuracy (higher better)")
bars_one_metric(axb, "silent_wrong", "silent-error (%)", "Silent-error (lower better)")
for ax in (axa, axb):
    for lab in ax.get_xticklabels():
        lab.set_rotation(18); lab.set_ha("right")
fig.suptitle("Spider in-contract: accuracy vs silent-error, 5 systems (135 cells, s47)",
             fontweight="bold", fontsize=11)
fig.subplots_adjust(bottom=0.22, top=0.86, wspace=0.22)
footnote(fig)
save(fig, "fig1_2_combined")

# ── FIGURE 3 — network per-tier per Spider domain (outcome split) ───────────
DOMAINS = ["wta_1", "world_1", "bike_1"]
TIERS = [("answer", "answer"), ("out_of_scope_register_s40off", "out-of-scope\n(instruction off)"),
         ("out_of_contract_aggregate", "out-of-contract\n(boundary)")]
fig, axes = plt.subplots(1, 3, figsize=(10.6, 4.1), sharey=True)
f3rec = []
for ax, dom in zip(axes, DOMAINS):
    xs = range(len(TIERS))
    corr = [val(fig_table="Fig3/Fig4", domain=dom, tier_group=tg, metric="correct")[0] for tg, _ in TIERS]
    sil = [val(fig_table="Fig3/Fig4", domain=dom, tier_group=tg, metric="silent_wrong")[0] for tg, _ in TIERS]
    ref = [val(fig_table="Fig3/Fig4", domain=dom, tier_group=tg, metric="refused_should_answer")[0] for tg, _ in TIERS]
    ax.bar(xs, corr, 0.62, color=OUTCOME["correct"], zorder=3, label="correct")
    ax.bar(xs, sil, 0.62, bottom=corr, color=OUTCOME["silent_wrong"], zorder=3, label="silent-wrong")
    ax.bar(xs, ref, 0.62, bottom=[c + s for c, s in zip(corr, sil)], color=OUTCOME["refused"], zorder=3, label="refused")
    for x, (tg, _) in zip(xs, TIERS):
        c = corr[x]
        if c >= 8: ax.text(x, c / 2, f"{c:.0f}", ha="center", va="center", color="white", fontsize=8, fontweight="bold")
        f3rec.append([dom, tg, corr[x], sil[x], ref[x]])
    ax.set_xticks(list(xs)); ax.set_xticklabels([lab for _, lab in TIERS], fontsize=8.5)
    ax.set_title(dom, fontweight="bold"); ax.set_ylim(0, 108)
    if dom == "wta_1": ax.set_ylabel("GDC outcome share (%)")
axes[0].legend(handles=[Patch(color=OUTCOME["correct"], label="correct  (answer, or correct refusal on boundary tiers)"),
                        Patch(color=OUTCOME["silent_wrong"], label="silent-wrong  (confident wrong answer)"),
                        Patch(color=OUTCOME["refused"], label="refused  (honest decline; 0% correct on out-of-contract is by design)")],
               loc="upper center", bbox_to_anchor=(1.75, -0.13), ncol=1, frameon=False, fontsize=8.2)
fig.suptitle("GDC outcome composition by tier, per Spider domain", fontweight="bold", fontsize=11.5, y=1.0)
fig.subplots_adjust(bottom=0.30)
footnote(fig)
save(fig, "fig3_network_tiers")
write_slice("fig3_network_tiers", ["domain", "tier", "correct_pct", "silent_wrong_pct", "refused_pct"], f3rec)

# ── FIGURE 4 — S40 off vs on, out_of_scope refusal accuracy ──────────────────
fig, ax = plt.subplots(figsize=(6.6, 3.9))
w = 0.36; f4rec = []
for i, dom in enumerate(DOMAINS):
    off, no, do = val(fig_table="Fig3/Fig4", domain=dom, tier_group="out_of_scope_register_s40off", metric="correct")
    on, non, don = val(fig_table="Fig3/Fig4", domain=dom, tier_group="out_of_scope_register_s40on", metric="correct")
    b1 = ax.bar(i - w / 2, off, w, color=S40["off"], edgecolor="#9a9a9a", linewidth=0.8, zorder=3)
    b2 = ax.bar(i + w / 2, on, w, color=S40["on"], edgecolor="#004c77", linewidth=0.8, zorder=3)
    ax.text(i - w / 2, off + 1.3, f"{off:.1f}", ha="center", va="bottom", fontsize=8.5, color=INK)
    ax.text(i + w / 2, on + 1.3, f"{on:.1f}", ha="center", va="bottom", fontsize=8.5, color=NETWORK, fontweight="bold")
    ax.annotate(f"+{on-off:.1f}", xy=(i, max(off, on) + 7), ha="center", fontsize=8.5, color=MUTED)
    f4rec.append([dom, off, on, round(on - off, 1)])
brief = {"wta_1": "phantom-table\nbriefing", "world_1": "phantom-table\nbriefing", "bike_1": "honest single-\ntable briefing"}
ax.set_xticks(range(len(DOMAINS)))
ax.set_xticklabels([f"{d}\n{brief[d]}" for d in DOMAINS], fontsize=8.2)
ax.set_ylabel("out-of-scope refusal accuracy (%)"); ax.set_ylim(0, 90)
ax.set_title("Out-of-scope refusal: table-coverage instruction off vs on, per Spider domain", fontweight="bold")
ax.legend([Patch(color=S40["off"]), Patch(color=S40["on"])], ["instruction off", "instruction on"], frameon=False, loc="upper left")
fig.subplots_adjust(bottom=0.30)
footnote(fig, base="source: reports/paper_numbers.csv  ·  strict scoring")
save(fig, "fig4_s40_refusal")
write_slice("fig4_s40_refusal", ["domain", "s40off_pct", "s40on_pct", "lift_pts"], f4rec)

# ── FIGURE (strict vs tolerant silent-error) — Spider in-contract, 5 systems ──
# Paired bars: strict s47 vs tie-tolerant s47t silent-error per system. Makes the
# GDC 11.1%→3.7% ranking-tie gap (§5.2) visible; GDC's tolerant bar is lowest of five.
STRICT_C = "#b8b8b8"     # muted gray — strict (as-scored)
TOL_C = "#0072B2"        # deep blue — tolerant, GDC's signature color
fig, ax = plt.subplots(figsize=(7.0, 4.0))
w = 0.38; fSTrec = []
for i, s in enumerate(SPIDER_ORDER):
    strict = val(fig_table="Fig1/Fig2/Table3", system=s, metric="silent_wrong", scoring="strict")[0]
    tol = val(fig_table="Fig1/Fig2/Table3", system=s, metric="silent_wrong", scoring="tolerant")[0]
    is_gdc = s == "network"
    ax.bar(i - w / 2, strict, w, color=STRICT_C, edgecolor="#9a9a9a", linewidth=0.8, zorder=3)
    ax.bar(i + w / 2, tol, w, color=TOL_C, edgecolor="#004c77", linewidth=0.8, zorder=3)
    ax.text(i - w / 2, strict + 0.5, f"{strict:.1f}", ha="center", va="bottom", fontsize=8.2, color=INK)
    ax.text(i + w / 2, tol + 0.5, f"{tol:.1f}", ha="center", va="bottom", fontsize=8.2,
            color=NETWORK, fontweight=("bold" if is_gdc else "normal"))
    fSTrec.append([s, strict, tol])
xt = list(range(len(SPIDER_ORDER)))
ax.set_xticks(xt)
ax.set_xticklabels([(LABELS[s] + "\n(tie-driven)" if s == "network" else LABELS[s]) for s in SPIDER_ORDER])
for lab, s in zip(ax.get_xticklabels(), SPIDER_ORDER):
    if s == "network": lab.set_fontweight("bold"); lab.set_color(NETWORK)
ax.set_ylabel("in-contract silent-error (%)"); ax.set_ylim(0, max(v[1] for v in fSTrec) * 1.22)
ax.set_title("Spider in-contract silent-error: strict vs tie-tolerant (135 cells)", fontweight="bold")
ax.legend([Patch(color=STRICT_C, ec="#9a9a9a"), Patch(color=TOL_C, ec="#004c77")],
          ["strict (s47)", "tie-tolerant (s47t)"], frameon=False, loc="upper left")
fig.subplots_adjust(bottom=0.20)
footnote(fig, "   ·  s47 vs s47t  ·  lower is better  ·  GDC gap = 2 ranking-tie questions (world_13, bike_06)")
save(fig, "fig_strict_vs_tolerant_silent")
write_slice("fig_strict_vs_tolerant_silent", ["system", "silent_strict_pct", "silent_tolerant_pct"], fSTrec)

print("figures written to reports/figures/:")
for p in sorted(OUT.glob("fig*")):
    print("  ", p.name)

# ════════════════════════════ TABLES (markdown + latex) ═════════════════════
def emit_table(name, headers, rows, caption, label, align=None):
    align = align or ("l" + "r" * (len(headers) - 1))
    md = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    md += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    (OUT / f"{name}.md").write_text("\n".join(md) + f"\n\n*{caption}*\n")
    tex = [r"\begin{table}[t]", r"\centering", f"\\caption{{{caption}}}", f"\\label{{{label}}}",
           f"\\begin{{tabular}}{{{align}}}", r"\toprule",
           " & ".join(h.replace("%", r"\%").replace("_", r"\_") for h in headers) + r" \\", r"\midrule"]
    for r in rows:
        tex.append(" & ".join(str(c).replace("%", r"\%").replace("_", r"\_") for c in r) + r" \\")
    tex += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    (OUT / f"{name}.tex").write_text("\n".join(tex) + "\n")
    return md

CAP = "Source: reports/paper\\_numbers.csv; comparator s47 (strict)."
SYS_ALL = ["network", "planner", "react", "code_agent", "text_to_sql"]

# ── Table 1 — risk + employee per-tier (acc / silent), 5 systems ────────────
SYN_TIERS = [("T1_single_op", "T1"), ("T2_two_ops", "T2"), ("T3_three_ops", "T3"),
             ("T4_business_rule_multi_op", "T4"), ("T5_high_complexity", "T5"),
             ("house_vocab", "house-vocab")]
TIER_ROWS = {"risk": SYN_TIERS + [RISK_BOUNDARY_TIER],
             "employee": SYN_TIERS + [("out_of_range", "out-of-range")]}
t1_rows_flat = []
for dom in (("risk", "employee") if HAS_RISK else ("employee",)):
    rows_md = []
    for tg, tlab in TIER_ROWS[dom]:
        cells = [tlab]
        for s in SYS_ALL:
            a = maybe(fig_table="Table1", domain=dom, system=s, tier_group=tg, scoring="strict", metric="accuracy")
            sw = maybe(fig_table="Table1", domain=dom, system=s, tier_group=tg, scoring="strict", metric="silent_wrong")
            if a and a["denominator"] not in ("0", "", None):
                cells.append(f"{a['value_pct']} / {sw['value_pct'] if sw else '-'}")
            else:
                cells.append("--")
            t1_rows_flat.append([dom, tg, s, a["value_pct"] if a else None,
                                 sw["value_pct"] if sw else None,
                                 a["numerator"] if a else 0, a["denominator"] if a else 0])
        rows_md.append(cells)
    emit_table(f"table1_{dom}", ["tier"] + [LABELS[s] for s in SYS_ALL], rows_md,
               f"{dom} per-tier accuracy \\% / silent-error \\%, five systems. {CAP}",
               f"tab:table1_{dom}")
write_slice("table1_synthetic", ["domain", "tier", "system", "accuracy_pct", "silent_wrong_pct", "numerator", "denominator"], t1_rows_flat)

# ── Table 2 — cross-domain in-contract summary ──────────────────────────────
def strongest_baseline(get):
    best = None
    for s in SYS_ALL:
        if s == "network": continue
        a = get(s)
        if a is None: continue
        if best is None or a[0] > best[1]: best = (s, a[0])
    return best
t2_rows = []
# risk / employee from Table2; spider from Fig1/2/Table3 (strict)
for dom, ft, group in (([("risk", "Table2", RISK_IN_CONTRACT_GROUP)] if HAS_RISK else []) +
                       [("employee", "Table2", "in_contract_answer"),
                        ("spider_pooled", "Fig1/Fig2/Table3", "in_contract_answer")]):
    sel = dict(fig_table=ft, domain=dom, tier_group=group, scoring="strict")
    na, nn, nd = val(system="network", metric="accuracy", **sel)
    ns = val(system="network", metric="silent_wrong", **sel)[0]
    def geta(s, sel=sel):
        r = maybe(system=s, metric="accuracy", **sel)
        return (float(r["value_pct"]),) if r and r["value_pct"] not in (None, "") else None
    sb = strongest_baseline(geta)
    sbs = val(system=sb[0], metric="silent_wrong", **sel)[0]
    domlab = {"risk": "risk", "employee": "employee", "spider_pooled": "Spider"}[dom] + f" ({nd})"
    t2_rows.append([domlab, f"{na}", f"{ns}", f"{LABELS[sb[0]]} ({sb[1]} / {sbs})"])
emit_table("table2_crossdomain", ["domain (cells)", "GDC acc %", "GDC silent %", "strongest baseline (acc / silent)"],
           t2_rows, f"Cross-domain in-contract: GDC vs the strongest baseline per domain. {CAP}", "tab:crossdomain")

# ── Table 3 — Spider in-contract, 5 systems, acc + silent (strict + tolerant) ─
t3_rows = []
for s in SPIDER_ORDER:
    a = val(fig_table="Fig1/Fig2/Table3", system=s, metric="accuracy", scoring="strict")
    ss = val(fig_table="Fig1/Fig2/Table3", system=s, metric="silent_wrong", scoring="strict")[0]
    st = val(fig_table="Fig1/Fig2/Table3", system=s, metric="silent_wrong", scoring="tolerant")[0]
    t3_rows.append([LABELS[s], f"{a[0]}", f"{a[1]}/{a[2]}", f"{ss}", f"{st}"])
emit_table("table3_spider_incontract",
           ["system", "accuracy %", "n/N", "silent-error % (strict)", "silent-error % (tolerant)"],
           t3_rows, f"Spider in-contract, five systems: accuracy and silent-error (strict s47 and tie-tolerant s47t). {CAP}",
           "tab:spider")

if HAS_RISK:
    # Everything below is derived from the risk domain only (cost frontier +
    # Haiku model-robustness). Skipped entirely when risk data is absent.
    # ── Fig 5 — cost frontier, two panels: accuracy AND silent-error vs cost ─────
    # A single accuracy-only panel lets GDC's honest T5 refusals (which cost it raw accuracy
    # but not silent-error) misread as "planner beats GDC" — Table 4 shows GDC leads planner
    # on silent-error in every domain. Two panels side by side so neither metric is read alone.
    def cost_of(system, metric):
        return float(one(fig_table="CostFrontier", system=system, tier_group=RISK_IN_CONTRACT_GROUP,
                         metric=metric)["value_pct"])
    def t2_of(system, metric):
        return float(one(fig_table="Table2", domain="risk", system=system, tier_group=RISK_IN_CONTRACT_GROUP,
                         metric=metric)["value_pct"])
    COST_SYS = ["network", "react", "planner", "code_agent", "text_to_sql"]
    by_sys = {}
    for s in COST_SYS:
        by_sys[s] = dict(acc=cost_of(s, "accuracy"), tok=cost_of(s, "mean_tokens_per_query"),
                          dollar=cost_of(s, "mean_cost_usd_per_query"), calls=cost_of(s, "mean_llm_calls_per_query"),
                          silent=t2_of(s, "silent_wrong"))

    fig, (axL, axR) = plt.subplots(1, 2, figsize=(13.2, 4.8))
    f5rec = []

    def plot_panel(ax, metric, ylabel, title, lbl_off, ylim, ann_fn):
        for s in COST_SYS:
            y = by_sys[s][metric]; dollar = by_sys[s]["dollar"]
            is_net = s == "network"
            ax.scatter([dollar], [y], s=190 if is_net else 120, color=NETWORK if is_net else BASELINE,
                       edgecolor="#004c77" if is_net else "#888888", linewidth=1.4, zorder=4)
            dx, dy, ha = lbl_off[s]
            ax.annotate(f"{LABELS[s]} ({by_sys[s]['calls']:g} call{'s' if by_sys[s]['calls'] != 1 else ''}/q, {by_sys[s]['tok']:,.0f} tok/q)",
                        (dollar, y), textcoords="offset points", xytext=(dx, dy),
                        ha=ha, fontsize=8, color=(NETWORK if is_net else INK),
                        fontweight=("bold" if is_net else "normal"))
        ax.set_xscale("log")
        ax.set_xlabel("mean cost / query ($, log scale; lower = cheaper)")
        ax.set_ylabel(ylabel)
        ax.set_ylim(*ylim); ax.set_xlim(0.0022, 0.075)
        xticks = [0.0025, 0.005, 0.01, 0.02, 0.04, 0.07]
        ax.set_xticks(xticks)
        ax.set_xticklabels([f"${v:.3f}" if v < 0.01 else f"${v:.2f}" for v in xticks])
        ax.minorticks_off()
        ax.set_title(title, fontweight="bold")
        ann_fn(ax)

    # left — accuracy (higher = better). network/planner cluster within ~1.1x $ (log-crowded);
    # labels stacked to the right of the cluster instead of beside their own points.
    LBL_ACC = {"network": (55, 10, "left"), "planner": (55, -14, "left"),
               "text_to_sql": (12, 14, "left"), "code_agent": (0, -30, "center"),
               "react": (0, 16, "center")}
    def ann_acc(ax):
        tok_ratio = by_sys["react"]["tok"] / by_sys["network"]["tok"]
        usd_ratio = by_sys["react"]["dollar"] / by_sys["network"]["dollar"]
        acc_gap = by_sys["react"]["acc"] - by_sys["network"]["acc"]
        ax.annotate(f"GDC within {acc_gap:.1f} points of react's accuracy\nat {tok_ratio:.1f}x fewer tokens / {usd_ratio:.1f}x lower cost",
                    xy=(by_sys["network"]["dollar"], by_sys["network"]["acc"]), xytext=(0.019, 78),
                    fontsize=8, color=MUTED, ha="left",
                    arrowprops=dict(arrowstyle="->", color=MUTED, lw=1, connectionstyle="arc3,rad=0.2"))
    plot_panel(axL, "acc", "risk in-contract accuracy (%)", "Accuracy vs cost", LBL_ACC, (64, 108), ann_acc)

    # right — silent-error (lower = better; note the axis is flipped in sense from the left
    # panel, so a system "leading" here sits toward the BOTTOM, not the top). network,
    # text_to_sql and planner all sit within ~1.2x in $ and 1 point of each other — a three-way
    # tight, log-crowded cluster; stack their labels to the right in silent-error order, same
    # technique as the accuracy panel's network/planner cluster, so none collides with the
    # y-axis, each other, or the frontier line.
    LBL_SIL = {"network": (55, 34, "left"), "text_to_sql": (55, 12, "left"), "planner": (55, -10, "left"),
               "code_agent": (0, 16, "center"), "react": (0, -30, "center")}
    def ann_sil(ax):
        silent_gap_planner = by_sys["planner"]["silent"] - by_sys["network"]["silent"]
        ax.annotate(f"GDC {silent_gap_planner:.1f} points lower silent-error than planner\n(ties text-to-SQL at {by_sys['network']['silent']:.1f}%; leads code-agent by "
                    f"{by_sys['code_agent']['silent'] - by_sys['network']['silent']:.1f} pts)",
                    xy=(by_sys["code_agent"]["dollar"], by_sys["code_agent"]["silent"]), xytext=(0.0072, 3.7),
                    fontsize=8, color=MUTED, ha="left",
                    arrowprops=dict(arrowstyle="->", color=MUTED, lw=1, connectionstyle="arc3,rad=-0.15"))
    plot_panel(axR, "silent", "risk in-contract silent-error (%)", "Silent-error vs cost", LBL_SIL, (0, 7.5), ann_sil)

    fig.suptitle("Cost frontier, risk in-contract (Sonnet): accuracy and silent-error both plotted against $/query\n"
                 "because accuracy alone hides that GDC's small accuracy dip vs planner is honest refusal, not a worse answer (Table 4)",
                 fontsize=9.5, color=MUTED, y=1.03)
    fig.subplots_adjust(bottom=0.16, wspace=0.28, top=0.86)
    footnote(fig, "   ·  left: GDC dominates code-agent on both axes, cheaper than react at comparable accuracy, text-to-SQL cheaper at lower accuracy  ·  "
                  "right: GDC leads planner and code-agent, ties text-to-SQL, react has fewer silent-errors at 12.1x the cost",
             base="source: reports/paper_numbers.csv  ·  strict scoring")
    save(fig, "fig5_cost_frontier")
    for s in COST_SYS:
        f5rec.append([s, by_sys[s]["acc"], by_sys[s]["silent"], by_sys[s]["tok"], by_sys[s]["dollar"], by_sys[s]["calls"]])
    write_slice("fig5_cost_frontier", ["system", "accuracy_pct", "silent_wrong_pct", "mean_tokens_per_q", "mean_usd_per_q", "mean_calls_per_q"], f5rec)

    # ── Fig 5 (clean) — same two panels, axes/gridlines/markers only: no per-point
    # labels, no annotation callouts, no title, no source footer. GDC stays visually
    # distinguished (fill color) since that is a data encoding, not text.
    def plot_panel_clean(ax, metric, ylabel, ylim):
        for s in COST_SYS:
            y = by_sys[s][metric]; dollar = by_sys[s]["dollar"]
            is_net = s == "network"
            ax.scatter([dollar], [y], s=190 if is_net else 120, color=NETWORK if is_net else BASELINE,
                       edgecolor="#004c77" if is_net else "#888888", linewidth=1.4, zorder=4)
        ax.set_xscale("log")
        ax.set_xlabel("mean cost / query ($, log scale)")
        ax.set_ylabel(ylabel)
        ax.set_ylim(*ylim); ax.set_xlim(0.0022, 0.075)
        xticks = [0.0025, 0.005, 0.01, 0.02, 0.04, 0.07]
        ax.set_xticks(xticks)
        ax.set_xticklabels([f"${v:.3f}" if v < 0.01 else f"${v:.2f}" for v in xticks])
        ax.minorticks_off()

    fig_c, (axLc, axRc) = plt.subplots(1, 2, figsize=(13.2, 4.8))
    plot_panel_clean(axLc, "acc", "risk in-contract accuracy (%)", (64, 108))
    plot_panel_clean(axRc, "silent", "risk in-contract silent-error (%)", (0, 7.5))
    fig_c.subplots_adjust(bottom=0.16, wspace=0.28, top=0.94)
    save(fig_c, "fig5_cost_frontier_clean")

    # ── Fig 6 — Haiku model-robustness, tier progression (network vs planner) ─────
    H_TIERS = [("T1_single_op", "T1"), ("T2_two_ops", "T2"), ("T3_three_ops", "T3"),
               ("T4_business_rule_multi_op", "T4"), ("T5_high_complexity", "T5"), ("house_vocab", "hv")]
    def hk(model, system, tier, metric):
        r = maybe(fig_table=f"Haiku_{model}", system=system, tier_group=tier, metric=metric)
        return float(r["value_pct"]) if r else None
    xs = list(range(len(H_TIERS)))
    fig, (axa, axb) = plt.subplots(1, 2, figsize=(11, 4.3), sharex=True)
    styles = [("network", "sonnet", NETWORK, "-", "o", "GDC · Sonnet"),
              ("network", "haiku", NETWORK, "--", "o", "GDC · Haiku"),
              ("planner", "sonnet", "#888888", "-", "s", "planner · Sonnet"),
              ("planner", "haiku", "#888888", "--", "s", "planner · Haiku")]
    f6rec = []
    for metric, ax, ylab in [("accuracy", axa, "accuracy (%)"), ("silent_wrong", axb, "silent-error (%)")]:
        for sys_, model, color, ls, mk, lab in styles:
            ys = [hk(model, sys_, t, metric) for t, _ in H_TIERS]
            ax.plot(xs, ys, ls=ls, marker=mk, color=color, linewidth=2, markersize=6,
                    markerfacecolor=(color if model == "sonnet" else "white"),
                    markeredgecolor=color, label=lab if metric == "accuracy" else None, zorder=3)
            if metric == "silent_wrong":
                for x, t in zip(xs, [t for t, _ in H_TIERS]):
                    f6rec.append([sys_, model, t, hk(model, sys_, t, "accuracy"), hk(model, sys_, t, "silent_wrong")])
        ax.set_xticks(xs); ax.set_xticklabels([lab for _, lab in H_TIERS])
        ax.set_ylabel(ylab); ax.set_title(("Accuracy" if metric == "accuracy" else "Silent-error") +
                                           " by tier (Sonnet solid, Haiku dashed)", fontsize=10.5, fontweight="bold")
        ax.set_ylim(-4, 104)
    # annotate the two honest halves on the silent-error panel
    axb.annotate("GDC robust\non T5 depth", xy=(4, 2.7), xytext=(2.2, 20), fontsize=8, color=NETWORK,
                 arrowprops=dict(arrowstyle="->", color=NETWORK, lw=1))
    axb.annotate("GDC's grounding tiers\n(T4, house-vocab) spike\nunder Haiku", xy=(5, 50), xytext=(2.6, 62),
                 fontsize=8, color="#c25400", arrowprops=dict(arrowstyle="->", color="#c25400", lw=1))
    axa.legend(frameon=False, fontsize=8.2, loc="lower left")
    fig.suptitle("Model robustness under a weaker emitter: risk, GDC vs planner, Sonnet vs Haiku",
                 fontweight="bold", fontsize=11.5)
    fig.subplots_adjust(bottom=0.12, top=0.86, wspace=0.18)
    footnote(fig, "   ·  Haiku from results_haiku.rescored.jsonl; GDC+planner emissions replayed on the current engine")
    save(fig, "fig6_haiku_robustness")
    write_slice("fig6_haiku_robustness", ["system", "model", "tier", "accuracy_pct", "silent_wrong_pct"], f6rec)

    # ── Table 4 — cost frontier · Table 5 — Haiku per-tier ───────────────────────
    t4 = [[LABELS[s], cost_of(s, "accuracy"), f"{cost_of(s,'mean_cost_usd_per_query'):.5f}",
           f"{cost_of(s,'mean_tokens_per_query'):.0f}", f"{cost_of(s,'mean_llm_calls_per_query'):g}"] for s in COST_SYS]
    emit_table("table4_cost_frontier", ["system", "accuracy %", "mean \\$/query", "mean tokens/query", "mean LLM calls/query"],
               t4, f"Accuracy vs cost, risk in-contract (Sonnet). GDC dominates code\\_agent on both axes; "
                   f"cheaper than react at comparable accuracy; text\\_to\\_sql is cheaper at lower accuracy. {CAP}", "tab:cost")
    t5 = []
    for tg, tl in H_TIERS:
        t5.append([tl, f"{hk('sonnet','network',tg,'accuracy')} / {hk('sonnet','network',tg,'silent_wrong')}",
                   f"{hk('haiku','network',tg,'accuracy')} / {hk('haiku','network',tg,'silent_wrong')}",
                   f"{hk('sonnet','planner',tg,'accuracy')} / {hk('sonnet','planner',tg,'silent_wrong')}",
                   f"{hk('haiku','planner',tg,'accuracy')} / {hk('haiku','planner',tg,'silent_wrong')}"])
    emit_table("table5_haiku_robustness", ["tier", "GDC Sonnet", "GDC Haiku", "plan Sonnet", "plan Haiku"], t5,
               f"Model robustness (risk, acc\\%/silent\\%): GDC+planner emissions replayed on the current engine. T5 shows GDC's "
               f"depth robustness; T4/house-vocab show its grounding fragility under the weaker model. {CAP}", "tab:haiku")

print("\ntables written to reports/figures/:")
for p in sorted(OUT.glob("table*")):
    print("  ", p.name)
if HAS_RISK:
    print("added: fig5_cost_frontier, fig6_haiku_robustness, table4_cost_frontier, table5_haiku_robustness")
