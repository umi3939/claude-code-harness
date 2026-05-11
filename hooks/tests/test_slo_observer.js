/**
 * Tests for hooks/behavior-guard.js — G62 SLO observer additions.
 *
 * Covers:
 *   - PreToolUse short-lived memo write for matched MCP tools
 *   - PostToolUse elapsed-seconds computation, WARN, persistence
 *   - fail-open paths (missing memo, missing tool_use_id, write errors)
 *   - WARN suppression window (rule_id-scoped frequency cap)
 *   - stdin pass-through on stdout
 *
 * All tests run behavior-guard.js as a subprocess with isolated HOME so
 * they never touch real growth/ or hooks/.behavior-guard-state.json data.
 *
 * Run: node hooks/tests/test_slo_observer.js
 */

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

let passed = 0;
let failed = 0;
const failures = [];

function assert(condition, msg) {
  if (condition) {
    passed++;
    console.log(`  PASS: ${msg}`);
  } else {
    failed++;
    failures.push(msg);
    console.log(`  FAIL: ${msg}`);
  }
}

const GUARD = path.join(__dirname, '..', 'behavior-guard.js');

// --- Test harness: isolated fixture layout ---
//
// behavior-guard.js uses __dirname-based paths.  We override those via
// env vars (HOOKS_DIR_OVERRIDE, OBS_FILE_OVERRIDE, SLO_VIOLATIONS_FILE,
// BEHAVIOR_RULES_FILE_OVERRIDE) which behavior-guard.js honors when set.
// This isolates each test from real session state.

function makeFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'slo-observer-'));
  fs.mkdirSync(path.join(root, 'hooks'), { recursive: true });
  fs.mkdirSync(path.join(root, 'hooks', '.slo-pending'), { recursive: true });
  fs.mkdirSync(path.join(root, 'data'), { recursive: true });
  fs.mkdirSync(path.join(root, 'growth'), { recursive: true });
  fs.writeFileSync(path.join(root, 'data', 'observations.jsonl'), '');
  // Minimal behavior-rules.json: only the SLO rule, isolating these tests.
  const rulesPath = path.join(root, 'hooks', 'behavior-rules.json');
  const rules = {
    version: 2,
    rules: [
      {
        id: 'memory-search-slo-violation',
        description: 'memory_search SLO violation observer (G62)',
        domain: 'observability',
        blocking: false,
        trigger: {
          tool: 'mcp__memory-tools__memory_search',
          condition: 'mcp_tool_guard',
          guard_type: 'duration_threshold',
          threshold_seconds: 30,
          window_minutes: 10,
          max_warns: 3,
        },
        type: 'pattern',
        message: 'memory_search SLO violation',
        severity: 'warn',
      },
    ],
  };
  fs.writeFileSync(rulesPath, JSON.stringify(rules, null, 2));
  return root;
}

function runGuard(fixture, input, extraEnv) {
  const env = {
    ...process.env,
    HOOKS_DIR_OVERRIDE: path.join(fixture, 'hooks'),
    OBS_FILE_OVERRIDE: path.join(fixture, 'data', 'observations.jsonl'),
    SLO_VIOLATIONS_FILE: path.join(fixture, 'growth', 'slo_violations.jsonl'),
    BEHAVIOR_RULES_FILE_OVERRIDE: path.join(fixture, 'hooks', 'behavior-rules.json'),
    HOOK_PROFILE: 'strict',
    ...(extraEnv || {}),
  };
  const result = spawnSync('node', [GUARD], {
    input: JSON.stringify(input),
    env,
    encoding: 'utf8',
    timeout: 10000,
  });
  return result;
}

function pendingPath(fixture, toolUseId) {
  // Sanitize tool_use_id for filename safety (mirrors behavior-guard.js logic)
  const safe = String(toolUseId).replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 128);
  return path.join(fixture, 'hooks', '.slo-pending', `${safe}.json`);
}

function readJsonl(filePath) {
  if (!fs.existsSync(filePath)) return [];
  return fs
    .readFileSync(filePath, 'utf8')
    .split('\n')
    .filter((l) => l.trim() !== '')
    .map((l) => JSON.parse(l));
}

// =====================================================================
// Test 1: PreToolUse writes pending memo when tool matches duration rule
// =====================================================================
console.log('Test 1: pretool writes pending memo when tool matches');
{
  const fix = makeFixture();
  const toolUseId = 'toolu_test_1';
  const input = {
    hook_event_name: 'PreToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: toolUseId,
    tool_input: { query: 'foo' },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 1 exit code 0 (got ${r.status})`);
  const memo = pendingPath(fix, toolUseId);
  assert(fs.existsSync(memo), 'Test 1 pending memo file exists');
  if (fs.existsSync(memo)) {
    const body = JSON.parse(fs.readFileSync(memo, 'utf8'));
    assert(typeof body.start_ms === 'number' && body.start_ms > 0, 'Test 1 memo has numeric start_ms');
    assert(body.tool_name === 'mcp__memory-tools__memory_search', 'Test 1 memo records tool_name');
    assert(body.rule_id === 'memory-search-slo-violation', 'Test 1 memo records rule_id');
  }
}

// =====================================================================
// Test 2: PreToolUse skips memo for non-target tools
// =====================================================================
console.log('Test 2: pretool skips memo for non-target tools');
{
  const fix = makeFixture();
  const toolUseId = 'toolu_test_2';
  const input = {
    hook_event_name: 'PreToolUse',
    tool_name: 'mcp__memory-tools__stm_write',
    tool_use_id: toolUseId,
    tool_input: { content: 'noop', category: 'thought' },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 2 exit 0 (got ${r.status})`);
  const memo = pendingPath(fix, toolUseId);
  assert(!fs.existsSync(memo), 'Test 2 no memo created for non-target tool');
}

// =====================================================================
// Test 3: PreToolUse fallback synthetic key when tool_use_id missing
// =====================================================================
console.log('Test 3: pretool uses fallback key when tool_use_id missing');
{
  const fix = makeFixture();
  const input = {
    hook_event_name: 'PreToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    // tool_use_id intentionally absent
    tool_input: { query: 'foo' },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 3 exit 0 (got ${r.status})`);
  const dir = path.join(fix, 'hooks', '.slo-pending');
  const files = fs.readdirSync(dir);
  assert(files.length === 1, `Test 3 fallback memo created (got ${files.length} files)`);
  if (files.length === 1) {
    const body = JSON.parse(fs.readFileSync(path.join(dir, files[0]), 'utf8'));
    assert(typeof body.start_ms === 'number', 'Test 3 fallback memo has start_ms');
    assert(body.synthetic === true, 'Test 3 fallback memo flagged as synthetic');
  }
}

// =====================================================================
// Test 4: PostToolUse computes elapsed and triggers WARN over threshold
// =====================================================================
console.log('Test 4: posttool warns when elapsed exceeds threshold');
{
  const fix = makeFixture();
  const toolUseId = 'toolu_test_4';
  fs.writeFileSync(
    pendingPath(fix, toolUseId),
    JSON.stringify({
      start_ms: Date.now() - 60_000,
      tool_name: 'mcp__memory-tools__memory_search',
      rule_id: 'memory-search-slo-violation',
      synthetic: false,
    })
  );
  const input = {
    hook_event_name: 'PostToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: toolUseId,
    tool_input: { query: 'foo' },
    tool_response: { results: [] },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 4 exit 0 even on WARN (got ${r.status})`);
  assert(/SLO/.test(r.stderr), 'Test 4 stderr contains SLO WARN');
  assert(/memory_search/.test(r.stderr), 'Test 4 stderr mentions memory_search');
}

// =====================================================================
// Test 5: PostToolUse stays silent when elapsed under threshold
// =====================================================================
console.log('Test 5: posttool silent under threshold');
{
  const fix = makeFixture();
  const toolUseId = 'toolu_test_5';
  fs.writeFileSync(
    pendingPath(fix, toolUseId),
    JSON.stringify({
      start_ms: Date.now() - 1_000,
      tool_name: 'mcp__memory-tools__memory_search',
      rule_id: 'memory-search-slo-violation',
      synthetic: false,
    })
  );
  const input = {
    hook_event_name: 'PostToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: toolUseId,
    tool_input: { query: 'foo' },
    tool_response: { results: [] },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 5 exit 0 (got ${r.status})`);
  assert(!/SLO/.test(r.stderr), 'Test 5 stderr free of SLO WARN');
  const violationsPath = path.join(fix, 'growth', 'slo_violations.jsonl');
  const entries = readJsonl(violationsPath);
  assert(entries.length === 0, `Test 5 no violation entry written (got ${entries.length})`);
}

// =====================================================================
// Test 6: PostToolUse deletes pending memo after match
// =====================================================================
console.log('Test 6: posttool deletes pending memo after match');
{
  const fix = makeFixture();
  const toolUseId = 'toolu_test_6';
  const memoPath = pendingPath(fix, toolUseId);
  fs.writeFileSync(
    memoPath,
    JSON.stringify({
      start_ms: Date.now() - 5_000,
      tool_name: 'mcp__memory-tools__memory_search',
      rule_id: 'memory-search-slo-violation',
      synthetic: false,
    })
  );
  const input = {
    hook_event_name: 'PostToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: toolUseId,
    tool_input: { query: 'foo' },
    tool_response: { results: [] },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 6 exit 0 (got ${r.status})`);
  assert(!fs.existsSync(memoPath), 'Test 6 pending memo deleted after PostToolUse match');
}

// =====================================================================
// Test 7: PostToolUse fail-open when no matching memo found
// =====================================================================
console.log('Test 7: posttool fail-open when no memo found');
{
  const fix = makeFixture();
  const input = {
    hook_event_name: 'PostToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: 'toolu_no_memo_exists',
    tool_input: { query: 'foo' },
    tool_response: { results: [] },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 7 exit 0 (got ${r.status})`);
  assert(!/SLO/.test(r.stderr), 'Test 7 stderr free of SLO WARN');
  const violationsPath = path.join(fix, 'growth', 'slo_violations.jsonl');
  const entries = readJsonl(violationsPath);
  assert(entries.length === 0, 'Test 7 no violation written (fail-open)');
}

// =====================================================================
// Test 8: PostToolUse persists violation entry to slo_violations.jsonl
// =====================================================================
console.log('Test 8: posttool persists violation to slo_violations.jsonl');
{
  const fix = makeFixture();
  const toolUseId = 'toolu_test_8';
  fs.writeFileSync(
    pendingPath(fix, toolUseId),
    JSON.stringify({
      start_ms: Date.now() - 60_000,
      tool_name: 'mcp__memory-tools__memory_search',
      rule_id: 'memory-search-slo-violation',
      synthetic: false,
    })
  );
  const input = {
    hook_event_name: 'PostToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: toolUseId,
    tool_input: { query: 'foo' },
    tool_response: { results: [] },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 8 exit 0 (got ${r.status})`);
  const violationsPath = path.join(fix, 'growth', 'slo_violations.jsonl');
  const entries = readJsonl(violationsPath);
  assert(entries.length === 1, `Test 8 exactly 1 violation entry (got ${entries.length})`);
}

// =====================================================================
// Test 9: persisted entry has required fields only
// =====================================================================
console.log('Test 9: persisted entry has required fields only');
{
  const fix = makeFixture();
  const toolUseId = 'toolu_test_9';
  fs.writeFileSync(
    pendingPath(fix, toolUseId),
    JSON.stringify({
      start_ms: Date.now() - 45_000,
      tool_name: 'mcp__memory-tools__memory_search',
      rule_id: 'memory-search-slo-violation',
      synthetic: false,
    })
  );
  const input = {
    hook_event_name: 'PostToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: toolUseId,
    tool_input: { query: 'sensitive query body' },
    tool_response: { results: [{ content: 'sensitive body' }] },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 9 exit 0 (got ${r.status})`);
  const entries = readJsonl(path.join(fix, 'growth', 'slo_violations.jsonl'));
  assert(entries.length === 1, 'Test 9 violation entry written');
  if (entries.length === 1) {
    const e = entries[0];
    assert(typeof e.ts === 'string' && /T/.test(e.ts), 'Test 9 entry has ISO ts');
    assert(typeof e.session === 'string', 'Test 9 entry has session field');
    assert(e.tool_name === 'mcp__memory-tools__memory_search', 'Test 9 entry has tool_name');
    assert(e.tool_use_id === toolUseId, 'Test 9 entry has tool_use_id');
    assert(typeof e.elapsed_seconds === 'number' && e.elapsed_seconds > 30, 'Test 9 entry has numeric elapsed_seconds > 30');
    assert(e.threshold_seconds === 30, 'Test 9 entry has threshold_seconds');
    assert(e.rule_id === 'memory-search-slo-violation', 'Test 9 entry has rule_id');
    assert(typeof e.message === 'string' && e.message.length > 0, 'Test 9 entry has message');
  }
}

// =====================================================================
// Test 10: persisted entry omits tool_response body
// =====================================================================
console.log('Test 10: persisted entry omits tool_response body');
{
  const fix = makeFixture();
  const toolUseId = 'toolu_test_10';
  fs.writeFileSync(
    pendingPath(fix, toolUseId),
    JSON.stringify({
      start_ms: Date.now() - 45_000,
      tool_name: 'mcp__memory-tools__memory_search',
      rule_id: 'memory-search-slo-violation',
      synthetic: false,
    })
  );
  const sentinel = 'PRIVATE_BODY_FENCE_MARKER_XYZZY';
  const input = {
    hook_event_name: 'PostToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: toolUseId,
    tool_input: { query: sentinel },
    tool_response: { results: [{ content: sentinel }] },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 10 exit 0 (got ${r.status})`);
  const violationsPath = path.join(fix, 'growth', 'slo_violations.jsonl');
  const raw = fs.existsSync(violationsPath) ? fs.readFileSync(violationsPath, 'utf8') : '';
  assert(!raw.includes(sentinel), 'Test 10 violation entry does NOT contain tool_response body');
}

// =====================================================================
// Test 11: WARN suppression window respects max_warns within window_minutes
// =====================================================================
console.log('Test 11: warn suppression window');
{
  const fix = makeFixture();
  let warnCount = 0;
  for (let i = 0; i < 5; i++) {
    const toolUseId = `toolu_test_11_${i}`;
    fs.writeFileSync(
      pendingPath(fix, toolUseId),
      JSON.stringify({
        start_ms: Date.now() - 60_000,
        tool_name: 'mcp__memory-tools__memory_search',
        rule_id: 'memory-search-slo-violation',
        synthetic: false,
      })
    );
    const input = {
      hook_event_name: 'PostToolUse',
      tool_name: 'mcp__memory-tools__memory_search',
      tool_use_id: toolUseId,
      tool_input: { query: 'foo' },
      tool_response: { results: [] },
    };
    const r = runGuard(fix, input);
    if (/SLO/.test(r.stderr)) warnCount++;
  }
  assert(warnCount === 3, `Test 11 exactly 3 stderr WARNs out of 5 (got ${warnCount})`);
  const entries = readJsonl(path.join(fix, 'growth', 'slo_violations.jsonl'));
  assert(entries.length === 5, `Test 11 all 5 entries persisted (got ${entries.length})`);
}

// =====================================================================
// Test 12: PostToolUse fails open when slo_violations.jsonl write throws
// =====================================================================
console.log('Test 12: posttool fail-open when violations write fails');
{
  const fix = makeFixture();
  const toolUseId = 'toolu_test_12';
  fs.writeFileSync(
    pendingPath(fix, toolUseId),
    JSON.stringify({
      start_ms: Date.now() - 60_000,
      tool_name: 'mcp__memory-tools__memory_search',
      rule_id: 'memory-search-slo-violation',
      synthetic: false,
    })
  );
  // Replace growth dir with a regular file so any write attempt throws.
  const growthDir = path.join(fix, 'growth');
  fs.rmdirSync(growthDir);
  fs.writeFileSync(growthDir, 'not a dir');
  const input = {
    hook_event_name: 'PostToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: toolUseId,
    tool_input: { query: 'foo' },
    tool_response: { results: [] },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 12 fail-open exit 0 even on write error (got ${r.status})`);
}

// =====================================================================
// Test 13: PostToolUse passes tool_response through stdout unchanged
// =====================================================================
console.log('Test 13: stdout empty on PostToolUse (observation-logger owns stdout)');
{
  const fix = makeFixture();
  const toolUseId = 'toolu_test_13';
  fs.writeFileSync(
    pendingPath(fix, toolUseId),
    JSON.stringify({
      start_ms: Date.now() - 60_000,
      tool_name: 'mcp__memory-tools__memory_search',
      rule_id: 'memory-search-slo-violation',
      synthetic: false,
    })
  );
  const input = {
    hook_event_name: 'PostToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: toolUseId,
    tool_input: { query: 'foo' },
    tool_response: { results: [] },
  };
  const r = runGuard(fix, input);
  assert(r.status === 0, `Test 13 exit 0 (got ${r.status})`);
  // Per analyzer §改訂5: behavior-guard.js MUST NOT write to stdout on PostToolUse
  // (observation-logger.js owns that channel).  WARN is stderr only.
  assert(r.stdout === '' || r.stdout.trim() === '', `Test 13 stdout is empty on PostToolUse (got ${JSON.stringify(r.stdout)})`);
}

// =====================================================================
// Test 14: PostToolUse memory_search_without_session_start rule does NOT fire
// (function-entry skip — G62 follow-up).  Verifies PreToolUse-only conditions
// are not evaluated on PostToolUse even when their tool matcher matches.
// =====================================================================
console.log('Test 14: posttool skips memory_search_without_session_start (PreToolUse-only condition)');
{
  const fix = makeFixture();
  // Replace fixture rules: include the PreToolUse-only blocking rule alongside SLO.
  // observations.jsonl is empty (no session_start observed) — under PreToolUse this
  // would block; under PostToolUse with the function-entry skip, the rule is skipped.
  const rules = {
    version: 2,
    rules: [
      {
        id: 'memory-search-without-session-start',
        domain: 'session',
        blocking: true,
        trigger: {
          tool: 'mcp__memory-tools__memory_search',
          condition: 'memory_search_without_session_start',
        },
        type: 'pattern',
        message: 'session_start required before memory_search',
        severity: 'warn',
      },
      {
        id: 'memory-search-slo-violation',
        domain: 'observability',
        blocking: false,
        trigger: {
          tool: 'mcp__memory-tools__memory_search',
          condition: 'mcp_tool_guard',
          guard_type: 'duration_threshold',
          threshold_seconds: 30,
          window_minutes: 10,
          max_warns: 3,
        },
        type: 'pattern',
        message: 'SLO violation',
        severity: 'warn',
      },
    ],
  };
  fs.writeFileSync(path.join(fix, 'hooks', 'behavior-rules.json'), JSON.stringify(rules));
  const input = {
    hook_event_name: 'PostToolUse',
    tool_name: 'mcp__memory-tools__memory_search',
    tool_use_id: 'toolu_test_14',
    tool_input: { query: 'foo' },
    tool_response: { results: [] },
  };
  const r = runGuard(fix, input);
  // Even though session_start is missing (which would block on PreToolUse),
  // PostToolUse must pass through cleanly because the function-entry skip
  // gates non-duration_threshold conditions.
  assert(r.status === 0, `Test 14 exit 0 — PostToolUse skips PreToolUse-only rule (got ${r.status})`);
  assert(!/session_start required/.test(r.stderr), 'Test 14 stderr free of memory_search_without_session_start BLOCKED message');
  assert(!/BLOCKED/.test(r.stderr), 'Test 14 stderr has no BLOCKED message');
}

// --- Summary ---
console.log('');
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  console.log('');
  console.log('Failures:');
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
process.exit(0);
