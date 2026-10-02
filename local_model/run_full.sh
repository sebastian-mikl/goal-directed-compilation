#!/bin/bash
# O2.c full run on the local model: the Ollama alias qwen3-coder-16k:30b-a3b-q4_K_M
# (FROM qwen3-coder:30b-a3b-q4_K_M, PARAMETER num_ctx 16384; ./Modelfile.16k) at
# localhost:11434, temperature 0 on every request (the alias's default 0.7 is overridden).
# Scope: employee (91 qids) + Spider wta_1 (30), world_1 (30), bike_1 (25); seed 0; all five
# systems; 880 cells; the S40-off configuration (all refuse/decline toggles off); the shipped
# prompts of the current code (GDC s48).
# Resumable: re-run the same command; completed cells are skipped, an aborted cell retries.
#
# Required: O2_CTX=16384. The preflight refuses to start unless the alias's num_ctx and the
# loaded runner's context are both 16384, its weights are the base model's, the tree matches
# tree_manifest.sha256, and the smoke test in ./smoke_ctx16k passed with no context loss.
# Each completed cell gets a context-loss flag in results/cell_context_<domain>.jsonl
# (dropped messages, input truncation or a mid-reply context shift; "[CONTEXT LOSS]" in the log).
#
#   cd <this dir> && O2_CTX=16384 nohup ./run_full.sh > results/run_full.log 2>&1 &
#   O2_CTX=16384 ./run_full.sh --preflight-only      # checks only, no cells
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
cd "$HERE"
mkdir -p results

# no-Anthropic guard, shell level: no key, no proxy, base URL forced to the local server
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export ANTHROPIC_BASE_URL=http://localhost:11434
export NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1
export PYTHONDONTWRITEBYTECODE=1
unset REFUSE_UNSUPPORTED REFUSE_OUT_OF_SCOPE_DATA DECLINE_AFFORDANCE EMISSION_MODEL MODEL_CALL1 MODEL_CALL2 MODEL_CALL3 PARITY_TEMP0 PARTIAL_BATCH2

if [ "${O2_CTX:-}" != "16384" ]; then echo "set O2_CTX=16384 (got '${O2_CTX:-}')"; exit 2; fi

# the server must be the local Ollama and nothing else
# (0.33.3 -> 0.35.0: the server auto-updated mid-project; model digest, weights blob and
# num_ctx were independently reverified unchanged under 0.35.0 before this bump)
v=$(curl -s --max-time 5 http://localhost:11434/api/version || true)
case "$v" in
  *'"0.35.0"'*) ;;
  *) echo "Ollama 0.35.0 not answering at localhost:11434 (got: ${v:-nothing})"; exit 2;;
esac

echo "O2 full run start $(date -u +%FT%TZ)  model qwen3-coder-16k:30b-a3b-q4_K_M  num_ctx 16384  patch sha256 $(shasum -a 256 local_model.patch | cut -d' ' -f1)  source commit 8605f52"
# caffeinate -i: keep the laptop from idle-sleeping for the length of the run
exec caffeinate -i "$PY" -u full.py "$@"
