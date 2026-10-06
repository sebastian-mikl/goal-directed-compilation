# Pre-registration: post hoc equal-information run for the planner

Written and committed before any API call of this run. This run is post hoc: it was added after other results were known, and the rule below is fixed before this run's results.

**Question.** The planner writes the steps for GDC's executor in one call, so it is the clean derive-versus-write comparison. But its prompt carried the shared briefing, not GDC's richer domain information. This run gives the planner that information. Does GDC stay more accurate?

## Design

- **System and model:** the planner (system id `planner_gdcinfo`), claude-sonnet-4-5, temperature 0, one call per cell, max_tokens 1500. These are the bounds of its reported cells. The executor is the same: the shipped `planner_upfront.execute_reply` on the fixed engine (contract s49).
- **Prompt:** the reported planner prompt with the shared briefing replaced by GDC's domain information. Nothing else changes; the planner's own instructions stay byte for byte.
- **Source of the domain information:** GDC's rendered prompt as reported.
  - Employee: s31, `results/results_employee.provenance.json`.
  - Spider: s45, `results/results_spider_<db>_postfix_s40off.provenance.json`.
  - Held-out databases: s45, `heldout/results/heldout_<db>.provenance.json`.
- **What counts as domain information:**
  - column types and match strategies;
  - full category value lists;
  - business rules, with their declared aliases and literal filters;
  - rules of interpretation that state what terms or data mean.
- **What is excluded:** everything about GDC's output format. That means the Goal schema, the JSON instructions, the OrderSpec and every rule that refers to Goal fields. Where a line mixes domain meaning with Goal format, only its domain part is kept, and the line is listed. The line-by-line split is `review/planner_info_split.md`, committed with the runner before the first API call.
- **Questions:**
  - employee in-scope within the vocabulary: 81 questions × seeds 0–2 = 243 cells;
  - the held-out answer tier: 10 questions × seeds 0–1 = 20 cells;
  - Spider's answer tier: 27 questions × seeds 0–4 = 135 cells.

  That is 398 cells. No risk tier and no out-of-scope tier is run.
- **Output:** a separate results file, `results/results_temp0_planner_gdcinfo.jsonl`, that replaces no reported cell.
- **Comparison cells:** GDC's and the reported planner's cells, scored as the paper scores them.
  - Employee and Spider: replays through the fixed engine. These are the cells of the `employee_in_contract` and `spider_answer` sets in `reports/stats/`.
  - Held-out: as run (`heldout/results/analysis/`, Appendix A.10).
- **Scoring of the new cells:** replay of each recorded reply through the fixed engine, as for every planner cell, under s48t (tie-tolerant) and s48 (strict).

## Primary measure

- **Measure:** accuracy, GDC minus the informed planner.
- **Set:** employee and held-out pooled (91 questions, 263 cells). Spider is not in it; it is GDC's development set.
- **Unit:** the question, stratified by database (employee, employee_hire_evaluation, train_station).
- **Method:** the method of every comparison in `reports/stats/`.
  - A 95% percentile interval from a question-level cluster bootstrap with 10,000 resamples.
  - An exact two-sided sign-flip permutation test on the per-question differences.
- **Scoring:** s48t, the paper's primary comparator. s48 is reported beside it.
- **Resolved:** the interval excludes 0 and the exact permutation p < 0.05.
- **Multiplicity:** one comparison, so no Holm.

## Secondary measures

- Silent-error rate, GDC minus the informed planner, on the same set by the same method.
- Accuracy and silent error on Spider's answer tier (27 questions, 135 cells, stratified by database), by the same method. They are reported separately because Spider is GDC's development set.
- Per database: accuracy and silent error for the informed planner, the reported planner and GDC.
- Every question on which the informed planner and GDC differ, listed with its classes.

## Decision rule, fixed now

The rule applies to the primary measure under s48t.

- **OUTCOME A:** no resolved accuracy difference.
- **OUTCOME B:** GDC resolved as more accurate.
- **OUTCOME C:** the informed planner resolved as more accurate.

The outcome under s48 is reported beside it.

## Prediction

OUTCOME A.

## Known before this run

These are the reported cells, as scored in `reports/stats/rates.csv` and `heldout/results/analysis/rates.csv`.

| Set | GDC | Reported planner |
|---|---|---|
| Employee in-scope accuracy | 240 of 243 | 218 of 243 |
| Held-out answer-tier accuracy | 18 of 20 | 18 of 20 |
| Spider answer-tier accuracy | 125 of 135 | 108 of 135 |

On employee and held-out, s48 and s48t agree for both systems. The Spider row is s48t.

## Rules

- Run once.
- No prompt change, rerun or re-seed after seeing results.
- If the run is interrupted, resume without re-running completed cells.
- Cost cap: $5 (expected about $1.50). The run stops before a cell whose worst case would take the spend over the cap.
- The statistics go through the pipeline, so `run_all.sh` regenerates them. No reported cell or number changes.
- This run is post hoc. It was added after other results were known, and the rule is fixed before this run's results.
