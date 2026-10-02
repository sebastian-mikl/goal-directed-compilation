# The held-out run: what is in this directory

`heldout/` is the pre-registered held-out Spider run of the paper: the five systems on two Spider
databases that no prompt, engine change or corpus decision was made on (`employee_hire_evaluation`
and `train_station`), at temperature 0, seeds 0 and 1, model `claude-sonnet-4-5`. The
pre-registration is `PREREGISTRATION.md`. It holds the corpora, registers, schemas, gold, the
scripts that built them and ran the run, the 348 recorded cells and their provenance, and the
analysis outputs. Spider-derived files are CC BY-SA 4.0 (`NOTICE.md`).

The run was made in the development tree, at commit `5bf429e`, whose layout differs from this
repository's. Every file under `heldout/` that came from the development tree is unchanged, except
the two `.provenance.json` files under `results/` (scrubbed, see below). `README_RELEASE.md`,
`NOTICE.md` and `check_frozen_files.py` are new. One file is renamed: `build_gold.py` is shipped
as `build_gold_heldout.py`, byte for byte, because `build_gold.py` is the name of the withheld
risk-domain gold builder. FROZEN.json, `freeze.py`, `validate_offline.py` and
`PREREGISTRATION.md` still call it `build_gold.py`; `check_frozen_files.py` maps the name.

## Re-running the analysis offline

```bash
python heldout/analyse_heldout.py
```

It needs numpy and the packages in `requirements.txt`, makes no API call, reads
`heldout/results/heldout_<db>.jsonl`, re-scores every cell with the frozen comparator (strict, and
the tie-tolerant rule, which here has no tied row), refuses to analyse an incomplete run, and
writes `heldout/results/analysis/{rates.csv, comparisons.csv, per_question.csv,
gdc_out_of_contract.csv, summary.txt}`. Run on the shipped results, it reproduces the five shipped
files byte for byte (checked on 2 October 2026, after the scrub). The analysis reads no
provenance file, so the scrub does not touch it.

`heldout/run_heldout.py` imports its runner helpers from `scripts/rerun_temp0_batch2.py`, which
this release carries with two edits for this layout (last section below).

## What cannot be re-run here

- `heldout/freeze.py --check` stops with a `FileNotFoundError` at the first frozen file this
  release does not ship (`combined_data.parquet`). FROZEN.json hashes development-layout paths
  (`planner/*.py`, `wta_1/matches.pkl`, `results_spider_wta_1_postfix_s40off.provenance.json` at the
  top level) and four files that are not shipped (the Spider question tables
  `combined_data.parquet` and `dev_data.parquet`, and the two Spider `.sqlite` files). Use
  `python heldout/check_frozen_files.py` instead: it looks each FROZEN.json entry up at its path in
  this layout and prints MATCH, DIFFERS or ABSENT. Of the 44 files, 29 match, 11 differ and 4 are
  absent; every entry under `heldout/` matches, and so do the six Spider registers and schemas at
  their new paths. The 11 that differ are development-tree files this release carries in edited form
  (comments, docstrings, risk vocabulary removed, paths in this layout), the three `s45` reference
  provenance files (absolute paths made relative before release) and
  `scripts/rerun_temp0_batch2.py` (the two edits below). The 23 engine hashes in FROZEN.json belong
  to the development layout; this release's engine is pinned by `PORTED_ENGINE` in
  `scripts/parity_overlay.py`.
- `run_heldout.py --dry` and `--run`, `build_domains.py`, `build_gold_heldout.py` and `validate_offline.py`
  need the two Spider `.sqlite` files (not shipped) and, for `--run`, an API key. The gold files
  and registers they produced are shipped.

## What was scrubbed, and why FROZEN.json hashes of those files do not match

The two files `results/heldout_<db>.provenance.json` were run through the release scrub
(`scrub_provenance.py`, the tool applied to every provenance file in this repository):

1. The development tree's text-to-SQL prompt quoted one register column name of the withheld
   risk-register domain as its example of a quoted identifier. The recorded prompt in each provenance
   file (`prompts.text_to_sql`) carried it once. It is replaced by the example this repository's
   prompt uses, `"Unit Price (Net)"`. The `.jsonl` rows did not contain it.
2. Two absolute local paths per file (`corpus_path`, `gold_path`) are made relative.

Consequences. `prompts.text_to_sql` no longer hashes to the text-to-SQL call as it was sent, so the
text-to-SQL entries of `heldout.sent_calls_by_sha256` cannot be checked against the stored prompt.
The sent text differed from the stored one by that example only. FROZEN.json does not list the
held-out provenance files (it was written before any cell ran), so no FROZEN.json hash of a scrubbed
held-out file exists to mismatch; each provenance file's `heldout.frozen_sha256` is the hash of
FROZEN.json itself, which is unchanged. The three `s45` reference provenance files that FROZEN.json
does list (`results/results_spider_<db>_postfix_s40off.provenance.json`) differ from their frozen
hashes for the same kind of reason: their absolute paths were made relative before release.

## The one deviation: FROZEN.json was regenerated before the first call

`FROZEN.json` here is the file the run used (sha256 `286ac45a...`). It was regenerated on 2 October
2026, before the first held-out call, because commit `5bf429e` changed the cost bound in
`scripts/rerun_temp0_batch2.py`, one of the frozen files. `FROZEN.json.pre_batch2_refix.bak` is the
file as committed at the pre-registration commit (sha256 `f63ad3f3...`). The two differ in one entry:
`scripts/rerun_temp0_batch2.py`, `31dd18f7...` before and `73e388a2...` after. The held-out runner
does not call the changed function (`_price`). Both provenance files record the regenerated file's
hash and `git_commit` `5bf429e`. `FROZEN.json` still names `8605f52` as `frozen_commit`.
`PREREGISTRATION.md` section 12 records the other deviation (the primary metric, changed on 1 October
before any call) and not this one; the document is hashed in FROZEN.json and is shipped unedited.

## Edits to `scripts/rerun_temp0_batch2.py`

The copy in `scripts/` is the development tree's file with two changes, so that it imports without
the withheld risk domain: `RISK_RERUN_S31` is defined in the file instead of imported from
`parity_overlay`, and `DOMAIN_SOURCES["risk"]` is read with `.get`. Its `--dry` and `--run` cannot
run here: the plan is built from an audit manifest that covers the withheld domain. Its hash is one
of the 11 that differ in `check_frozen_files.py`.
