#!/bin/bash
# Async wrapper for test_behavior_guard.sh
#
# Fire-and-forget runner invoked by auto-test-runner.js. Runs the full
# guard smoke test suite (~145s), then atomically writes results to
# .last-guard-test-result.json so subsequent hook invocations can report
# the previous outcome via stderr.
#
# Lock: .guard-test-running.lock prevents overlapping runs. The caller
# (auto-test-runner.js) should check for this file before spawning.
#
# Exit codes here are not observed by the hook (detached), but are used
# by this script to derive the exit_code field in the result JSON.

set -u

HOOKS_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_SCRIPT="$HOOKS_DIR/test_behavior_guard.sh"
OUTPUT_LOG="$HOOKS_DIR/.last-guard-test-output.log"
RESULT_JSON="$HOOKS_DIR/.last-guard-test-result.json"
LOCK_FILE="$HOOKS_DIR/.guard-test-running.lock"
RESULT_TMP="$RESULT_JSON.tmp.$$"

# Ensure lock is cleaned up on any exit path
cleanup() {
    rm -f "$LOCK_FILE" "$RESULT_TMP" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

START_TS=$(date +%s)

# Run the actual test suite. Capture all output to log.
bash "$TEST_SCRIPT" > "$OUTPUT_LOG" 2>&1
EXIT_CODE=$?

END_TS=$(date +%s)
DURATION=$((END_TS - START_TS))

# Extract the "Results: N passed, M failed, K skipped" line.
# Use grep + tail to find the last matching line (most recent summary).
SUMMARY=$(grep -E '^Results:' "$OUTPUT_LOG" | tail -n 1)
if [ -z "$SUMMARY" ]; then
    SUMMARY="results unparseable"
fi

# Escape double quotes and backslashes in summary for safe JSON embedding.
SUMMARY_ESCAPED=$(printf '%s' "$SUMMARY" | sed 's/\\/\\\\/g; s/"/\\"/g')

TIMESTAMP_MS=$((END_TS * 1000))

# Write result JSON atomically: tmp file then rename.
cat > "$RESULT_TMP" <<EOF
{
  "timestamp": $TIMESTAMP_MS,
  "exit_code": $EXIT_CODE,
  "summary": "$SUMMARY_ESCAPED",
  "duration_sec": $DURATION
}
EOF

mv -f "$RESULT_TMP" "$RESULT_JSON"

exit "$EXIT_CODE"
