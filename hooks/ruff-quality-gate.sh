#!/bin/bash
# PostToolUse hook: ruff quality gate for Python files
#
# Runs after Edit/Write on .py files:
# 1. ruff format (silent auto-format)
# 2. ruff check --fix (auto-fix what's possible)
# 3. ruff check (report remaining violations)
# 4. If violations remain, exit 2 to block (stderr feedback)
#
# Non-.py files: exit 0 silently.

# Read tool input from stdin
INPUT=$(cat)

# Extract file_path from JSON input
FILE_PATH=$(echo "$INPUT" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null)

# Skip non-Python files
if [[ ! "$FILE_PATH" == *.py ]]; then
    exit 0
fi

# Skip if file doesn't exist (deleted or invalid path)
if [[ ! -f "$FILE_PATH" ]]; then
    exit 0
fi

# Skip test files from strict checking (they have their own rules)
BASENAME=$(basename "$FILE_PATH")

# Ruff config location
RUFF_CONFIG="$HOME/.claude/ruff.toml"
RUFF_ARGS=""
if [[ -f "$RUFF_CONFIG" ]]; then
    RUFF_ARGS="--config $RUFF_CONFIG"
fi

# Ignore pre-existing patterns in legacy code (E402: import order, S110: try-except-pass)
RUFF_ARGS="$RUFF_ARGS --ignore E402,S110,S603"

# Step 1: Auto-format (silent)
ruff format $RUFF_ARGS "$FILE_PATH" 2>/dev/null

# Step 2: Auto-fix what's possible (silent)
ruff check --fix $RUFF_ARGS "$FILE_PATH" 2>/dev/null

# --- Violation collector setup ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VIOLATION_COLLECTOR="$SCRIPT_DIR/violation_collector.py"
VIOLATIONS_JSONL="$SCRIPT_DIR/../data/write_time_violations.jsonl"

# Step 3: Check for remaining violations (JSON format for both display and collection)
RUFF_JSON=$(ruff check $RUFF_ARGS --output-format json "$FILE_PATH" 2>/dev/null)
EXIT_CODE=$?

# Step 3a: Collect ruff violations (exception-safe, never blocks)
if [[ -n "$RUFF_JSON" ]]; then
    echo "$RUFF_JSON" | python "$VIOLATION_COLLECTOR" ruff "$VIOLATIONS_JSONL" 2>/dev/null || true
fi

# Step 3b: If violations found, convert JSON to human-readable for stderr and block
if [[ $EXIT_CODE -ne 0 ]] && [[ -n "$RUFF_JSON" ]]; then
    VIOLATIONS=$(echo "$RUFF_JSON" | python -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for item in data:
        loc = item.get('location', {}) or {}
        row = loc.get('row', '?')
        col = loc.get('column', '?')
        code = item.get('code', '?')
        msg = item.get('message', '')
        fname = item.get('filename', '')
        print(f'{fname}:{row}:{col}: {code} {msg}')
except Exception:
    pass
" 2>/dev/null)
    if [[ -n "$VIOLATIONS" ]]; then
        echo "[RuffQualityGate] Violations found in $BASENAME:" >&2
        echo "$VIOLATIONS" >&2
        echo "[RuffQualityGate] Fix these issues before proceeding." >&2
        exit 2
    fi
fi

# Step 4: Bandit security check (if installed)
if command -v bandit >/dev/null 2>&1; then
    BANDIT_OUTPUT=$(bandit -r "$FILE_PATH" -f json -q 2>/dev/null)
    BANDIT_EXIT=$?

    if [[ -n "$BANDIT_OUTPUT" ]]; then
        # Step 4a: Collect bandit violations (exception-safe, never blocks)
        echo "$BANDIT_OUTPUT" | python "$VIOLATION_COLLECTOR" bandit "$VIOLATIONS_JSONL" 2>/dev/null || true
    fi

    if [[ $BANDIT_EXIT -ne 0 ]] && [[ -n "$BANDIT_OUTPUT" ]]; then
        # Parse JSON to check for HIGH/MEDIUM severity issues
        HAS_ISSUES=$(echo "$BANDIT_OUTPUT" | python -c "
import sys, json
try:
    data = json.load(sys.stdin)
    results = data.get('results', [])
    high_med = [r for r in results if r.get('issue_severity') in ('HIGH', 'MEDIUM')]
    if high_med:
        for r in high_med[:5]:
            print(f\"  {r.get('issue_severity')}: {r.get('issue_text')} (line {r.get('line_number')})\")
        sys.exit(1)
    sys.exit(0)
except Exception:
    sys.exit(0)
" 2>/dev/null)
        PARSE_EXIT=$?

        if [[ $PARSE_EXIT -ne 0 ]]; then
            echo "[BanditSecurityGate] Security issues found in $BASENAME:" >&2
            echo "$HAS_ISSUES" >&2
            echo "[BanditSecurityGate] Fix HIGH/MEDIUM security issues before proceeding." >&2
            exit 2
        fi
    fi
fi

# All clean
exit 0
