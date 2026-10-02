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
  echo "--live: this will make real API calls (5 systems x seeds x queries"
  echo "per domain) and will cost money. Recorded emissions already released"
  echo "under results/ are NOT needed for a normal reproduction run — only"
  echo "use --live if you intend to produce fresh emissions from scratch."
  echo
  echo "There is no single combined live-run entry point for every domain:"
  echo "  Spider (wta_1/world_1/bike_1): scripts/run_full_spider_eval.py"
  echo "  Employee: python -m evidence.eval_run.run (harness.py's DEFAULTS"
  echo "            point at the withheld risk paths, so pass --results/"
  echo "            --provenance/--gold/--register/--schema explicitly, or"
  echo "            drive evidence.eval_run.harness.run_cells() directly —"
  echo "            see run_full_spider_eval.py for the pattern to follow)."
  read -r -p "Type 'yes' to print the exact commands and cost estimates: " CONFIRM
  if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 1
  fi
  echo
  echo "[1/6] LIVE — Spider domains:"
  echo "  python scripts/run_full_spider_eval.py"
  echo "  (prints its own cost estimate before making any call; see its"
  echo "  docstring for exact scope — 85 queries x 5 systems x 5 seeds)"
  echo "      Employee domain has no equivalent script in this release; see"
  echo "      the note above. This wrapper does not run either for you —"
  echo "      re-emission is a deliberate, separate, costed action."
  echo
else
  echo "[1/6] LIVE step skipped (offline reproduction from released"
  echo "      emissions). Pass --live for live-run guidance."
  echo
fi

echo "[2/6] Rescoring recorded results under the current comparator (diagnostic; writes nothing) ..."
"$PY" scripts/rescore_all.py
echo

echo "[3/6] Replaying recorded GDC goals through the current engine (diagnostic; writes nothing) ..."
"$PY" scripts/replay_goals.py
echo

echo "[4/6] Building locked numbers -> reports/paper_numbers.csv ..."
PARITY_TEMP0=1 "$PY" scripts/build_paper_numbers.py
echo

echo "[5/6] Building figures + tables -> reports/figures/ ..."
"$PY" scripts/make_paper_figures.py
echo
echo "[6/6] Question-clustered statistics (employee and Spider) -> reports/stats/ ..."
"$PY" scripts/question_clustered_stats.py
echo

echo "================================================================"
echo "Done. reports/paper_numbers.csv, reports/figures/ and reports/stats/"
echo "regenerated for employee + Spider. paper_numbers.csv has no risk-domain"
echo "rows: risk is reported in the paper but its data is not in this release"
echo "(see README.md)."
echo "================================================================"
