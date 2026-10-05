# Pre-registration: post hoc alias control for GDC on employee

Written and committed before any API call of this run. This run is post hoc: the rule below was fixed after the alias-arm results were known and before this run's results.

**Question.** Does GDC keep its employee lead when the "active employees" alias is removed from its own prompt? The paper says this is untested (§5.1).

## Design

- **System and model:** GDC (system id `network_noalias`), claude-sonnet-4-5, temperature 0, seeds 0–2.
- **Questions:** the 81 employee in-scope questions, 243 cells. The out-of-range tier is not run.
- **Instruction:** the table-coverage instruction is off.
- **Engine:** the fixed engine (contract s49).
- **Prompt:** the original employee prompt (s31) with only the "active employees" alias removed. The recorded s31 prompt is the one in `results/results_employee.provenance.json` (`network_prompt_version` s31, `git_commit` a204d1e): 4,844 characters, sha256 `b41d4eb53a27a4f86b2edbd15d9e2e5c6ec1121865e51b373d2e59b9fe6a7a49`. It is re-rendered byte for byte from that commit. The control prompt removes ` or "active employees"` from the one rule line that states the mapping, which is exactly what s31's code renders when the alias field is absent:
  - before: `- When the request refers to "current" or "active employees" rows, filter status to one of those values: emit {"col": "status", "op": "in", "val": ["Active", "On Leave", "Sabbatical"]} …`
  - after: `- When the request refers to "current" rows, filter status to one of those values: emit {"col": "status", "op": "in", "val": ["Active", "On Leave", "Sabbatical"]} …`

  The control prompt is 4,822 characters, sha256 `470cb29bc3391e6c43dd7a89218aacb7d3ed5f5d6857a6046a6025639952333f`. The mapping occurs nowhere else in the prompt, and no engine code resolves "active" after the model emits (the alias is read only by the prompt renderer, `gdc/llm.py` `_rules_block`). The model is `claude-sonnet-4-5` with max_tokens 900, as recorded. The runner is `scripts/run_alias_control.py`, and scoring is `scripts/score_alias_control.py`, which reproduces the reported employee GDC cells' outcome classes exactly offline.

## Primary measure

The primary measure is the eight employee T4 questions worded with "active", 24 cells.
- **Failed question:** a question counts as FAILED if GDC is silent-wrong on at least 2 of its 3 seeds.
- **Silent-wrong:** a confident answer that does not match gold. A correct answer therefore needs the full status set (Active, On Leave, Sabbatical).
- **Baseline:** with the alias, GDC fails 0 of the 8.
- **The eight questions:** t4_01, t4_02, t4_05, t4_07, t4_08, t4_10, t4_12 and t4_14 (the alias arm's eight).
- **Note, recorded before the run (it changes no rule):** t4_14 contains "Current Role", a column name, so the remaining trigger word "current" appears in one of the eight questions.

## Decision rule, fixed now

- **OUTCOME A:** at least 6 of 8 questions FAILED.
- **OUTCOME B:** at most 2 of 8 FAILED.
- **OUTCOME C:** 3 to 5 of 8 FAILED.

The paired comparison with the alias-on cells is resolved only if at least 6 questions are discordant, all one way (exact sign-flip p = 0.031 at 6 of 6).

## Prediction

OUTCOME A.

## Secondary measures

- Employee in-scope silent-error over all 243 cells (strict).
- GDC without the alias minus each alternative as run (neither side has the alias), question-clustered, with Holm over the four alternatives.
- The regression check: on the other 73 in-scope questions (219 cells), count the cells whose outcome class differs from the reported run. If more than 6 of 219 differ, the control is not clean and the analysis stops and is reported.
- No in-scope employee question beyond the eight mentions "active" in any form, so there is no further secondary set.

## Rules

- Run once.
- No prompt change, rerun or re-seed after seeing results.
- If the run is interrupted, resume without re-running completed cells.
- Cost cap: $3 (expected about $0.80); the run stops at the cap.
- Scoring: by replay through the fixed engine, as for every GDC cell, strict (primary for employee) and tie-tolerant.
- The results go to a separate file that replaces no reported cell.
