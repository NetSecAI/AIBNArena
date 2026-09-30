#!/usr/bin/env bash
# Run the LangChain baseline matrix on SME01-small:
#   2 models x 3 scenarios x 3 deterministic seeds = 18 episodes.
#
# Safety contract: each model gets a fresh process, any stale listener on the
# configured port is stopped, and PID/model identity is checked before every
# episode. Contradictory result provenance aborts the matrix immediately.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${IBN_EXPERIMENT_PYTHON:-$REPO_ROOT/.venv/bin/python}"
SUT_HOST="${IBN_EXPERIMENT_SUT_HOST:-127.0.0.1}"
SUT_PORT="${IBN_EXPERIMENT_SUT_PORT:-8003}"
SUT_URL="http://$SUT_HOST:$SUT_PORT"
SUT_RUNTIME_URL="$SUT_URL/.well-known/sut-runtime.json"
EXPECTED_SUT_IDENTITY="langchain_agent"
EXECUTION_BUDGET_SECONDS="${IBN_EXPERIMENT_BUDGET_SECONDS:-400}"
MAX_TOKENS="${IBN_EXPERIMENT_MAX_TOKENS:-800}"
RECURSION_LIMIT="${IBN_EXPERIMENT_RECURSION_LIMIT:-1000}"
PORT_RELEASE_TIMEOUT_SECONDS="${IBN_EXPERIMENT_PORT_RELEASE_TIMEOUT_SECONDS:-10}"
STARTUP_TIMEOUT_SECONDS="${IBN_EXPERIMENT_STARTUP_TIMEOUT_SECONDS:-60}"

MODELS=(
  "openai/gpt-4o-mini"
  "openai/gpt-5.4"
)

SEEDS=(9 17 29)

SCENARIOS=(
  "connectivity.disable_interface.m1"
  "connectivity.remove_ip.m1"
  "qos.link_impairment.m1"
)

TOPOLOGY="scenarios/topologies/sme_leaf_spine_dmz_small.yaml"
HEALTHY_STATE="benchmarks/testbeds/containerlab/sme01-small/states/healthy.json"
CONNECTIVITY_CONFIG="benchmarks/configs/experiments/connectivity-smoke.toml"
QOS_CONFIG="benchmarks/configs/experiments/qos-smoke.toml"
CAMPAIGN_ID="${IBN_EXPERIMENT_CAMPAIGN_ID:-langchain-baseline-$(date -u +%Y%m%dT%H%M%SZ)}"
RESULT_DIRECTORY="${IBN_EXPERIMENT_RESULT_DIR:-reports/campaigns/runs/$CAMPAIGN_ID}"
REPORT_DIRECTORY="${IBN_EXPERIMENT_REPORT_DIR:-$RESULT_DIRECTORY/reports}"
LOG_DIRECTORY="$RESULT_DIRECTORY/server-logs"

SUT_PID=""
failed_commands=0
completed_commands=0

if [[ ! -x "$PYTHON" ]]; then
  echo "Python executable not found: $PYTHON" >&2
  exit 2
fi

for required_command in curl lsof; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "Required command not found: $required_command" >&2
    exit 2
  fi
done

mkdir -p "$LOG_DIRECTORY"

slugify() {
  printf '%s' "$1" | tr '/.' '--' | tr -cd '[:alnum:]_-'
}

listener_pids() {
  # Check every local bind address: a listener on 0.0.0.0 conflicts with
  # 127.0.0.1 even though its textual host is different.
  lsof -nP -t -iTCP:"$SUT_PORT" -sTCP:LISTEN 2>/dev/null | sort -u || true
}

process_is_running() {
  local pid="$1"
  local state
  if ! kill -0 "$pid" 2>/dev/null; then
    return 1
  fi
  state="$(ps -p "$pid" -o stat= 2>/dev/null || true)"
  [[ -n "$state" && "$state" != Z* ]]
}

terminate_pid() {
  local pid="$1"
  local deadline

  if ! process_is_running "$pid"; then
    return 0
  fi

  kill -TERM "$pid" 2>/dev/null || true
  deadline=$((SECONDS + PORT_RELEASE_TIMEOUT_SECONDS))
  while process_is_running "$pid" && (( SECONDS < deadline )); do
    sleep 0.2
  done

  if process_is_running "$pid"; then
    echo "PID $pid ignored SIGTERM; sending SIGKILL" >&2
    kill -KILL "$pid" 2>/dev/null || true
  fi

  deadline=$((SECONDS + 2))
  while process_is_running "$pid" && (( SECONDS < deadline )); do
    sleep 0.1
  done
  if process_is_running "$pid"; then
    echo "PID $pid is still alive after SIGKILL" >&2
    return 1
  fi
}

release_sut_port() {
  local -a pids
  local -a remaining
  local pid

  mapfile -t pids < <(listener_pids)
  if (( ${#pids[@]} == 0 )); then
    echo "Port $SUT_PORT is free"
    return 0
  fi

  echo "Port $SUT_PORT is occupied; stopping the existing listener(s):" >&2
  for pid in "${pids[@]}"; do
    ps -p "$pid" -o pid=,ppid=,comm=,args= >&2 || true
    terminate_pid "$pid"
  done

  # Reap a previous child SUT if it was one of the listeners.
  if [[ -n "$SUT_PID" ]]; then
    wait "$SUT_PID" 2>/dev/null || true
    SUT_PID=""
  fi

  mapfile -t remaining < <(listener_pids)
  if (( ${#remaining[@]} > 0 )); then
    echo "Cannot release TCP port $SUT_PORT; remaining PID(s): ${remaining[*]}" >&2
    return 1
  fi
  echo "Port $SUT_PORT released"
}

stop_sut() {
  local pid="$SUT_PID"
  if [[ -n "$pid" ]]; then
    terminate_pid "$pid"
    wait "$SUT_PID" 2>/dev/null || true
  fi
  SUT_PID=""
}

listener_is_owned_by_sut() {
  local pid
  while IFS= read -r pid; do
    if [[ "$pid" == "$SUT_PID" ]]; then
      return 0
    fi
  done < <(listener_pids)
  return 1
}

validate_runtime_identity() {
  local expected_model="$1"
  "$PYTHON" -c '
import json
import sys

expected_identity, expected_model, expected_pid = sys.argv[1:]
try:
    payload = json.load(sys.stdin)
except Exception as exc:
    raise SystemExit(f"invalid runtime identity document: {exc}")

expected = {
    "schema_version": "1.0",
    "sut_identity": expected_identity,
    "configured_model": expected_model,
    "process_id": int(expected_pid),
}
mismatches = [
    f"{key}: expected {value!r}, received {payload.get(key)!r}"
    for key, value in expected.items()
    if payload.get(key) != value
]
if mismatches:
    raise SystemExit("runtime identity mismatch: " + "; ".join(mismatches))
' "$EXPECTED_SUT_IDENTITY" "$expected_model" "$SUT_PID"
}

assert_sut_runtime() {
  local expected_model="$1"
  local runtime_document

  if [[ -z "$SUT_PID" ]] || ! kill -0 "$SUT_PID" 2>/dev/null; then
    echo "Expected SUT process is not running" >&2
    return 1
  fi
  if ! listener_is_owned_by_sut; then
    echo "PID $SUT_PID does not own TCP port $SUT_PORT" >&2
    return 1
  fi
  if ! curl --silent --fail --max-time 2 \
    "$SUT_URL/.well-known/agent-card.json" >/dev/null; then
    echo "Agent Card is unavailable at $SUT_URL" >&2
    return 1
  fi
  if ! runtime_document="$(curl --silent --fail --max-time 2 "$SUT_RUNTIME_URL")"; then
    echo "Runtime identity is unavailable at $SUT_RUNTIME_URL" >&2
    return 1
  fi
  printf '%s' "$runtime_document" | validate_runtime_identity "$expected_model"
}

validate_result_provenance() {
  local result_file="$1"
  local expected_model="$2"
  "$PYTHON" -c '
import json
import sys
from pathlib import Path

result_path = Path(sys.argv[1])
expected_model = sys.argv[2]
payload = json.loads(result_path.read_text())
provenance = payload.get("provenance") or {}
observed = {
    "configured_model": provenance.get("configured_model"),
    "sut_reported_model": provenance.get("sut_reported_model"),
    "provider_reported_model": provenance.get("provider_reported_model"),
}
mismatches = [
    f"{key}: expected {expected_model!r}, received {value!r}"
    for key, value in observed.items()
    if (
        key in {"configured_model", "sut_reported_model"}
        and value != expected_model
    ) or (
        key == "provider_reported_model"
        and value is not None
        and value != expected_model
    )
]
if mismatches:
    raise SystemExit(
        f"invalid model provenance in {result_path}: " + "; ".join(mismatches)
    )
' "$result_file" "$expected_model"
}

start_sut() {
  local model="$1"
  local model_slug
  local log_file
  local attempt
  model_slug="$(slugify "$model")"
  log_file="$LOG_DIRECTORY/langchain-$model_slug.log"

  echo
  echo "Starting LangChain SUT: $model"
  echo "Server log: $log_file"

  stop_sut
  release_sut_port

  "$PYTHON" -m sut.langchain_agent.a2a_server \
    --host "$SUT_HOST" \
    --port "$SUT_PORT" \
    --model "$model" \
    --max-tokens "$MAX_TOKENS" \
    --max-execution-seconds "$EXECUTION_BUDGET_SECONDS" \
    --recursion-limit "$RECURSION_LIMIT" \
    --scenario-topology "$TOPOLOGY" \
    --healthy-state "$HEALTHY_STATE" \
    >"$log_file" 2>&1 &
  SUT_PID=$!

  # Readiness is accepted only from the PID and model started above.
  for ((attempt = 1; attempt <= STARTUP_TIMEOUT_SECONDS; attempt++)); do
    if ! kill -0 "$SUT_PID" 2>/dev/null; then
      echo "SUT exited during startup. See $log_file" >&2
      return 1
    fi
    if curl --silent --fail --max-time 2 "$SUT_RUNTIME_URL" >/dev/null; then
      if assert_sut_runtime "$model"; then
        echo "SUT ready at $SUT_URL (pid=$SUT_PID, model=$model)"
        return 0
      fi
      echo "A listener answered, but its runtime identity is invalid; aborting" >&2
      stop_sut
      return 1
    fi
    sleep 1
  done

  echo "SUT did not become ready within $STARTUP_TIMEOUT_SECONDS seconds. See $log_file" >&2
  stop_sut
  return 1
}

run_episode() {
  local model="$1"
  local scenario="$2"
  local seed="$3"
  local config="$CONNECTIVITY_CONFIG"
  local model_slug
  local scenario_slug
  local experiment_id
  local marker
  local benchmark_status=0
  local -a result_files

  if [[ "$scenario" == qos.* ]]; then
    config="$QOS_CONFIG"
  fi

  assert_sut_runtime "$model"

  model_slug="$(slugify "$model")"
  scenario_slug="$(slugify "$scenario")"
  experiment_id="langchain-${model_slug}-${scenario_slug}-seed-${seed}"

  echo
  echo "[$((completed_commands + failed_commands + 1))/18] model=$model scenario=$scenario seed=$seed"

  marker="$(mktemp)"
  if "$PYTHON" benchmarks/run.py \
    --config "$config" \
    --sut-url "$SUT_URL" \
    --scenario-id "$scenario" \
    --seed "$seed" \
    --experiment-id "$experiment_id" \
    --result-dir "$RESULT_DIRECTORY" \
    --report-dir "$REPORT_DIRECTORY" \
    --execution-budget-seconds "$EXECUTION_BUDGET_SECONDS"; then
    benchmark_status=0
  else
    benchmark_status=$?
  fi

  mapfile -t result_files < <(
    find "$RESULT_DIRECTORY" -maxdepth 1 -type f \
      -name "$experiment_id-*.json" -newer "$marker" -print
  )
  rm -f "$marker"

  if (( ${#result_files[@]} != 1 )); then
    failed_commands=$((failed_commands + 1))
    echo "Expected exactly one new result for $experiment_id; found ${#result_files[@]}" >&2
    return 1
  fi

  if ! validate_result_provenance "${result_files[0]}" "$model"; then
    failed_commands=$((failed_commands + 1))
    echo "Stopping the matrix to prevent contaminated model results" >&2
    return 1
  fi

  echo "Validated result provenance: ${result_files[0]}"
  if (( benchmark_status == 0 )); then
    completed_commands=$((completed_commands + 1))
  else
    # A lifecycle command can fail while still producing a valid, attributable
    # result. Count it and continue; identity/provenance failures abort above.
    failed_commands=$((failed_commands + 1))
  fi
}

main() {
  trap stop_sut EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  for model in "${MODELS[@]}"; do
    start_sut "$model"
    for seed in "${SEEDS[@]}"; do
      for scenario in "${SCENARIOS[@]}"; do
        run_episode "$model" "$scenario" "$seed"
      done
    done
    stop_sut
  done

  echo
  echo "Experiment matrix finished"
  echo "Successful commands: $completed_commands"
  echo "Failed commands: $failed_commands"
  echo "Campaign: $CAMPAIGN_ID"
  echo "Results: $RESULT_DIRECTORY/"

  if (( failed_commands > 0 )); then
    return 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
