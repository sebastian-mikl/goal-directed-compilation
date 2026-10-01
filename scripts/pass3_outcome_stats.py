#!/usr/bin/env python
"""Pass-3 analysis: five-way outcome partitions, the 11 exact-col flip cells,
per-question Spider breakdown + Wilson intervals, risk-coverage. REPLAY only.

Provenance matches scripts/build_paper_numbers.py exactly, because the cells and
their scoring inputs come from the same module (scripts/paper_inputs.py):
  - network and planner = REPLAY recorded goals / plan replies through the
    current engine, every domain (Decisions 2, 7)
  - react/code_agent/text_to_sql = score recorded raw_result
  - risk T5 = results_t5.rescored + results_risk_t5_missing (Dec 3/4)
  - Spider network = the postfix_s40off run (Dec 1)
  - PARITY_TEMP0=1 -> the same temperature-0 overlays (Decisions 5, 6)
"""
from __future__ import annotations
import sys, math
from collections import Counter, defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from evidence.eval_run.comparator import score, COMPARATOR_VERSION
from scripts import paper_inputs as pi
import scripts.ablate_checks as A
assert COMPARATOR_VERSION == "s48"

SPIDER = pi.SPIDER
SYSTEMS = pi.SYSTEMS
BOUNDARY = {"out_of_scope_register", "out_of_contract_aggregate"}
GOLD_SPIDER = {d: pi.GOLD[d] for d in SPIDER}
GOLD_RISK = pi.GOLD.get("risk", {})   # empty where the risk data is withheld
GOLD_EMP = pi.GOLD["employee"]
_load = pi.load

FIVE = ["correct", "silent_wrong", "refused_should_answer", "crash", "timeout"]
def part(outs):
    c = Counter(outs)
    return {k: c.get(k, 0) for k in FIVE}, len(outs)

RISK_OFF = pi.risk_off()
RISK_T5 = pi.risk_t5()
EMP = pi.employee()
SYN_TIERS = ["T1_single_op","T2_two_ops","T3_three_ops","T4_business_rule_multi_op",
             "T5_high_complexity","house_vocab","algebra_boundary","out_of_range"]

def syn_rows(dom, sysname, tier):
    if dom == "employee":
        return [r for r in EMP if r["system"]==sysname and r["tier"]==tier], GOLD_EMP
    pool = RISK_T5 if tier=="T5_high_complexity" else RISK_OFF
    return [r for r in pool if r["system"]==sysname and r["tier"]==tier], GOLD_RISK

def outcomes_syn(dom, sysname, tier):
    rows, gold = syn_rows(dom, sysname, tier); outs=[]; cells=[]
    for r in rows:
        g = gold.get(r["qid"])
        if g is None: continue
        res, status = pi.scored_input(dom, r)   # network/planner replayed, others recorded
        o = score(res, g, status=status)[0]
        outs.append(o); cells.append((r["qid"], r.get("seed"), o))
    return outs, cells

# ============ ITEM 1: five-way risk + employee per tier per system ============
print("="*78); print("ITEM 1 — FIVE-WAY OUTCOME PARTITION (risk, employee)"); print("="*78)
for dom in (("risk","employee") if pi.HAS_RISK else ("employee",)):
    print(f"\n### {dom} ### (c=correct s=silent r=refused_should_answer x=crash t=timeout)")
    print(f"{'tier':<26}{'system':<12}{'c':>4}{'s':>4}{'r':>4}{'x':>4}{'t':>4}{'n':>5}  sum_ok")
    for tier in SYN_TIERS:
        for sysname in SYSTEMS:
            outs, _ = outcomes_syn(dom, sysname, tier)
            if not outs:
                print(f"{tier:<26}{sysname:<12}{'--- NO DATA ---':>25}"); continue
            p, n = part(outs)
            s = p["correct"]+p["silent_wrong"]+p["refused_should_answer"]+p["crash"]+p["timeout"]
            print(f"{tier:<26}{sysname:<12}{p['correct']:>4}{p['silent_wrong']:>4}"
                  f"{p['refused_should_answer']:>4}{p['crash']:>4}{p['timeout']:>4}{n:>5}  {s==n}")

# ---- main-table percentages: acc% / silent% / refused% (+ crash+timeout remainder) ----
print("\n--- MAIN TABLE cells: acc% / silent% / refused%  (x+t = crash+timeout remainder) ---")
def pct(k,n): return round(100*k/n,1) if n else 0.0
for dom in (("risk","employee") if pi.HAS_RISK else ("employee",)):
    print(f"\n[{dom}]  tier            " + "".join(f"{s[:10]:>22}" for s in SYSTEMS))
    for tier in SYN_TIERS:
        line=f"  {tier:<16}"
        for sysname in SYSTEMS:
            outs,_=outcomes_syn(dom,sysname,tier)
            if not outs: line+=f"{'NODATA':>22}"; continue
            p,n=part(outs); xt=p['crash']+p['timeout']
            cell=f"{pct(p['correct'],n)}/{pct(p['silent_wrong'],n)}/{pct(p['refused_should_answer'],n)}"
            if xt: cell+=f"(+{pct(xt,n)}xt)"
            line+=f"{cell:>22}"
        print(line)

# ============ Spider in-contract pooled five-way per system ============
print("\n"+"="*78); print("Spider in-contract (pooled 135) FIVE-WAY per system (strict s47)"); print("="*78)
print(f"{'system':<12}{'c':>4}{'s':>4}{'r':>4}{'x':>4}{'t':>4}{'n':>5}  sum_ok")
spider_cells = defaultdict(list)   # system -> list of (d,qid,seed,outcome)
for sysname in SYSTEMS:
    outs=[]
    for d in SPIDER:
        for r in _load(pi.spider_file(d, sysname)):
            if r.get("system")!=sysname: continue
            g = GOLD_SPIDER[d].get(r["qid"])
            if g is None or g["tier"] in BOUNDARY: continue
            res, status = pi.scored_input(d, r)
            o = score(res, g, status=status)[0]
            outs.append(o); spider_cells[sysname].append((d, r["qid"], r.get("seed"), o))
    p,n = part(outs)
    s = sum(p.values())
    print(f"{sysname:<12}{p['correct']:>4}{p['silent_wrong']:>4}{p['refused_should_answer']:>4}"
          f"{p['crash']:>4}{p['timeout']:>4}{n:>5}  {s==n}")

# ============ ITEM 2: the exact-col flip cells (silent -> correct) ============
print("\n"+"="*78); print("ITEM 2 — exact-col EXACT->SUBSET flip cells (baseline vs no_exact_col)"); print("="*78)
base = A.replay_all("baseline")
sub  = A.replay_all("no_exact_col")
flips = defaultdict(list)   # (b->a) -> list of (domain,qid,tier,seed)
for k in base:
    b, a = base[k]["outcome"], sub[k]["outcome"]
    if b != a:
        flips[(b,a)].append((base[k]["domain"], k[1], base[k]["tier"], k[2]))
print("all flip classes:", {f"{b}->{a}":len(v) for (b,a),v in sorted(flips.items())})
sc = flips.get(("silent_wrong","correct"), [])
print(f"\nsilent_wrong -> correct : {len(sc)} cells (these are the '11')")
by_qid = Counter((d,q,t) for (d,q,t,s) in sc)
for (d,q,t),cnt in sorted(by_qid.items()):
    print(f"   {d:<9} {q:<8} {t:<26} x{cnt} reps")
cs = flips.get(("correct","silent_wrong"), [])
print(f"\ncorrect -> silent_wrong : {len(cs)} cells (the '23 prevented' direction)")
for (d,q,t),cnt in sorted(Counter((d,q,t) for (d,q,t,s) in cs).items()):
    print(f"   {d:<9} {q:<8} {t:<26} x{cnt} reps")

# confirm the 11 are silent_wrong in HEADLINE (baseline==headline for net) & counterfactual
print("\n-- headline silent-error impact if the 11 were scored correct --")
for dom, gold, tot_label in ((("risk",GOLD_RISK,"risk"),) if pi.HAS_RISK else ()) + (("employee",GOLD_EMP,"employee"),):
    ic_tiers = ["T1_single_op","T2_two_ops","T3_three_ops","T4_business_rule_multi_op","T5_high_complexity","house_vocab","algebra_boundary"]
    outs=[]
    for tier in ic_tiers:
        o,_ = outcomes_syn(dom,"network",tier); outs+=o
    n=len(outs); sw=outs.count("silent_wrong")
    flip_here = sum(cnt for (d,q,t),cnt in by_qid.items() if d==dom)
    print(f"   {tot_label:<9} network in-contract: silent {sw}/{n} = {100*sw/n:.1f}%   "
          f"exact-col flip cells here={flip_here}  ->counterfactual silent {sw-flip_here}/{n} = {100*(sw-flip_here)/n:.1f}%")

# ============ ITEM 3: per-question Spider in-contract breakdown ============
print("\n"+"="*78); print("ITEM 3 — per-QUESTION Spider in-contract (correct reps / n) by system"); print("="*78)
# gather qids in-contract, ordered
qorder=[]
for d in SPIDER:
    for qid,g in GOLD_SPIDER[d].items():
        if g["tier"] not in BOUNDARY:
            qorder.append((d,qid))
qorder=sorted(set(qorder))
print(f"in-contract unique questions: {len(qorder)}")
# per system per qid: correct count / reps
pq = {s:{} for s in SYSTEMS}
for s in SYSTEMS:
    for (d,qid,seed,o) in spider_cells[s]:
        pq[s].setdefault((d,qid),[]).append(o)
hdr = f"{'d/qid':<16}" + "".join(f"{s[:5]:>7}" for s in SYSTEMS)
print(hdr)
for (d,qid) in qorder:
    cells=f"{d[:4]}/{qid:<10}"
    for s in SYSTEMS:
        outs = pq[s].get((d,qid),[])
        c=outs.count("correct"); n=len(outs)
        cells+=f"{c}/{n:<5}".rjust(7)
    print(cells)

# Wilson 95% interval helper
def wilson(k,n,z=1.96):
    if n==0: return (0,0)
    ph=k/n; d=1+z*z/n
    c=(ph+z*z/(2*n))/d; h=z*math.sqrt(ph*(1-ph)/n+z*z/(4*n*n))/d
    return (100*(c-h),100*(c+h))
print("\n-- Wilson 95% CI on pooled 135 (clustering caveat: 27q x 5 reps) --")
for s in SYSTEMS:
    outs=[o for (_,_,_,o) in spider_cells[s]]
    n=len(outs); c=outs.count("correct"); sw=outs.count("silent_wrong")
    lo,hi=wilson(c,n); slo,shi=wilson(sw,n)
    print(f"   {s:<12} acc {100*c/n:.1f}% [{lo:.1f},{hi:.1f}]   silent {100*sw/n:.1f}% [{slo:.1f},{shi:.1f}]  (n={n})")

# per-question: how many of 27 questions does each system get right in ALL / ANY reps
print("\n-- per-question stability (of 27): all-5-correct / all-5-wrong / split --")
for s in SYSTEMS:
    allc=allw=split=0
    for (d,qid) in qorder:
        outs=pq[s].get((d,qid),[]); c=outs.count("correct"); n=len(outs)
        if c==n: allc+=1
        elif c==0: allw+=1
        else: split+=1
    print(f"   {s:<12} all-correct={allc:>3}  all-wrong={allw:>3}  split={split:>3}")

# ============ ITEM 3b: risk-coverage feasibility (Spider in-contract) ============
print("\n"+"="*78); print("ITEM 3b — risk-coverage feasibility (answered vs refused)"); print("="*78)
for s in SYSTEMS:
    outs=[o for (_,_,_,o) in spider_cells[s]]
    n=len(outs)
    answered=[o for o in outs if o in ("correct","silent_wrong")]
    refused=sum(1 for o in outs if o=="refused_should_answer")
    crash=sum(1 for o in outs if o in ("crash","timeout"))
    acc_on_answered = (100*answered.count("correct")/len(answered)) if answered else float('nan')
    print(f"   {s:<12} answered={len(answered)}/{n} ({100*len(answered)/n:.0f}%)  "
          f"refused={refused} crash/timeout={crash}  acc-on-answered={acc_on_answered:.1f}%")
print("\n(risk-coverage curve needs a confidence/abstention signal to VARY coverage;")
print(" GDC's refusals are structural not score-thresholded, so only the single")
print(" operating point exists per system on Spider in-contract — see notes.)")
print("\nDONE.")
