#!/usr/bin/env bash
# GDC — offline reproduction pipeline.
#
# Default (no flags): reproduces every published number and figure for the
# employee and Spider domains from the RELEASED recorded emissions. No API
# calls, no cost, no ANTHROPIC_API_KEY needed.
#
#   ./run_all.sh
#
# --live: additionally re-runs the five systems live over the employee and
# Spider corpora, producing NEW emissions before the offline steps (the only
# step that needs an LLM and costs money). Requires ANTHROPIC_API_KEY and
# prompts for confirmation before spending.
#
#   ./run_all.sh --live
#
# Either way this script never touches the risk domain — see the note it
# prints below and README.md's "What's not included" section.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

PY="${PYTHON:-python3}"
LIVE=0
for arg in "$@"; do
  case "$arg" in
    --live) LIVE=1 ;;
    -h|--help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown flag: $arg (see --help)"; exit 2 ;;
  esac
done

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
  echo "[1/5] LIVE — Spider domains:"
  echo "  python scripts/run_full_spider_eval.py"
  echo "  (prints its own cost estimate before making any call; see its"
  echo "  docstring for exact scope — 85 queries x 5 systems x 5 seeds)"
  echo "      Employee domain has no equivalent script in this release; see"
  echo "      the note above. This wrapper does not run either for you —"
  echo "      re-emission is a deliberate, separate, costed action."
  echo
else
  echo "[1/5] LIVE step skipped (offline reproduction from released"
  echo "      emissions). Pass --live for live-run guidance."
  echo
fi

echo "[2/5] Rescoring recorded results under the current comparator ..."
"$PY" scripts/rescore_all.py
echo

echo "[3/5] Replaying recorded GDC goals through the current engine ..."
"$PY" scripts/replay_goals.py
echo

echo "[4/5] Building locked numbers -> reports/paper_numbers.csv ..."
PARITY_TEMP0=1 "$PY" scripts/build_paper_numbers.py
echo

echo "[5/5] Building figures + tables -> reports/figures/ ..."
"$PY" scripts/make_paper_figures.py
echo

echo "================================================================"
echo "Done. reports/paper_numbers.csv and reports/figures/ regenerated"
echo "for employee + Spider. paper_numbers.csv has no risk-domain rows:"
echo "risk is reported in the paper but its data is not in this release"
echo "(see README.md)."
echo "================================================================"
