# Pre-registration: classifying the silent errors of the step-writing designs

Written and committed before any labelling. It covers analysis of recorded outputs only: no evaluated system is run, and the paper is not edited.

**Question.** When the designs in which the model writes the steps give a wrong answer, does the error come from the steps (a procedure error) or from what they understood the question to mean (a specification error)?

## Scope

- **Cells:** every silent-error cell under strict scoring (s48), claude-sonnet-4-5, in scope and within the vocabulary:
  - employee, as run: the cells of `reports/stats/`'s `employee_in_contract` set;
  - Spider's answer tier: the cells of `spider_answer_strict`;
  - the held-out answer tier, as run: `heldout/analyse_heldout.py`, strict.
- **Systems:** the planner, react and the code agent. GDC's silent errors are labelled too, as a check that the rubric works.
- **Excluded:**
  - Text-to-SQL: it writes a declarative query, so the line between procedure and specification does not apply cleanly to it.
  - Risk: excluded until approval is confirmed.
- **Unit:** the (system, question) pair. Per-cell labels are recorded too.
- **Known before labelling** (counts only, no labels):

| System | Employee | Spider | Held-out | Total |
|---|---|---|---|---|
| Planner | 9 pairs, 25 cells | 6 pairs, 30 cells | 1 pair, 2 cells | 16 pairs, 57 cells |
| React | 9 pairs, 27 cells | 3 pairs, 15 cells | 1 pair, 2 cells | 13 pairs, 44 cells |
| Code agent | 12 pairs, 31 cells | 7 pairs, 28 cells | 1 pair, 2 cells | 20 pairs, 61 cells |
| GDC (check) | none | 2 pairs, 10 cells | 1 pair, 2 cells | 3 pairs, 12 cells |

The three designs pooled have 49 pairs and 162 cells.

## Rubric

For each error, first write down the specification the system evidently intended: its output columns, filters, grouping and derived values, as shown by its own choices, comments or reasoning. Then give one label.

- **SPECIFICATION:** correctly executing that intended specification would reproduce the wrong answer. The system misread the question, the schema or a term. Examples: wrong column, wrong status set, a literal reading of "active", substring instead of exact match, wrong sort direction.
- **PROCEDURE:** correctly executing that intended specification would give the gold answer. The steps failed to implement the intent. Examples: wrong order of operations, a dropped or duplicated step, a filter applied after aggregation, a variable mix-up, a pandas or type slip.
- **SCORING:** the answer is right in substance but was scored wrong. Examples: shape, dict or list form, an identifier the question did not ask for, a tie.
- **UNCLEAR:** the intent cannot be determined from the trace.

The evidence for every label is quoted from the trace.

**Rules fixed now, beyond the rubric:**
- **Both wrong:** sometimes the intended specification is itself wrong and the steps also fail to implement it. Then even correct steps could not give gold, so the label is SPECIFICATION, and the labeller sets the flag `also_procedure`. These pairs are listed. S is also reported with them counted as PROCEDURE.
- **Labelling by cell:** the labeller labels every silent-error cell of the pair. Cells whose trace and output are identical are shown once, with their seeds listed, and get one label.
- **Pair label:** the label all its cells share. If its cells' labels differ, the pair takes the most frequent label, with a tie going to UNCLEAR, and the pair is flagged.

## Process

- **Packets:** one per pair, built by `scripts/error_classification.py` from the recorded results. Each holds:
  - the rubric;
  - a neutral description of the system's design (the system is not named);
  - the question and the gold answer;
  - the schema briefing: the shared briefing the alternatives saw, and the register's column list;
  - for each silent-error cell, the recorded trace and the output that was scored:
    - planner: its plan reply;
    - react: each tool call with its result summary, and its last message;
    - code agent: each code attempt with its output, and its last message;
    - GDC: its Goal and OrderSpec, the steps the engine derives from them, and the result.

  The output scored is the replay through the fixed engine for the planner and GDC on employee and Spider. Elsewhere it is the recorded result. The packets and the labeller instructions are committed before the first label.
- **Labellers:** Claude Code sub-agents of the built-in Plan type, which do not load the project's CLAUDE.md, on claude-opus-5-5. Each is told to use no tools and to answer from its packet only.
- **Passes 1 and 2:** independent, with one fresh sub-agent per pair per pass. A labeller sees only its packet: not the other pass's labels, the paper, its thesis, or any other system's output.
- **Agreement:** at the pair level over the four labels, as a percentage and as Cohen's kappa. This is reported for the three designs pooled (primary), for all pairs including GDC, and at the cell level.
- **Pass 3:** a disagreement is any pair on which the two passes differ, in the pair label or in any cell's label. Each disagreement goes to a third fresh sub-agent. It sees the packet and the two labels with their intended specifications and evidence, presented as labeller 1 and labeller 2, and gives the final label with its own quoted evidence. Every disagreement is listed with both labels and the resolution.
- **Final labels:** the agreed label, or else pass 3's. Nobody changes a label after that within this analysis. Seb's spot check comes afterwards.
- **Spot-check sample:** for each final label, up to 10 pairs drawn at random over all labelled pairs (Python `random.Random(20261006)`, from the sorted pair ids), plus every disagreement.

## Thresholds, fixed now

The three designs (planner, react, code agent) are pooled at the (system, question) level. Let S = SPECIFICATION / (SPECIFICATION + PROCEDURE), over final pair labels.

- **OUTCOME A:** S ≥ 90%.
- **OUTCOME B:** S ≤ 75%.
- **OUTCOME M:** anything in between.
- **INCONCLUSIVE:** if UNCLEAR exceeds 20% of the 49 pooled pairs, whatever S is.

**GDC check:** it passes if every GDC pair is labelled SPECIFICATION or SCORING. If it fails, the failure is reported beside the outcome.

## Outputs

- `review/error_classification.csv`: one row per cell and one per pair, with the labels of each pass, the final label, the evidence and whether the passes agreed.
- `review/error_classification/`: the packets, the labeller instructions, the raw output of every pass, the disagreement list and the spot-check sample.
- The pipeline: `scripts/error_classification_stats.py`, run in `run_all.sh` step 6, checks that the labelled cells are exactly the in-scope silent-error cells. It then writes `reports/stats/error_classification/`: counts by system and label, S, the outcome, the agreement figures and the GDC check. Any number about this analysis comes from that file.
