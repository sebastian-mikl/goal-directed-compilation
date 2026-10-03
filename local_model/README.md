# The open-weights run: what is in this directory

`local_model/` holds the replication of the five systems on an open-weights model, run locally
through Ollama: the scripts and the patch that ran it, the 880 recorded cells and their provenance,
the scoring script and the scored outputs. It makes no claim about the paper's numbers and none of
it is read by `./run_all.sh`.

## What ran

- Model: `qwen3-coder:30b-a3b-q4_K_M` (Qwen3-Coder 30B, a mixture of experts, 4-bit Q4_K_M), served
  by Ollama as the alias `qwen3-coder-16k:30b-a3b-q4_K_M`, which is the base model with
  `num_ctx 16384` (`Modelfile.16k`; same weights). Every request, for all five systems and GDC's
  Call 1 included, ran at temperature 0 (the alias's own default is 0.7; the client refuses any
  request that is not at 0), on `http://localhost:11434` only (`localguard.py` removes any
  Anthropic key from the environment, neutralises `dotenv` and refuses every non-loopback socket).
- Scope (`full_scope.json`): employee, all 91 questions of the corpus as it then stood (`oor_03`
  was dropped from scoring afterwards; its cells are recorded and not scored), and Spider wta_1 (30),
  world_1 (30) and bike_1 (25); seed 0; five systems; 880 cells. GDC at prompt version `s48`, every
  refuse and decline toggle off (the S40-off configuration), react at 15 iterations, code-agent at 12
  retries, 60 s per query.
- Code: the development tree at commit `8605f52` plus `local_model.patch`, in a scratch copy that
  was not a git checkout. The patch points `gdc/config.py` at the local server, prices the model at
  zero (`pricing.py`) and adds a 60 s wall-clock watchdog on each code-agent `run_python` call
  (`tools_code.py`, `adapters/code_agent.py`). **The watchdog is in this patch only.** This
  repository's engine has none, and neither had the runs on Claude. `tree_manifest.sha256` lists the
  sha256 of the code, corpora, gold and registers of that tree: 184 of its 2,408 entries. The rest
  are the private audit documents (1,967 under `docs/`), the withheld risk-domain files, results
  files and drafts. The patch applies cleanly (dry run) to this repository's four files, but it
  changes engine files that `scripts/parity_overlay.py` pins, so apply it to a copy.
- The watchdog ended one cell: code-agent, employee `oor_04`, seed 0 (`iter 1: agent code did not
  return within 60s`; the agent's code follows Manager ID upwards, and the register's Manager ID
  chains cycle). Python cannot kill a thread, so the watchdog abandons it and the loop kept running
  in that process. Two employee text-to-SQL cells recorded later in the same process, `t1_01` and
  `t1_03` (seed 0), ended on the 60 s SQL bound (`sql error: interrupted`) with queries that run in
  milliseconds in a fresh process. They are kept as recorded.

## Ollama version drift

The reported run used Ollama 0.35.0 throughout; `run_full.sh` refuses to start on any other
version. The Ollama app started as 0.33.3 on 2 October 2026 and updated itself to 0.35.0 at startup,
before the run began. The weights blob (the base model's) and `num_ctx` 16384 are the same under both
versions: the same blob ran at 16384 under 0.33.3 on 1 October. An earlier run of 1 October under
0.33.3 kept its directory in a scratch location that was lost; it is not part of these results, and the
reported run started again from zero (every one of the 880 cells has exactly its model calls in this
run's call logs). The alias's digest under 0.33.3 is not recorded anywhere in the run directory. The run took six sessions (two stopped before writing their provenance block, so the
employee provenance holds the `o2_local_model` block of one session only).

## Files

| File | What it is |
|---|---|
| `Modelfile.16k` | the Ollama alias |
| `local_model.patch` | the patch to the development tree; its sha256 is in every provenance file |
| `localguard.py`, `o2common.py`, `full.py`, `run_full.sh` | the no-Anthropic guard, the shared driver, the full run and its launcher (the three Python files are byte for byte what the provenance hashes) |
| `full_scope.json` | the questions run |
| `smoke.py`, `analyse_smoke.py`, `project.py` | the smoke test (five employee questions), its pass criteria and the run-time projection made from it |
| `tree_manifest.sha256` | filtered, see above |
| `results/results_o2_<domain>.jsonl` and `.provenance.json` | the 880 cells and each domain's provenance; `results_sha256.txt` holds the sha256 of the four `.jsonl` files |
| `analyse_o2.py` | the scoring script (see below) |
| `analysis/{rates,comparisons,per_question}.csv`, `summary.txt` | what it wrote: rates and paired comparisons per set with question-cluster intervals, and the per-question counts |

The per-cell result files are shipped: they are 2.2 MB gzipped (27 MB as files), under the 15 MB the
release allowed. Not shipped: the per-call logs (`calls_*.jsonl`), the per-cell context-loss flags
(`cell_context_*.jsonl`), `sessions.jsonl`, `run_full.log`, `summary.json`, the smoke-test results and
the Ollama log excerpts. They hold local paths and server log lines and are not needed to read a cell.

## Scrub

The four provenance files carried the development tree's text-to-SQL prompt, which quotes one register
column name of the withheld risk-register domain as its example of a quoted identifier, once each.
They went through the release scrub (`scrub_provenance.py`), which replaces it with the public
prompt's `"Unit Price (Net)"`; as everywhere in this repository, `prompts.text_to_sql` then no longer
hashes to the call as sent. The scrub tool does not recognise the scratch-directory paths these
files hold (`/private/tmp/...`, which name the author's account), so they were removed by a second
substitution and `corpus_path` and `gold_path` read `tree/...`. The `.jsonl` rows needed no change
and match `results_sha256.txt`.

## Scoring

`analyse_o2.py` scores the cells as the paper scores the Claude cells, with the development tree's
own functions: a scoring tree (the development tree at `5bf429e` plus the decisions patch), the
temperature-0 Claude numbers of the paper, and the Ollama logs. It cannot run in this repository.
The copy here has its absolute path defaults replaced by placeholders and the author's first name
replaced in the text it writes. Its outputs are in `analysis/`; `summary.txt` has its three
absolute paths replaced by descriptions.

The scoring run printed the two-decimal values of `rates.csv` and `comparisons.csv` to one decimal
in `summary.txt`, so 15 figures there were rounded twice (7/13 = 53.846% printed as 53.9, and 6/13 =
46.154% as 46.1). The copy here keeps the unrounded values and rounds each printed figure once.
`analyse_o2.py --from-shipped`, which runs in this repository, rewrote those lines: it recomputes the
rates and comparisons from `analysis/per_question.csv` and the status counts from `results/` with the
same functions, refuses unless the recomputed `rates.csv` and `comparisons.csv` equal the shipped
ones byte for byte, and replaces only the statistics and status-count lines of `summary.txt`. The
CSVs did not change. `--check` compares without writing; the test suite runs it.

## Re-running

Not possible from this repository alone, and not tested here. It needs the development tree at
`8605f52` (the engine there is laid out as `planner/` and `gdc/`, which `o2common.py` imports), the
patch applied to a copy of it, Ollama 0.35.0 at `localhost:11434` with the alias built from
`Modelfile.16k`, and `O2_CTX=16384`. `O2_CTX=16384 ./run_full.sh --preflight-only` checks the server,
the alias and the tree manifest and runs no cell. No API key is used or needed; the guard refuses to
send anything off the machine.

The Spider-derived rows are CC BY-SA 4.0 (see the top-level README, "Third-party data"). The model's
weights are not distributed here and are under their own licence.
