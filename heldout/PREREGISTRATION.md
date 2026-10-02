# Pre-registration: held-out Spider run (checklist item O1)

Drafted Thursday 1 October 2026, before any held-out question has been sent to any system. Nothing in this document may change after the first held-out API call; a change before it is made in section 12 with its date.

## 1. Question

The paper's Spider results use wta_1, world_1 and bike_1. GDC's Call-1 prompt (s43 to s45) and several engine fixes were written after its failures on those questions were seen, so the Spider results are open to the objection that GDC was fitted to its test set. This run asks whether the paper's central finding holds on Spider databases that no prompt, engine change or corpus decision was ever made on: on questions the register cannot answer, GDC answers anyway by silent goal substitution (§5.3, §5.4).

Primary prediction, fixed now: on the held-out out_of_scope_register questions, GDC answers anyway (silent_wrong) in a share of cells whose 95% question-cluster interval excludes 0. The paper's development figure for Spider out_of_scope_register (S40 off) is 63%; the held-out point estimate is reported beside it.

Secondary prediction, fixed now: if the §5.2 pattern carries over, GDC's tie-tolerant in-scope silent-error rate is the lowest of the five systems, or tied for lowest. With 10 in-scope questions this is descriptive (section 7).

Both results are reported whether or not they hold (section 8).

## 2. Frozen code

- Commit `8605f521f4d106509b79ca49f0f8fc439dc2fd3f` on `public-release` (1 Oct 2026), the commit batch 2 runs on. Its engine-file hashes (`scripts/parity_overlay.engine_hashes`) are the ones batch 2 records in `fixed_code.sha256`.
- Additions, all new files under `heldout/` (patch `heldout.patch`); no tracked file changes, so the engine hashes are unchanged:
  - `sql_tier.py`: the tier and register rule (section 4), stdlib only.
  - `build_domains.py`: corpora, register files and `schema.json`.
  - `build_gold.py`: gold, a copy of `evidence/gold/build_gold_spider_wta_1.py` that differs only in paths and output names.
  - `detect_ties.py`: ranking ties, by the method of `scripts/detect_ties.py`.
  - `run_heldout.py`: plan, cost, checks, no-API preflight and the run. It reuses batch 2's prompt guard, bounds and fake client.
  - `analyse_heldout.py`: the analysis in section 7.
  - `validate_offline.py`: the offline checks.
  - `freeze.py` and `FROZEN.json`: the sha256 of every input, of these scripts and of this document, and the frozen engine hashes.
  - The built corpora, register files, schemas, gold, digests and tie file.
- The harness itself is not modified. Domains are passed to `evidence.eval_run.harness.run_cells` by path, as batch 2 does.
- `run_heldout.py` refuses to plan or run if any file listed in `FROZEN.json` or any engine file differs from its frozen hash.

## 3. Databases

Eligible: a Spider database other than wta_1, world_1 and bike_1 whose official `.sqlite` is in the frozen tree and none of whose questions appears in any corpus, test, prompt or note. Gold is built by executing the reference SQL, so a database without its `.sqlite` cannot be used.

The frozen tree contains the questions and SQL of Spider's train and dev sets (`dev_data.parquet`, `train_data.parquet`, `combined_data.parquet`). It contains the `.sqlite` of only five: the three development databases, `employee_hire_evaluation` and `train_station`. A search of `public-release` and `gdc-paper-2` for all 874 questions of the 19 candidate databases found none of them. The one substring hit was a code-agent comment, "Count the number of employees in the Sales department", in the synthetic employee domain's results.

The held-out set is therefore:

| database | Spider split | questions | distinct gold SQL | tables (rows) |
|---|---|---|---|---|
| employee_hire_evaluation | dev | 38 | 19 | employee (10), shop (9), hiring (7), evaluation (6) |
| train_station | train (it has no dev questions; bike_1's 104 questions are all train-split too) | 23 | 23 | station (12), train (11), train_station (11) |

Disclosed history. Both databases were candidates when the third Spider domain was chosen in July. `scripts/survey_spider_candidates.py`, which prints example questions, shortlisted employee_hire_evaluation. `scripts/extract_spider_schema.py` then dumped its schema, and it was not chosen. Both are scored in `reports/spider_full_ranking.txt`. train_station's `.sqlite` was added on 13 July with `train_data.parquet` (commit 9889538). No system has been run on either database, and no prompt, engine change or corpus decision used them.

The other 17 unused Spider dev databases are not in this run: their `.sqlite` files are not on disk.

## 4. Corpus construction (mechanical)

1. **Questions.** Every Spider question for the database in `combined_data.parquet`, in file order, deduplicated by exact gold SQL text with the first phrasing kept. This is the rule that reproduces the released corpora's 31, 65 and 53 distinct queries. No question is authored, reworded, sampled or dropped. (The released corpora were then trimmed by hand for diversity; this run does not trim.)
2. **Register table.** The table that the most deduplicated questions read alone, ties broken by more rows, then by name. Applied to the development databases, this rule picks matches and country, as chosen there, and weather for bike_1, where trip was chosen by hand for theme; that choice is disclosed in that corpus. Here it picks **shop** (read alone by 7 questions; employee 4, hiring 1, evaluation 1) and **station** (12; train 3).
3. **Tier**, decided from the gold SQL and the table list by `sql_tier.tier_of`, in this order:
   - **out_of_scope_register** if the SQL reads any table other than the register, anywhere: a join, a subquery or a set-operation branch. expected_behavior is refuse.
   - **out_of_contract_aggregate** if it reads only the register but uses a construct outside the Goal vocabulary. expected_behavior is answer; gold is executed; the tier is excluded from scoring, as in §4.2.
   - Otherwise the **answer** tier. The tier field is the SQL structure (filter / aggregate / group_by / order_by), as in the released corpora.

   The Goal vocabulary is what `planner/contract.py`, `planner/order.py` and `gdc/primitives.py` declare at the frozen commit:
   - filters: a conjunction of `col op literal` with op in == != < <= > >= in contains;
   - aggregates: at most one, in sum / avg / count / min / max / count_distinct, over all rows or per group, with only the group keys beside it;
   - computed columns: + - * / over columns and numeric literals;
   - ordering: an OrderSpec of sort keys and a row limit.

   A register-only question goes to out_of_contract_aggregate if it uses any of:
   - the four constructs §4.2 names: HAVING (`having_filter`), more than one distinct aggregate across SELECT, ORDER BY and HAVING (`multi_aggregate`), a subquery with an aggregate (`aggregate_subquery`), or a non-key column beside an aggregate (`group_attribute_projection`);
   - the released corpora's `distinct_projection` (SELECT DISTINCT, or GROUP BY with no aggregate);
   - a construct the development databases' in-register questions did not use: `set_operation`, `self_join`, `subquery`, `pattern_match` (LIKE other than `%text%`), `scalar_function`, `disjunction` (OR other than equalities on one column, which is `in`), `null_predicate` or `limit_offset`.

   These questions are answerable from the register, so they are in scope by τ(q); they leave the answer tier because the vocabulary cannot express them, as in §4.2. The gap labels are descriptive; only the tier is used.
4. **Ambiguous cases** are decided by the code, not by reading. `sql_tier.py` is frozen by hash. No case is decided by looking at a system output, and none can be, since no system has run. The rule raises on a table name it cannot resolve; it raised on none.
5. **Check against the released corpora.** On the 85 released Spider questions, the rule gives the released tier for 82. It differs on three, which stay as released and are listed for the author:
   - wta_23: SELECT DISTINCT. Released in the answer tier; the rule gives `distinct_projection`, the label bike_09 was retiered under.
   - wta_26: an INTERSECT reading only matches. Released as out_of_scope_register; the rule gives out_of_contract_aggregate (`set_operation`), since the data is in the register.
   - bike_05: `LIKE "8/%"` with a non-key column beside the count. Released in the answer tier; the rule gives `pattern_match` and `group_attribute_projection`.
6. **Register file and `schema.json`.** The register is `SELECT * FROM <register>` read from the `.sqlite` by pandas. Column types are assigned by rule, in table order:
   - `id`: a primary key, a foreign-key column, or a name `id` / `*_id`;
   - `numeric`: declared numeric affinity;
   - `date`: declared DATE / DATETIME / TIMESTAMP;
   - `categorical`: 2 to 50 distinct values and at most half the non-null rows, with the sorted values listed;
   - `text`: everything else.

   match_strategy is numeric_compare, date_compare or exact; no column is `contains`. `domain.description` names the database and all of its tables and nothing else. There are no business rules and no column descriptions. The result: shop has one numeric column and no categorical column; station has four numeric columns and one categorical column, Location (Birmingham, Glasgow, London).
7. **Gold.** Each answer and out_of_contract_aggregate question's reference SQL is executed against the `.sqlite` by `evidence/gold/spider_gold_common.execute_gold`, as for the released Spider gold. out_of_scope_register gold is a refusal record. Ties: `detect_ties.py` finds no ranking tie at a LIMIT boundary in either register, so strict and tie-tolerant scoring coincide on this data.

Result (built and validated offline; `validate_offline.py` passes):

| database | register (rows) | answer | out_of_scope_register | out_of_contract_aggregate |
|---|---|---|---|---|
| employee_hire_evaluation | shop (9) | 4 | 12 | 3 |
| train_station | station (12) | 6 | 11 | 6 |
| **total** | | **10** | **23** | **9** |

- Answer tier: hire_05, hire_06, hire_08, hire_19; station_01, station_02, station_04, station_05, station_10, station_23.
- out_of_contract_aggregate:
  - hire_07 (multi_aggregate), hire_09 (aggregate_subquery), hire_18 (set_operation);
  - station_03 and station_08 (distinct_projection), station_06 and station_07 (multi_aggregate), station_09 (set_operation), station_19 (having_filter).

## 5. Systems and configuration

- **GDC** (`network`), in the configuration behind the paper's Spider numbers: `results_spider_<db>_postfix_s40off`, Call-1 prompt **s45**, table-coverage instruction (S40) **off**, temperature 0.
  - The frozen code renders prompt s48. The runner sends s45: the s48 text with the two blocks s46a/s48 added removed (the "top N" direction line and the STAGED FILTERS rule).
  - Before the run starts, this transform is applied to the frozen code's prompt for wta_1, world_1 and bike_1. It must reproduce the recorded s45 prompt of each byte for byte; it does.
  - The engine is the frozen one, the same engine every reported GDC Goal is replayed on.
- **planner, react, code-agent, text-to-SQL**: the prompts the frozen code renders, which on the three development databases are byte-identical to their recorded Spider prompts. The tools and harness are the frozen ones, including the 1 October code-agent fixes. Temperature 0.
- All five systems: model claude-sonnet-4-5, prompt caching on. The bounds are those of batch 2: react 15 iterations, code-agent 12 retries (13 calls), text-to-SQL 60 s per SQL query.
- No refusal or decline instruction: REFUSE_UNSUPPORTED, REFUSE_OUT_OF_SCOPE_DATA and DECLINE_AFFORDANCE are unset, as are EMISSION_MODEL and MODEL_CALL1. The runner refuses to start otherwise. Every call passes batch 2's guard, which rejects another system text, another model or a temperature other than 0 before anything is sent.
- **Seeds 0 and 1.** The API has no seed, so a seed is a repetition label. At temperature 0, repetitions are near-copies (Appendix A.8), so the unit of analysis is the question.
- **Cells.** All five systems on the answer tier and out_of_scope_register; GDC alone on out_of_contract_aggregate, run and not scored, as in Appendix A.5. 10×5×2 + 23×5×2 + 9×2 = **348 cells**.

## 6. Procedure

1. Batch 2 finishes first.
2. In a tree at the frozen commit with `heldout.patch` applied (new files only): `python heldout/freeze.py --check` passes, then `python heldout/run_heldout.py --dry` prints PREFLIGHT PASSED. The preflight is a no-API pass of all 348 cells through the real harness with a fake client.
3. `python heldout/run_heldout.py --run --cap 15`. Outputs go to `heldout/results/`.
4. A run interrupted by a transport or credit error resumes; completed cells are never re-run. No file in `FROZEN.json` changes after the first call.
5. `python heldout/analyse_heldout.py`. It refuses to analyse unless all 348 planned cells are present exactly once.

## 7. Metrics and analysis

- **Scoring.** The frozen comparator, s48 strict. It equals s47 on gold without a `match` field, and no Spider gold has one. Tie-tolerant scoring is s48t with the held-out tie file, which is empty, so strict and tolerant coincide on this data.
- **Intervals and tests (all metrics).** A 95% question-cluster bootstrap interval: 10,000 resamples of questions stratified by database, seed 20261001 + crc32(set name), percentile interval. For GDC against each alternative, the paired difference with its cluster-bootstrap interval, an exact sign test and an exact sign-flip permutation test, with Holm's adjustment over the four alternatives. A difference counts as resolved only when the interval excludes 0 and the exact permutation p < 0.05. These are the methods of the paper's question-clustered statistics.
- **Primary metric.** Answer-anyway rate on out_of_scope_register: the share of cells scored silent_wrong (an answer returned to a question the register cannot answer), per system, both databases pooled, 23 questions × 2 seeds. GDC's rate with its interval is the primary result; its paired differences against the four alternatives are reported with it.
- **Secondary**, each with the same intervals:
  - in-scope (answer-tier) silent-error rate, tie-tolerant with strict alongside;
  - in-scope accuracy, strict and tolerant;
  - refused-should-answer;
  - GDC on out_of_contract_aggregate (refused / silent / correct, unscored);
  - billed cost per system.
- **What this run can show.** The primary metric has 23 questions. The in-scope set has 10: an exact test reaches p < 0.05 only when at least 6 questions differ, all in one direction (p = 0.031), and at least 8 after Holm. The registers have 9 and 12 rows. The in-scope row tests whether the direction of §5.2 holds on unseen databases; it cannot rank systems whose rates differ by a few questions, and it is reported as that.

## 8. Reporting

- §5.3 gets the primary result: one held-out row in the out_of_scope_register table (five systems, answer-anyway rate with intervals, n = 23 questions × 2 seeds) and one sentence stating whether the primary prediction in section 1 held, beside the development figure.
- §5.2 gets one held-out row under Table 2: five systems, in-scope silent-error (tolerant and strict) and accuracy, n = 10 questions × 2 seeds, and one sentence on the secondary prediction.
- Appendix A gets a per-question table, A.10.
- The held-out cells are never pooled with wta_1, world_1 or bike_1, and the text states the two databases, their sizes and the question count.
- Reported whatever it shows. Wording is approved by Sebastian (O1.d). Numbers come only from `analyse_heldout.py` output.

## 9. Budget

- From recorded per-cell costs of the same five systems on the three Spider databases, the realistic cost is **$3.9 to $5.7**. The lower figure uses batch 2's temperature-0 react and code-agent costs; the higher uses the July default-temperature react out_of_scope_register cost of $0.056 per cell.
- **Hard cap $15.** A (system, question) batch whose worst case (call bound × the dearest single recorded call of that system on Spider) would take the billed spend over the cap is not started. That stops the run, which then counts as incomplete (section 10).

## 10. Gates and drop rules (from `docs/audit/checklist_2026-10.md`)

- **O1.a, Fri 2 Oct 12:00.** This document approved by Sebastian and committed with `FROZEN.json` before any held-out cell runs. Missed: O1 dropped.
- **O1.b, Fri 2 Oct 18:00.** Corpora, schemas and gold built by the rule. Built 1 Oct; they are committed with O1.a. Missed: O1 dropped.
- **O1.c, Sat 3 Oct 08:00.** All 348 cells complete within the $15 cap. Missed, cap reached, or a frozen-hash mismatch: O1 dropped.
- A dropped O1 is not mentioned in the paper. Inclusion is decided by completion, never by results.
- **O1.d.** Reported as in section 8, whatever it shows.

## 11. Known limits, stated now

- Two small databases and 10 in-scope questions.
- Both databases were candidates in the July selection (section 3).
- train_station's questions come from Spider's train split.
- The schema-typing and description rule is new. The development databases' schemas were written by hand: wta_1's person-name columns are `contains`, and their descriptions are prose.
- GDC's prompt carries column types and category values that the four alternatives' briefing lacks, as on the development databases.

## 12. Deviations

- **1 October 2026, before any held-out call: primary metric changed** from the in-scope silent-error rate to the answer-anyway rate on out_of_scope_register; the in-scope comparison becomes secondary. Reason: only two unused databases are on disk, giving 10 in-scope questions, too few to resolve a difference of a few questions; the out_of_scope_register set (23 questions) tests the paper's central claim. Decided by Sebastian on tier counts alone; no system had been run on any held-out question. `freeze.py` re-run.

Any further change before the first held-out call is recorded here with its date and reason, and `freeze.py` is re-run. Any change after it voids O1.
