# GDC — Goal-Directed Compilation

Code, data, and recorded evaluation results for the GDC paper: a deterministic
goal→plan→execute engine for tabular analytical queries, compared against
four LLM baselines (a step-planning LLM, a react/tool-use loop, a pandas
code-writing agent, and a text-to-SQL agent) across three domains.

Released here: the engine, the five systems' harness, the employee and Spider corpora,
registers, gold keys and gold builders, every recorded emission the paper's employee and
Spider numbers are built from, the scripts that turn them into `reports/paper_numbers.csv`,
the figures and tables, and the question-clustered statistics; the pre-registered held-out
Spider run (`heldout/`).

**What's not included, upfront:** the risk-register domain's underlying data
(register, corpus, gold, and recorded emissions) is withheld from this public
release for data-sensitivity reasons — the register's schema was designed for
structural realism against a real-world risk-register format, and while every
value in it is fabricated, the schema and category vocabulary are close
enough to a real reference that we do not release the raw data. Risk's
results are reported in full in the paper (Tables 2 and 5, Appendix A.1,
A.7 and A.14) exactly as measured. They are not in this repository's
`reports/paper_numbers.csv`, which is built from the released data and holds
the employee and Spider rows only, and they are not independently
reproducible from this repository the way the employee and Spider domains
are. See [What's not included](#whats-not-included) below for the full
disclosure.

## Quick start

```bash
git clone https://github.com/sebastian-mikl/goal-directed-compilation.git   # see License & citation below
cd goal-directed-compilation
python3 -m venv .venv                 # Python 3.12 or later
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
./run_all.sh
```

`requirements-lock.txt` holds the exact versions the release was checked with;
`requirements.txt` holds version ranges. Under the lock file every file that `run_all.sh`
rewrites comes back byte for byte. Under another matplotlib release the figure PNGs keep
identical pixels (the PNGs carry no matplotlib version string).

`run_all.sh` takes under a minute and makes no API calls. It checks the environment
first and stops, before writing anything, with a message naming what is missing or too
old (Python below 3.12, pandas below 3, numpy below 2, no pyarrow, no matplotlib).

To instead produce *new* emissions by running the five systems live (costs money, needs
`ANTHROPIC_API_KEY`):

```bash
./run_all.sh --live
```

`--live` prints the commands for each domain and runs none of them: there is no single
combined live-run entry point.

## What `./run_all.sh` regenerates, and what it does not

Seven steps, in order. Steps 2 and 3 print diagnostics and write nothing. A correct run prints
the lines below, and nothing in them is an error.

1. **Live step**: skipped unless `--live`, which only prints commands (see above).
2. **Rescore** (`scripts/rescore_all.py`): re-scores every recorded cell under the current
   comparator and lists every cell whose outcome changes against the outcome recorded in the
   results file ("FLIPS"). Both directions are listed, never netted.
3. **Replay** (`scripts/replay_goals.py`): runs every recorded GDC Goal through the shipped
   engine and classes each cell (clean, value drift, comparator drift, engine flip). It ends
   "engine flips (must all be intended, or the phase stops)" and the number of flips. It lists
   each flip per cell and exits 0 whatever the number.
4. **Numbers** (`PARITY_TEMP0=1 scripts/build_paper_numbers.py`): writes
   `reports/paper_numbers.csv`, `reports/paper_numbers.engine.json` (the engine's file hashes
   and the CSV's own sha256) and, git-ignored, `reports/paper_numbers.overlay_provenance.csv`
   (every recorded cell a temperature-0 re-run replaced). It refuses a re-run file that is
   partial, holds a cell the run did not complete, or ran on other engine code.
5. **Figures and tables** (`scripts/make_paper_figures.py`): `reports/figures/`, and, git-ignored,
   `table2_crossdomain.{md,tex}`.
6. **Statistics** (`scripts/question_clustered_stats.py`): `reports/stats/`, after checking
   that every outcome-class count equals the CSV. The step then runs
   `scripts/alias_control_stats.py` ([The alias control](#the-alias-control)) and
   `scripts/planner_info_stats.py` ([The equal-information run](#the-equal-information-run-of-the-planner)),
   each of which writes nothing when its results file is absent.
7. **Calls and refusals** (`scripts/calls_and_refusals.py`): `reports/calls_and_refusals.csv`,
   the mean LLM calls per query, the in-scope refusal rate and the out-of-scope decline rate of
   the five systems on claude-sonnet-4-5 and on the open-weights model of `local_model/`, over
   the same 162 questions. It checks its claude-sonnet-4-5 counts against the CSV and every
   open-weights cell against `local_model/analysis/per_question.csv`; its docstring defines each
   column.

The counts steps 2 and 3 print are in the commit that last changed the recorded data; they are
not constants of the repository.

**Not regenerated by `run_all.sh`** (each has its own command; none needs an API key unless
stated):

| Output | Command | Note |
|---|---|---|
| `evidence/gold/gold_employee.json` and digest | `python evidence/gold/build_gold_employee.py` | reproduces the committed files byte for byte |
| `evidence/gold/gold_spider_*.json` and digests | `python evidence/gold/build_gold_spider_<db>.py` | needs Spider's SQLite files, which are not shipped |
| `reports/tie_alternatives.json` | `python scripts/detect_ties.py` | Spider ranking ties |
| `reports/tie_alternatives_synthetic.json` | `python scripts/synthetic_ties.py` | employee ties |
| `reports/decline_readings.json` | none | hand readings of 495 no-answer cells (data, not code); `scripts/check_decline_readings.py` checks it against the cells |
| `reports/ablation_data.json` | `python scripts/ablate_checks.py --json reports/ablation_data.json` | the employee and Spider part of the ablation; the paper's table also counts the withheld risk emissions |
| the search-cost counts (Section 3.1) | `python scripts/measure_search_cost.py` | prints, writes nothing |
| `heldout/results/analysis/` | `python heldout/analyse_heldout.py` | see `heldout/README_RELEASE.md` |
| `reports/figures/pipeline.png`, `spider_data_scope.png` | none | hand-drawn diagrams (`reports/figures/README.md`) |

**Cannot be regenerated from this repository**: every risk-domain number (Tables 2 and 5,
Appendix A.1, A.7 and A.14), the paper's pooled in-scope statistics, which include the risk
domain, the self-declaration probe counts of Section 5.4 and Appendix A.13 (their probe trees
are withheld), and any number from a live re-run (live emissions are not exact: the API exposes
no seed).
`scripts/rerun_temp0_batch2.py`, the runner behind the batch-2 files, is shipped but cannot run:
its plan is built from an audit manifest that covers the withheld domain.

## Repository structure

```
gdc/                          The GDC engine, whole: Goal/State, A* search,
                               contract, order, schema bridge, LLM1 goal
                               emission, the primitives executor, and config.
                               Note: the engine is NOT the "planner" BASELINE
                               SYSTEM (adapters/planner_upfront.py), which is
                               an unrelated step-planning baseline that merely
                               shares that identifier string.

evidence/
  eval_run/                   The 5-system harness: adapters/ (one file per
                               system, incl. network.py — GDC's adapter; the
                               "network" string is retained as the system
                               identifier throughout recorded data and
                               scoring, unrelated to any code module name),
                               harness.py, run.py, comparator.py, and
                               supporting config (pricing, caching, refusal
                               toggles).
  gold/                       Independently-authored gold keys + their
                               builders (pandas + stdlib only — zero
                               imports from gdc/, so gold can't
                               be contaminated by the system under test).
  tests/                      Deterministic test suite (pytest).

evaluation/domains/
  employee/                   eval_corpus_employee.json, employees.pkl,
                               schema.json
  spider_wta_1/                eval_corpus_spider_wta_1.json, matches.pkl,
                               schema.json, NOTICE.md
  spider_world_1/               eval_corpus_spider_world_1.json,
                               country.pkl, schema.json, NOTICE.md
  spider_bike_1/                eval_corpus_spider_bike_1.json, trip.pkl,
                               schema.json, NOTICE.md
                               (the Spider directories are CC BY-SA 4.0;
                               see Third-party data below)

results/                      Every results_*.jsonl + its .provenance.json
                               sidecar (the one exception is
                               results_verify_refuse_out_of_scope.jsonl, which
                               has none) — recorded (system, query, seed)
                               cells: emitted Goal, produced result, score.
                               results_temp0_batch2_* is the second
                               temperature-0 re-run; its _employee_alias file
                               is the alias arm, which no reported cell reads.

reports/                      paper_numbers.csv (locked numbers every
                               table/figure derives from),
                               paper_numbers.engine.json (the engine GDC and
                               the planner baseline were replayed on for it),
                               figures/ (PNG figures, .md/.tex tables, a
                               README), stats/ (question-clustered
                               statistics), calls_and_refusals.csv (calls
                               and refusals per system, both models),
                               decline_readings.json, tie_alternatives*.json,
                               ablation_data.json.

heldout/                      The pre-registered held-out Spider run: see
                               heldout/README_RELEASE.md.

local_model/                  The run of the five systems on an open-weights
                               model through Ollama: scripts, patch, recorded
                               cells, scored outputs (local_model/README.md).

scripts/                      Pipeline scripts (rescore, replay, build
                               numbers, build figures, statistics) and the
                               analysis, re-run and live-run scripts used
                               during development.

SPEC.md                       Design notes from an earlier architecture; not
                               the shipped engine (see the docstrings in gdc/).
run_all.sh                    Offline reproduction pipeline (see above).
requirements.txt              Python version ranges.
requirements-lock.txt         Exact versions the release was checked with.
```

The provenance files record `git_commit` and `fix_commits` hashes of the development
repository, whose history is not part of this one, so none of them resolves here. They also
record paths of the development layout (`eval_corpus_employee.json`,
`evidence/gold/gold_employee.json`), made relative before release.

## Where the paper's tables and figures come from

| Paper item | Rows of `reports/paper_numbers.csv` (`fig_table`, domain) | Output |
|---|---|---|
| Table 6 (employee, per tier) | `Table1`, `employee` (strict; tolerant beside it for the in-contract tiers) | `reports/figures/table1_employee.*` |
| Appendix A.3 table (Spider, all outcome classes, strict) | `Fig1/Fig2/Table3`, `spider_pooled`, `in_contract_answer` | CSV only |
| Table 2 (in-scope silent-error, strict and tolerant) | `Table2` for `employee` (and risk), `Fig1/Fig2/Table3` for Spider | `table3_spider_incontract.*` (Spider); `table2_crossdomain.*` (GDC against the strongest baseline per domain, git-ignored) |
| Table 3 (out-of-scope declines and answers) | `Table5` | CSV only |
| Table 3's row "GDC, told it may decline" | `Fig3/Fig4`, each Spider database, tier group `out_of_scope_register_s40on` | `reports/stats/rates.csv`, set `decline_spider_oosr_pooled_s40on` (the pooled rates and their intervals); `reports/stats/comparisons.csv`, the same set (its differences from the four alternatives) |
| Appendix A partitions (correct, silent, refused, failed, crash, timeout) | `Table1`, `Table2`, `Fig1/Fig2/Table3`, `Table5` | CSV only |
| Figure 2 | `Fig1/Fig2/Table3` | `reports/figures/`, listed in its README |
| Appendix A.4 and A.5 tables (GDC on the two Spider boundary tiers) | `Fig3/Fig4`, each Spider database (`Table5` for A.4's pooled row) | CSV only |
| employee limit ties | `Ties` | CSV only |
| Section 5.5 intervals | not CSV rows | `reports/stats/` |
| Section 5.3 calls per question and Section 5.2 refusal rates, both models (the out-of-scope declines are those of Table 3 and Appendix A.9, pooled over employee and Spider) | not CSV rows | `reports/calls_and_refusals.csv` |
| held-out run (Table 4's held-out row, Appendix A.10) | not CSV rows | `heldout/results/analysis/` |

Table 5, Table 2's risk column, the beyond-vocabulary figures of Section 5.4 and Appendix A.1 and
A.14 are risk-domain items. The `fig_table` keys are identifiers, not the paper's numbers: `Table5`
is the paper's Table 3, the out-of-scope table, and `make_paper_figures.py` calls the Haiku table
"table5" in its risk branch. The paper items above follow the submitted paper's numbering.
`reports/figures/README.md`, and the `figure` column that `make_paper_figures.py` writes into
`figures_manifest.csv`, keep an earlier draft's figure numbers: their Figure 4 is the paper's Figure
2, and their Figures 3 and 5 to 8 are not in the paper (the values of Figures 5 and 6 are in the
Appendix A.3 table, of Figure 7 in A.4 and of Figure 8 in A.14). The paper draws its Figure 1 in its
LaTeX source; `pipeline.png` is an earlier drawing of the control flow. Where this table and the
paper disagree, the paper is the authority.

## The alias control

GDC's recorded employee prompt maps "active employees" to the "current" status rule; the four
alternatives' briefing states the rule without that alias. The batch-2 alias arm
(`results/results_temp0_batch2_employee_alias.jsonl`) re-ran the alternatives with the alias added.
The alias control is the converse: GDC (system id `network_noalias`) on the 81 employee in-scope
questions, seeds 0 to 2, temperature 0, under its recorded s31 prompt with only the alias removed.
Its pre-registration, committed before the run, is `review/alias_control_prereg.md`. The runner is
`scripts/run_alias_control.py` (`--dry` makes no API call). The cells are
`results/results_temp0_employee_gdc_noalias.jsonl`; its `.provenance.json` records the prompt sent,
its sha256 and its diff from the recorded prompt, the engine and the cost, and `.spend.json` the
billed ledger. `scripts/score_alias_control.py` replays the cells through the engine and scores them
as every GDC cell is scored; with `--control` it reproduces the reported GDC employee classes.
`scripts/alias_control_stats.py`, run in step 6, writes `reports/stats/alias_control/` (the eight
"active" questions per seed with the pre-registered outcome, the regression check on the other 73
questions, the rates and `headline.csv`) and appends the `alias_control_*` sets to
`reports/stats/comparisons.csv`. No builder reads these files, so no reported cell changes.

## The equal-information run of the planner

The planner writes the steps for GDC's executor in one call, but its prompt carried the shared
briefing rather than GDC's richer domain information. The equal-information run gives it that
information: the planner (system id `planner_gdcinfo`), claude-sonnet-4-5, temperature 0, under its
reported prompt with the briefing replaced by the domain lines of GDC's recorded prompt (s31 for
employee, s45 for Spider and the held-out databases). It covers the 81 employee in-scope questions
(seeds 0 to 2), the 10 held-out answer-tier questions (seeds 0 and 1) and the 27 Spider answer-tier
questions (seeds 0 to 4): 398 cells. Its pre-registration, committed before the run, is
`review/planner_info_prereg.md`. `scripts/planner_info_prompt.py` builds the prompts, and
`review/planner_info_split.md` lists every line of GDC's prompt as kept, mixed (only its domain part
is sent) or out (GDC's output format), with a reason each. The runner is
`scripts/run_planner_info.py` (`--dry` makes no API call). The cells are
`results/results_temp0_planner_gdcinfo.jsonl`; its `.provenance.json` records every prompt sent,
its sha256 and its diff from the reported planner prompt, the engine and the cost, and `.spend.json`
the billed ledger. `scripts/planner_info_stats.py`, run in step 6, replays the cells through the
executor as every planner cell is scored and writes `reports/stats/planner_info/` (the rates, the
per-database table, every question on which the informed planner and GDC differ, `headline.csv`
with the pre-registered outcome). It also appends the `planner_info_*` sets to
`reports/stats/comparisons.csv`. No builder reads these files, so no reported cell changes.

## What's not included

The risk-register domain's underlying data is withheld from this release for
**data-sensitivity reasons, not licensing**: the register's schema was
designed for structural realism against a real-world risk-register format.
Every value in it is fabricated, but the schema and category vocabulary are
close enough to a real reference that we do not release the raw data.

Concretely, withheld: the risk corpus (93 queries), the risk register +
schema, the risk gold key + its builder, and the risk domain's recorded
emissions (`results_off*.jsonl`, `results_t5*.jsonl`, `results_haiku*.jsonl`
and similar).

**What this means in practice:**
- Risk's results are reported in full in the paper (Tables 2 and 5,
  Appendix A.1, A.7 and A.14) exactly as measured.
- `reports/paper_numbers.csv` in this repository is built from the released
  data, so it has no risk-domain rows.
- Risk's results are not independently reproducible from this repository.
- The employee and Spider domains are released in full — corpus, register,
  gold + builder, recorded emissions — and reproduce exactly via
  `./run_all.sh`.
- The scripts `run_all.sh` calls, and the tests, degrade gracefully in the risk domain's
  absence: they print a note (`risk domain data not included in this release — skipping
  ...`) and continue. Some development scripts do not: `scripts/audit_coverage.py` stops
  with an `AttributeError` on the absent risk provenance.

## Requirements

Python 3.12 or later (checked on 3.12.13 and 3.14.6).

- **Offline pipeline** (`./run_all.sh`): `pandas>=3.0`, `numpy>=2.0`, `pyarrow` and
  `matplotlib`. The registers are pandas 3 pickles: pandas 2 and numpy 1 cannot load them,
  and the three Spider registers hold Arrow-backed string columns, so `pyarrow` is required
  to load them. (The engine's filter semantics also differ between pandas 2 and 3.)
- **Tests**: the above plus `pytest`, `anthropic` and `python-dotenv`. The suite imports
  `gdc/config.py`, which imports both; no test calls the API.
- **Live step only**: an `ANTHROPIC_API_KEY`.

`requirements.txt` lists these. The development scripts under `scripts/` import more
(`datasets`, `huggingface_hub` for `survey_spider_candidates.py`; `yaml` for
`portability_live.py`, which also imports a module, `oracles`, that is not in this
repository); those are not installed by either requirements file.

## Rebuilding the gold keys

`evidence/gold/build_gold_employee.py` rebuilds the employee gold from the files in this
repository (it reproduces `gold_employee.json` and its digest byte for byte). The three
Spider builders execute Spider's own gold SQL against Spider's SQLite databases, which are
not part of this release: download Spider from https://yale-lily.github.io/spider and copy
`wta_1.sqlite` and `world_1.sqlite` to `spider/` and `bike_1.sqlite` to `database/bike_1/`
before running them. Without the database a builder prints where it expects the file, exits
non-zero and writes nothing.

## Running the tests

```bash
pytest
```

Tests that need the withheld risk-domain data skip with a stated reason rather than failing.
They need the packages of `requirements.txt` including `anthropic` and `python-dotenv`, which
`gdc/config.py` imports; `gdc/config.py` also calls `load_dotenv()` at import, so a `.env` file in
the working directory or a parent is read when the tests import it.

## License & citation

The code and the files written for this repository are under the MIT License.
See [LICENSE](LICENSE) for the full text. Files derived from the Spider
dataset are under CC BY-SA 4.0 instead; see [Third-party data](#third-party-data).

If you use this work, please cite:

```bibtex
@misc{gdc2026,
  title         = {Structural Refusal for In-Scope Queries: Reliability and Its Limits at the Domain Boundary},
  author        = {Sebastian Mikl},
  year          = {2026},
  eprint        = {[ARXIV_ID_PLACEHOLDER]},
  archivePrefix = {arXiv},
  url           = {https://github.com/sebastian-mikl/goal-directed-compilation},
}
```

Repository: `https://github.com/sebastian-mikl/goal-directed-compilation`

**The arXiv eprint ID above is a placeholder until the paper is
posted** — this README does not guess at it.

## Third-party data

The three Spider domains are built from the Spider dataset:

> Tao Yu, Rui Zhang, Kai Yang, Michihiro Yasunaga, Dongxu Wang, Zifan Li,
> James Ma, Irene Li, Qingning Yao, Shanelle Roman, Zilin Zhang and Dragomir
> Radev. "Spider: A Large-Scale Human-Labeled Dataset for Complex and
> Cross-Domain Semantic Parsing and Text-to-SQL Task." EMNLP 2018.
> https://yale-lily.github.io/spider

Spider is licensed under the Creative Commons Attribution-ShareAlike 4.0
International licence (CC BY-SA 4.0,
https://creativecommons.org/licenses/by-sa/4.0/). The files derived from it
are released under the same licence, not under MIT:

- `evaluation/domains/spider_wta_1/`, `spider_world_1/`, `spider_bike_1/`:
  one table of each database (`matches`, `country`, `trip`) as a pandas
  pickle, its schema description, and a selection of Spider's questions and
  gold SQL with this project's tier labels, expected behaviours and reasons
  added. Each directory's `NOTICE.md` lists the changes.
- `evidence/gold/gold_spider_*.json` and `gold_spider_*_digest.txt`: answers
  computed from those tables.
- the recorded Spider results under `results/` (`results_spider_*` and the
  `wta_1`, `world_1` and `bike_1` re-run files), which quote the questions
  and table values; `results/results_verify_refuse_out_of_scope.jsonl`, which
  holds 18 Spider questions.
- `reports/tie_alternatives.json` (rows of the `country` and `trip` tables) and
  `evidence/tests/test_refuse_out_of_scope_toggle.py` (one Spider question).
- `heldout/`: the same kinds of file for the two held-out databases
  (`heldout/NOTICE.md` lists them).
- `local_model/results/` and `local_model/analysis/`: the recorded open-weights cells, which quote
  the Spider questions and table values, and the scores computed from them.

The employee domain is synthetic and was written for this repository.
