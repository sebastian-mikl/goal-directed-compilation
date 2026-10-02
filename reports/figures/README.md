# Figures and tables: what is here and where each value comes from

Everything except `pipeline.png`, `spider_data_scope.png`, `pipeline.mmd` and this file is written by
`scripts/make_paper_figures.py` (step 5 of `./run_all.sh`) from `reports/paper_numbers.csv`. This file
holds no number: read the values from the CSV, from the `.csv` slice beside each figure, or from
`figures_manifest.csv`, which lists every value a figure plots with its CSV key
(`fig_table, domain, system, tier_group, scoring, metric`), numerator, denominator, comparator and source.
The comparator versions in the captions and footnotes are read from the CSV (`s48` strict, `s48t`
tie-tolerant).

## Figures drawn from the CSV

| Paper figure | File | What it plots | CSV rows (`fig_table` / `domain` / `tier_group`) |
|---|---|---|---|
| Figure 4 | `fig2_silent_error_tolerant.png` | Spider in-scope silent-error, tie-tolerant, five systems | `Fig1/Fig2/Table3` / `spider_pooled` / `in_contract_answer`, scoring `tolerant`, metric `silent_wrong` |
| Figure 5 | `fig1_accuracy.png` | Spider in-scope accuracy, strict | same rows, scoring `strict`, metric `accuracy` |
| Figure 6 | `fig2_silent_error.png` | Spider in-scope silent-error, strict | same rows, scoring `strict`, metric `silent_wrong` |
| Figure 7 | `fig4_s40_refusal.png` | GDC out-of-scope-register refusal accuracy, table-coverage instruction off vs on | `Fig3/Fig4` / each Spider database / `out_of_scope_register_s40off` and `_s40on` |

Each has a `.csv` slice with the plotted values. The tier identifier `in_contract_answer` is the builder's
name for the answer tier of the contract label tau(q); the figures and tables print it as "in-scope".

Drawn but not in the paper: `fig1_2_combined.png`, `fig3_network_tiers.png` (`Fig3/Fig4`, GDC per tier and
Spider database) and `fig_strict_vs_tolerant_silent.png`. Their footnote text (for example the number of
cells that strict and tolerant scoring score differently for GDC) is computed from the CSV rows.

## Tables

| File | Content | CSV rows |
|---|---|---|
| `table1_employee.{md,tex}`, `table1_synthetic.csv` | employee per-tier accuracy and silent-error, strict | `Table1` / `employee` |
| `table2_crossdomain.{md,tex}` | GDC against the strongest baseline per domain. Git-ignored: the version built with the risk data holds a risk row, so the file written here (employee and Spider only) is never committed | `Table2` / `employee`, `Fig1/Fig2/Table3` / `spider_pooled` |
| `table3_spider_incontract.{md,tex}` | Spider in-scope accuracy and silent-error, strict and tolerant, five systems | `Fig1/Fig2/Table3` / `spider_pooled` |

## Diagrams that are not generated here

`pipeline.png` (Figure 1, the control flow) and `spider_data_scope.png` (Figure 3, the Spider tier
structure) are hand-drawn diagrams with no generating source in this repository. They are shipped as
drawn and carry no data. `pipeline.mmd` and `scripts/make_pipeline_figure.py` draw an earlier version of
the control flow, with a "Model declined?" branch and a "Crash" outcome that `pipeline.png` does not
have. Neither reproduces `pipeline.png`: the script's output is a different, larger image.

## Not produced in this release

The paper's Figure 8 (the risk cost frontier), the Haiku robustness panel and the risk tables
(`table1_risk`, `table4_cost_frontier`, `table5_haiku_robustness`) read risk rows that
`reports/paper_numbers.csv` does not hold. `make_paper_figures.py` skips them when the CSV has no risk
rows, and their file names are in `.gitignore`.
