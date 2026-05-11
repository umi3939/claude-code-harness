#!/bin/bash
# Tests for bandit integration in ruff-quality-gate.sh
# Verifies that bandit runs after ruff and detects security issues

SCRIPT_PATH="$HOME/.claude/hooks/ruff-quality-gate.sh"
PASS=0
FAIL=0
TMPDIR=$(mktemp -d)

check() {
    local desc="$1"
    local condition="$2"
    if [ "$condition" = "true" ]; then
        echo "  PASS: $desc"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Bandit Integration Tests ==="

# T1: bandit is installed
if command -v bandit >/dev/null 2>&1; then
    check "T1: bandit is installed" "true"
else
    check "T1: bandit is installed" "false"
    echo "  (install with: pip install bandit)"
fi

# T2: ruff-quality-gate.sh contains bandit invocation
if grep -q "bandit" "$SCRIPT_PATH"; then
    check "T2: script contains bandit" "true"
else
    check "T2: script contains bandit" "false"
fi

# T3: bandit runs with -f json flag
if grep -q "\-f json" "$SCRIPT_PATH"; then
    check "T3: bandit uses JSON format" "true"
else
    check "T3: bandit uses JSON format" "false"
fi

# T4: bandit runs with -q flag (quiet)
if grep -q "\-q" "$SCRIPT_PATH"; then
    check "T4: bandit uses quiet mode" "true"
else
    check "T4: bandit uses quiet mode" "false"
fi

# T5: Test with a safe Python file (should pass)
cat > "$TMPDIR/safe_file.py" << 'PYEOF'
def add(a, b):
    return a + b
PYEOF

INPUT_JSON='{"tool_input":{"file_path":"'"$TMPDIR/safe_file.py"'"}}'
echo "$INPUT_JSON" | bash "$SCRIPT_PATH" 2>/dev/null
EXIT_CODE=$?
check "T5: safe file passes (exit 0)" "$([ $EXIT_CODE -eq 0 ] && echo true || echo false)"

# T6: Test with non-Python file (should be skipped)
INPUT_JSON='{"tool_input":{"file_path":"'"$TMPDIR/readme.md"'"}}'
echo "$INPUT_JSON" | bash "$SCRIPT_PATH" 2>/dev/null
EXIT_CODE=$?
check "T6: non-Python file skipped" "$([ $EXIT_CODE -eq 0 ] && echo true || echo false)"

# T7: script checks for HIGH/MEDIUM severity
if grep -q "HIGH\|MEDIUM\|high\|medium" "$SCRIPT_PATH"; then
    check "T7: checks for HIGH/MEDIUM severity" "true"
else
    check "T7: checks for HIGH/MEDIUM severity" "false"
fi

# Cleanup
rm -rf "$TMPDIR"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ $FAIL -gt 0 ] && exit 1
exit 0
