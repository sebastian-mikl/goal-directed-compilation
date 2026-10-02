# GDC — Goal-Directed Compilation

Code, data, and recorded evaluation results for the GDC paper: a deterministic
goal→plan→execute engine for tabular analytical queries, compared against
four LLM baselines (a step-planning LLM, a react/tool-use loop, a pandas
code-writing agent, and a text-to-SQL agent) across three domains.

**What's not included, upfront:** the risk-register domain's underlying data
(register, corpus, gold, and recorded emissions) is withheld from this public
release for data-sensitivity reasons — the register's schema was designed for
structural realism against a real-world risk-register format, and while every
value in it is fabricated, the schema and category vocabulary are close
enough to a real reference that we do not release the raw data. Risk's
results are reported in full in the paper (Tables 1a and 4, Figure 8 and
Appendix A) exactly as measured. They are not in this repository's
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
                               sidecar — recorded (system, query, seed)
                               cells: emitted Goal, produced result, score.

reports/                      Generated output: paper_numbers.csv (locked
                               numbers every table/figure derives from),
                               paper_numbers.engine.json (the engine GDC and
                               the planner baseline were replayed on for it)
                               and figures/ (PNG figures + .md/.tex tables).

scripts/                      Pipeline scripts (rescore, replay, build
                               numbers, build figures) and analysis/live-run
                               scripts used during development.

SPEC.md                       Engine specification.
run_all.sh                    Offline reproduction pipeline (see above).
requirements.txt              Python dependencies.
```

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
- Risk's results are reported in full in the paper (Tables 1a and 4,
  Figure 8 and Appendix A) exactly as measured.
- `reports/paper_numbers.csv` in this repository is built from the released
  data, so it has no risk-domain rows.
- Risk's results are not independently reproducible from this repository.
- The employee and Spider domains are released in full — corpus, register,
  gold + builder, recorded emissions — and reproduce exactly via
  `./run_all.sh`.
- Every pipeline script degrades gracefully in the risk domain's absence: it
  prints a note (`risk domain data not included in this release — skipping
  ...`) and continues rather than erroring, for both the tests and the
  reproduction pipeline.

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

## Running the tests

```bash
pytest
```

Tests requiring the withheld risk-domain data skip cleanly with a stated
reason rather than failing.

## License & citation

The code and the files written for this repository are under the MIT License.
See [LICENSE](LICENSE) for the full text. Files derived from the Spider
dataset are under CC BY-SA 4.0 instead; see [Third-party data](#third-party-data).

If you use this work, please cite:

```bibtex
@article{gdc2026,
  title   = {Structural Refusal for In-Scope Queries: Reliability and Its Limits at the Domain Boundary},
  author  = {Sebastian Mikl},
  year    = {2026},
  eprint  = {[ARXIV_ID_PLACEHOLDER]},
  url     = {https://github.com/sebastian-mikl/goal-directed-compilation},
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
  and table values.

The employee domain is synthetic and was written for this repository.
