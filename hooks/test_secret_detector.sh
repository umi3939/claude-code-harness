#!/bin/bash
# Test suite for secret-detector.js
# Usage: bash test_secret_detector.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/secret-detector.js"
PASS=0
FAIL=0

run_test() {
  local desc="$1"
  local input="$2"
  local expect_exit="$3"  # 0=pass, 2=blocked

  local result
  result=$(echo "$input" | node "$HOOK" 2>&1)
  local actual_exit=$?

  if [ "$actual_exit" -eq "$expect_exit" ]; then
    PASS=$((PASS + 1))
    echo "  PASS: $desc"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL: $desc (expected exit=$expect_exit, got exit=$actual_exit)"
    echo "        output: $result"
  fi
}

echo "=== Secret Detector Tests ==="
echo ""

# ---- OpenAI key patterns ----
echo "--- OpenAI API Keys ---"

run_test "Detect sk- key in Edit content" \
  '{"tool_name":"Edit","tool_input":{"file_path":"config.js","old_string":"TODO","new_string":"sk-EXAMPLEFIXTUREdoNotUseABCdef0123456789ABCDEF0123"}}' \
  2

run_test "Detect sk- key in Write content" \
  '{"tool_name":"Write","tool_input":{"file_path":"config.js","content":"const key = \"sk-EXAMPLEFIXTUREdoNotUseABCdef0123456789ABCDEF0123\";"}}' \
  2

run_test "Detect sk- key in Bash command" \
  '{"tool_name":"Bash","tool_input":{"command":"curl -H \"Authorization: Bearer sk-EXAMPLEFIXTUREdoNotUseABCdef0123456789ABCDEF0123\" https://api.openai.com"}}' \
  2

# ---- GitHub token patterns ----
echo "--- GitHub Tokens ---"

run_test "Detect ghp_ token" \
  '{"tool_name":"Write","tool_input":{"file_path":".env","content":"GITHUB_TOKEN=ghp_EXAMPLEFIXTUREdoNotUse1234567890ABCDEFghij"}}' \
  2

run_test "Detect gho_ token" \
  '{"tool_name":"Edit","tool_input":{"file_path":"config.yaml","old_string":"token: xxx","new_string":"token: gho_EXAMPLEFIXTUREdoNotUse1234567890ABCDEFghij"}}' \
  2

run_test "Detect ghs_ token" \
  '{"tool_name":"Bash","tool_input":{"command":"echo ghs_EXAMPLEFIXTUREdoNotUse1234567890ABCDEFghij"}}' \
  2

# ---- AWS keys ----
echo "--- AWS Keys ---"

run_test "Detect AKIA key" \
  '{"tool_name":"Write","tool_input":{"file_path":"aws.conf","content":"aws_access_key_id = AKIAIOSFODNN7EXAMPLE"}}' \
  2

# ---- Slack tokens ----
echo "--- Slack Tokens ---"

run_test "Detect xoxb- token" \
  '{"tool_name":"Edit","tool_input":{"file_path":"slack.js","old_string":"TODO","new_string":"xoxb-0-0-EXAMPLEFIXTUREdoNotUse"}}' \
  2

run_test "Detect xoxp- token" \
  '{"tool_name":"Write","tool_input":{"file_path":"slack.conf","content":"token=xoxp-0-0-EXAMPLEFIXTUREdoNotUse"}}' \
  2

# ---- Generic password/secret/token patterns ----
echo "--- Generic Patterns ---"

run_test "Detect password assignment" \
  '{"tool_name":"Write","tool_input":{"file_path":"config.py","content":"password = \"EXAMPLE_FIXTURE_DO_NOT_USE_pass\""}}' \
  2

run_test "Detect PASSWORD assignment" \
  '{"tool_name":"Edit","tool_input":{"file_path":"config.py","old_string":"TODO","new_string":"PASSWORD=\"EXAMPLE_FIXTURE_pwd\""}}' \
  2

run_test "Detect secret assignment" \
  '{"tool_name":"Write","tool_input":{"file_path":"app.py","content":"secret = \"EXAMPLE_FIXTURE_secret\""}}' \
  2

run_test "Detect api_key assignment" \
  '{"tool_name":"Write","tool_input":{"file_path":"app.js","content":"api_key = \"EXAMPLE_FIXTURE_apikey\""}}' \
  2

run_test "Detect API_KEY assignment" \
  '{"tool_name":"Edit","tool_input":{"file_path":"config.env","old_string":"#key","new_string":"API_KEY=\"EXAMPLE_FIXTURE_apikey\""}}' \
  2

run_test "Detect token assignment" \
  '{"tool_name":"Write","tool_input":{"file_path":"auth.js","content":"token = \"EXAMPLE_FIXTURE_DO_NOT_USE_jwt_payload_xxxxxxxxxx\""}}' \
  2

# ---- Non-detection cases (should pass through) ----
echo "--- Non-detection (should pass) ---"

run_test "Normal code edit passes" \
  '{"tool_name":"Edit","tool_input":{"file_path":"app.js","old_string":"const x = 1","new_string":"const x = 2"}}' \
  0

run_test "Normal write passes" \
  '{"tool_name":"Write","tool_input":{"file_path":"hello.py","content":"print(\"hello world\")"}}' \
  0

run_test "Normal bash passes" \
  '{"tool_name":"Bash","tool_input":{"command":"ls -la"}}' \
  0

run_test "Non-target tool passes (Read)" \
  '{"tool_name":"Read","tool_input":{"file_path":"secret.txt"}}' \
  0

run_test "Non-target tool passes (Glob)" \
  '{"tool_name":"Glob","tool_input":{"pattern":"**/*.key"}}' \
  0

run_test "Comment about password passes" \
  '{"tool_name":"Write","tool_input":{"file_path":"doc.md","content":"# Password Management\nUse a password manager to store credentials securely."}}' \
  0

run_test "Variable named password_hash passes (no assignment with literal)" \
  '{"tool_name":"Edit","tool_input":{"file_path":"auth.py","old_string":"hash = bcrypt(pw)","new_string":"password_hash = bcrypt(pw)"}}' \
  0

run_test "Empty tool_input passes" \
  '{"tool_name":"Edit","tool_input":{}}' \
  0

run_test "sk- in a comment/doc context (short, not real key)" \
  '{"tool_name":"Write","tool_input":{"file_path":"doc.md","content":"The prefix sk- is used by OpenAI."}}' \
  0

# ---- Edge cases ----
echo "--- Edge Cases ---"

run_test "Invalid JSON passes through" \
  'not json at all' \
  0

run_test "Empty stdin passes through" \
  '' \
  0

run_test "Detect secret in Bash env export" \
  '{"tool_name":"Bash","tool_input":{"command":"export OPENAI_API_KEY=sk-EXAMPLEFIXTUREdoNotUseABCdef0123456789ABCDEF0123"}}' \
  2

# ---- Self-test path exemption ----
echo "--- Self-test path skip ---"

run_test "Edit on test_secret_detector.sh is exempt (sk- in new_string passes)" \
  '{"tool_name":"Edit","tool_input":{"file_path":"hooks/test_secret_detector.sh","old_string":"x","new_string":"sk-EXAMPLEFIXTUREdoNotUse123456789012345"}}' \
  0

run_test "Edit on other path still detects sk-" \
  '{"tool_name":"Edit","tool_input":{"file_path":"app.js","old_string":"x","new_string":"sk-EXAMPLEFIXTUREdoNotUse123456789012345"}}' \
  2

# ---- Summary ----
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
