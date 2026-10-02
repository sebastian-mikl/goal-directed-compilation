#!/usr/bin/env python3
"""Render Figure 1 (system control-flow) to reports/figures/pipeline.png.

Source of record for the pipeline diagram. The previously committed
pipeline.png was a rasterised export from an external diagram tool with no
in-repo source, and had baked-in label defects (a truncated "Cras" for
"Crash" and several "Refuse:" boxes with dropped cause labels). This script
regenerates the figure deterministically from matplotlib so the labels are
exact and the source lives with the paper.

Run:  python scripts/make_pipeline_figure.py
Deps: matplotlib only.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, PathPatch
from matplotlib.path import Path

# palette (light, print-safe)
C_LLM = ("#ede7ff", "#7c4dff")
C_CORE = ("#d6f0ff", "#00a2e8")
C_REFUSE = ("#fff3d6", "#e8a000")
C_CRASH = ("#ffd6dc", "#e8003c")
C_DONE = ("#d6ffe0", "#00c853")
TXT = "#1a1a1a"

# node registry: name -> (x, y, w, h, kind, label)
# coordinates on an arbitrary grid; y grows downward (flipped at render).
N = {}


def node(name, x, y, w, h, kind, label):
    N[name] = dict(x=x, y=y, w=w, h=h, kind=kind, label=label)


# left column (main happy path descends)
node("nl",        0.0,  0.0, 3.0, 0.9, "stadium", "Natural-language query")
node("call1",     0.0,  1.6, 3.4, 1.1, "box",     "Call-1: single LLM call\nemit declarative JSON Goal + OrderSpec")
node("declined",  0.0,  3.4, 2.9, 1.8, "diamond", "Model declined?\nout-of-scope / unanswerable")
node("parse",     0.0,  5.9, 2.9, 1.8, "diamond", "JSON parses into\nwell-formed Goal?")
node("contract",  0.0,  8.4, 2.9, 1.9, "diamond", "Goal passes contract?\nat most one aggregate;\ngrouped grain iff exactly one")
node("order",     0.0, 11.0, 2.9, 1.9, "diamond", "Order valid?\nevery order-by column in Goal\nor derived columns")

# right column (search + execution)
node("lint",      7.0,  2.0, 2.9, 1.9, "diamond", "Goal lint passes?\nno dead derived column")
node("plan",      7.0,  4.6, 3.4, 1.5, "box",     "A* planner: search over 4 primitives\nfilter, select, aggregate, compute\nfind operator sequence satisfying Goal")
node("reach",     7.0,  6.9, 2.9, 1.7, "diamond", "Reachable plan found?")
node("exec",      7.0,  9.1, 3.4, 1.1, "box",     "Execute plan over data table\napply operators, then sort/limit")
node("execerr",   7.0, 11.0, 2.9, 1.7, "diamond", "Execution error?")

# terminals
node("r_explicit", -3.6, 3.7, 2.3, 0.85, "box_r", "Refuse: explicit")
node("r_parse",    -3.6, 6.2, 2.3, 0.85, "box_r", "Refuse: ParseError")
node("r_contract1",-3.6, 8.7, 2.3, 0.85, "box_r", "Refuse: ContractError")
node("r_contract2",-3.6, 11.3, 2.3, 0.85, "box_r","Refuse: ContractError")
node("r_lint",      4.0, 2.3, 2.3, 0.85, "box_r", "Refuse: GoalLint")
node("r_unreach",   4.0, 7.2, 2.3, 0.85, "box_r", "Refuse: Unreachable")

node("r_filter",    4.6, 13.9, 3.0, 0.85, "box_r", "Refuse:\nFilterTypeError")
node("r_orderapp",  8.2, 13.9, 3.2, 0.85, "box_r", "Refuse:\nOrderApplicationError")
node("crash",      11.8, 13.9, 1.9, 0.85, "crash", "Crash")
node("answer",     14.4, 13.9, 2.4, 0.9,  "stadium2", "Answer: result")

# edges: (src, dst, label, label_dx, label_dy)
E = [
    ("nl", "call1", "", 0, 0),
    ("call1", "declined", "", 0, 0),
    ("declined", "r_explicit", "YES", 0, 0.35),
    ("declined", "parse", "NO", 0.35, 0),
    ("parse", "r_parse", "NO", 0, 0.35),
    ("parse", "contract", "YES", 0.35, 0),
    ("contract", "r_contract1", "NO", 0, 0.35),
    ("contract", "order", "YES", 0.35, 0),
    ("order", "r_contract2", "NO", 0, 0.35),
    ("order", "lint", "YES", 0, 0),      # long cross link up-right
    ("lint", "r_lint", "NO", 0, 0.35),
    ("lint", "plan", "YES", 0.35, 0),
    ("plan", "reach", "", 0, 0),
    ("reach", "r_unreach", "NO", 0, 0.35),
    ("reach", "exec", "YES", 0.35, 0),
    ("exec", "execerr", "", 0, 0),
    ("execerr", "r_filter", "Type-mismatched filter", 0, 0),
    ("execerr", "r_orderapp", "Unorderable output", 0, 0),
    ("execerr", "crash", "Other exception", 0, 0),
    ("execerr", "answer", "No error", 0, 0),
]

# per-edge elbow height (fraction from src to dst) and label y, so the four
# fan-out edges from execerr don't overlap. keyed by (src, dst).
ELBOW = {
    ("execerr", "r_filter"): (0.30, 0.30),
    ("execerr", "r_orderapp"): (0.48, 0.48),
    ("execerr", "crash"): (0.66, 0.66),
    ("execerr", "answer"): (0.84, 0.84),
}


def edge_point(nd, side):
    x, y, w, h = nd["x"], nd["y"], nd["w"], nd["h"]
    if side == "bottom":
        return (x, y + h / 2)
    if side == "top":
        return (x, y - h / 2)
    if side == "left":
        return (x - w / 2, y)
    if side == "right":
        return (x + w / 2, y)
    return (x, y)


def pick_sides(src, dst):
    sx, sy = src["x"], src["y"]
    dx, dy = dst["x"], dst["y"]
    # prefer vertical if mostly below, else horizontal
    if abs(dy - sy) >= abs(dx - sx):
        s_side = "bottom" if dy > sy else "top"
        d_side = "top" if dy > sy else "bottom"
    else:
        s_side = "right" if dx > sx else "left"
        d_side = "left" if dx > sx else "right"
    return s_side, d_side


def draw():
    fig, ax = plt.subplots(figsize=(17, 15), dpi=200)
    ax.set_xlim(-6, 18)
    ax.set_ylim(0, 15.4)
    ax.invert_yaxis()
    ax.axis("off")

    kindmap = {
        "box": C_LLM, "core": C_CORE, "box_r": C_REFUSE,
        "crash": C_CRASH, "stadium": C_DONE, "stadium2": C_DONE,
        "diamond": ("#ffffff", "#7c4dff"),
    }
    # override core boxes
    N["plan"]["_col"] = C_CORE
    N["exec"]["_col"] = C_CORE

    def facecol(name, nd):
        if "_col" in nd:
            return nd["_col"]
        return kindmap.get(nd["kind"], ("#ffffff", "#333333"))

    # draw edges first (under nodes)
    for src_n, dst_n, label, ldx, ldy in E:
        s, d = N[src_n], N[dst_n]
        # fan-out edges from execerr: drop from bottom at a staggered elbow,
        # traverse horizontally, then drop into the target's top.
        if (src_n, dst_n) in ELBOW:
            frac, lfrac = ELBOW[(src_n, dst_n)]
            p0 = edge_point(s, "bottom")
            p1 = edge_point(d, "top")
            ey = p0[1] + frac * (p1[1] - p0[1])
            verts = [p0, (p0[0], ey), (p1[0], ey), p1]
            codes = [Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO]
            ax.add_patch(PathPatch(Path(verts, codes), fill=False, edgecolor="#444444", lw=1.4))
            ax.add_patch(FancyArrowPatch(verts[-2], verts[-1], arrowstyle="-|>",
                                         mutation_scale=16, color="#444444", lw=0))
            # label sits on the horizontal run, biased toward the target x
            lx = p0[0] + 0.72 * (p1[0] - p0[0])
            ax.text(lx, ey - 0.18, label, ha="center", va="bottom", fontsize=9,
                    color="#333333",
                    bbox=dict(boxstyle="round,pad=0.15", fc="#eeeeee", ec="none"))
            continue
        ss, ds = pick_sides(s, d)
        p0 = edge_point(s, ss)
        p1 = edge_point(d, ds)
        # orthogonal-ish routing via a mid elbow
        if ss in ("bottom", "top") and ds in ("bottom", "top"):
            verts = [p0, (p0[0], (p0[1] + p1[1]) / 2), (p1[0], (p0[1] + p1[1]) / 2), p1]
            codes = [Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO]
        elif ss in ("left", "right") and ds in ("left", "right"):
            verts = [p0, ((p0[0] + p1[0]) / 2, p0[1]), ((p0[0] + p1[0]) / 2, p1[1]), p1]
            codes = [Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO]
        else:
            verts = [p0, (p1[0], p0[1]), p1]
            codes = [Path.MOVETO, Path.LINETO, Path.LINETO]
        ax.add_patch(PathPatch(Path(verts, codes), fill=False, edgecolor="#444444", lw=1.4))
        # arrowhead
        ax.add_patch(FancyArrowPatch(verts[-2], verts[-1], arrowstyle="-|>",
                                     mutation_scale=16, color="#444444", lw=0))
        if label:
            mx = (p0[0] + p1[0]) / 2 + ldx
            my = (p0[1] + p1[1]) / 2 + ldy
            ax.text(mx, my, label, ha="center", va="center", fontsize=9,
                    color="#333333",
                    bbox=dict(boxstyle="round,pad=0.15", fc="#eeeeee", ec="none"))

    # draw nodes
    for name, nd in N.items():
        x, y, w, h = nd["x"], nd["y"], nd["w"], nd["h"]
        fc, ec = facecol(name, nd)
        if nd["kind"] == "diamond":
            dpath = Path([(x, y - h / 2), (x + w / 2, y), (x, y + h / 2),
                          (x - w / 2, y), (x, y - h / 2)],
                         [Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO, Path.CLOSEPOLY])
            ax.add_patch(PathPatch(dpath, fc=fc, ec=ec, lw=2))
        elif nd["kind"] in ("stadium", "stadium2"):
            ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                         boxstyle="round,pad=0.02,rounding_size=0.45",
                         fc=fc, ec=ec, lw=2))
        else:
            ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                         boxstyle="round,pad=0.02,rounding_size=0.08",
                         fc=fc, ec=ec, lw=2))
        weight = "bold" if nd["kind"] in ("box", "box_r", "stadium", "stadium2", "crash") else "normal"
        ax.text(x, y, nd["label"], ha="center", va="center", fontsize=10.5,
                color=TXT, weight=weight)

    fig.tight_layout(pad=0.4)
    out = "reports/figures/pipeline.png"
    fig.savefig(out, dpi=200, bbox_inches="tight", facecolor="white", metadata={"Software": None})
    print(f"wrote {out}")


if __name__ == "__main__":
    draw()
