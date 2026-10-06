#!/usr/bin/env bash
# GDC — offline reproduction pipeline.
#
# Default (no flags): rebuilds the numbers, figures and tables of the employee and
# Spider domains from the RELEASED recorded emissions. No API calls, no cost, no
# ANTHROPIC_API_KEY. It needs the environment of requirements-lock.txt (README.md,
# "Quick start") and stops at once, before writing anything, if Python or a package
# is missing or too old.
#
#   ./run_all.sh
#
# --live: PRINTS the commands that produce NEW emissions (the only step that needs an
# LLM and costs money) and runs none of them. Requires ANTHROPIC_API_KEY and asks
# for confirmation first.
#
#   ./run_all.sh --live
#
# Either way this script never touches the risk domain: see the note it prints below
# and README.md's "What's not included". README.md also says what the script
# regenerates and what it does not.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PY="${PYTHON:-python3}"
LIVE=0
for arg in "$@"; do
  case "$arg" in
    --live) LIVE=1 ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown flag: $arg (see --help)"; exit 2 ;;
  esac
done

# Preflight: the registers are pandas 3 / numpy 2 pickles and the three Spider registers
# hold Arrow-backed strings, so an environment that lacks pyarrow, or holds pandas 2 or
# numpy 1, otherwise fails inside step 3 with an error that does not name the cause.
if ! "$PY" - <<'PYCHECK'
import sys
problems = []
if sys.version_info < (3, 12):
    problems.append("Python 3.12 or later (this is %d.%d)" % sys.version_info[:2])
def need(mod, minimum=None, why=""):
    try:
        m = __import__(mod)
    except ImportError:
        problems.append("%s is not installed%s" % (mod, why))
        return
    if minimum:
        have = tuple(int(x) for x in m.__version__.split(".")[:2] if x.isdigit())
        if have < minimum:
            problems.append("%s >= %s (this is %s)%s" % (mod, ".".join(map(str, minimum)), m.__version__, why))
need("pandas", (3, 0), ": the registers are pandas 3 pickles")
need("numpy", (2, 0), ": the registers are numpy 2 pickles")
need("pyarrow", None, ": the Spider registers hold Arrow-backed strings")
need("matplotlib")
if problems:
    sys.stderr.write("run_all.sh: the Python environment is not the released one:\n")
    for p in problems:
        sys.stderr.write("  - " + p + "\n")
    sys.stderr.write("Create it as README.md's Quick start says: python3.12 -m venv .venv, "
                     "then pip install -r requirements-lock.txt.\n")
    sys.exit(1)
PYCHECK
then
  exit 3
fi

echo "================================================================"
echo "Note: the risk-register domain is not included in this public"
echo "release (data-sensitivity reasons — see README.md). Employee and"
echo "Spider domains run in full below."
echo "================================================================"
echo

if [ "$LIVE" = "1" ]; then
  if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo "ERROR: --live requires ANTHROPIC_API_KEY to be set. Aborting." >&2
    exit 2
  fi
  echo "--live: this PRINTS the commands that make real API calls (5 systems x seeds x"
  echo "queries per domain) and cost money, and runs none of them. The recorded emissions"
  echo "already released under results/ are NOT needed for a normal reproduction run; use"
  echo "these commands only to produce fresh emissions from scratch."
  echo
  echo "There is no single combined live-run entry point for every domain."
  read -r -p "Type 'yes' to print the commands: " CONFIRM
  if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 1
  fi
  echo
  echo "[1/7] LIVE, commands only:"
  echo "  Spider (wta_1, world_1, bike_1). It prints its wiring check and a cost estimate and"
  echo "  then runs, as soon as ANTHROPIC_API_KEY is set, over results/results_spider_<db>.jsonl"
  echo "  (resumable: cells already recorded there are skipped):"
  echo "    python scripts/run_full_spider_eval.py"
  echo "  Employee (3 seeds, as reported; a new results file):"
  echo "    python -m evidence.eval_run.run \\"
  echo "      --corpus evaluation/domains/employee/eval_corpus_employee.json \\"
  echo "      --gold evidence/gold/gold_employee.json \\"
  echo "      --register evaluation/domains/employee/employees.pkl \\"
  echo "      --schema evaluation/domains/employee/schema.json --seeds 0 1 2 \\"
  echo "      --results results/results_employee_new.jsonl \\"
  echo "      --provenance results/results_employee_new.provenance.json"
  echo "  Both run the reported protocol: temperature 0, code-agent 12 retries (13 calls)."
  echo "  Re-emission is a deliberate, separate, costed action."
  echo
else
  echo "[1/7] LIVE step skipped (offline reproduction from released"
  echo "      emissions). Pass --live for live-run guidance."
  echo
fi

echo "[2/7] Rescoring recorded results under the current comparator (diagnostic; writes nothing) ..."
"$PY" scripts/rescore_all.py
echo

echo "[3/7] Replaying recorded GDC goals through the current engine (diagnostic; writes nothing) ..."
"$PY" scripts/replay_goals.py
echo

echo "[4/7] Building locked numbers -> reports/paper_numbers.csv ..."
PARITY_TEMP0=1 "$PY" scripts/build_paper_numbers.py
echo

echo "[5/7] Building figures + tables -> reports/figures/ ..."
"$PY" scripts/make_paper_figures.py
echo
echo "[6/7] Question-clustered statistics (employee and Spider) -> reports/stats/ ..."
"$PY" scripts/question_clustered_stats.py
# The alias control (review/alias_control_prereg.md): reports/stats/alias_control/ and the
# alias_control_* rows of comparisons.csv. Writes nothing when its results file is absent.
"$PY" scripts/alias_control_stats.py
# The equal-information run of the planner (review/planner_info_prereg.md): reports/stats/planner_info/
# and the planner_info_* rows of comparisons.csv. Writes nothing when its results file is absent.
"$PY" scripts/planner_info_stats.py
# The error classification (review/error_classification_prereg.md): reports/stats/error_classification/.
# Writes nothing when review/error_classification.csv is absent.
"$PY" scripts/error_classification_stats.py
echo

echo "[7/7] Calls and refusals per design, claude-sonnet-4-5 and the open-weights model -> reports/calls_and_refusals.csv ..."
"$PY" scripts/calls_and_refusals.py
echo

echo "================================================================"
echo "Done. reports/paper_numbers.csv, reports/figures/, reports/stats/ and"
echo "reports/calls_and_refusals.csv regenerated for employee + Spider (the last"
echo "also for the open-weights run of local_model/). paper_numbers.csv has no risk-domain"
echo "rows: risk is reported in the paper but its data is not in this release"
echo "(see README.md)."
echo "================================================================"
