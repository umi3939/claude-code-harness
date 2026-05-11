/**
 * Tests for hooks/auto-test-runner.js
 *
 * Verifies behavior-guard trigger detection, result parsing, and end-to-end
 * hook invocation. The main goal is to confirm G57: editing behavior-guard
 * files triggers test_behavior_guard.sh via auto-test-runner.
 *
 * Run: node hooks/tests/test_auto_test_runner.js
 */

const path = require('path');
const { spawnSync } = require('child_process');

const RUNNER = path.join(__dirname, '..', 'auto-test-runner.js');
const mod = require(RUNNER);

let passed = 0;
let failed = 0;

function assert(cond, msg) {
  if (cond) {
    passed++;
    console.log(`  PASS: ${msg}`);
  } else {
    failed++;
    console.log(`  FAIL: ${msg}`);
  }
}

function runHook(toolName, filePath) {
  const input = JSON.stringify({
    tool_name: toolName,
    tool_input: { file_path: filePath },
  });
  return spawnSync('node', [RUNNER], {
    input,
    encoding: 'utf8',
    env: Object.assign({}, process.env, {
      // Point the runner at a stub script via env would be ideal, but we
      // cannot intercept the hard-coded path from outside. Instead, end-to-end
      // invocation tests assert only on exit code + stdout passthrough.
      // Detailed behavior is covered by unit tests below.
      AUTO_TEST_RUNNER_TEST: '1',
    }),
    timeout: 90000,
  });
}

// --- Unit tests: matchesGuardFile ---
console.log('Test 1: matchesGuardFile detects behavior-guard trigger files');
assert(
  mod.matchesGuardFile('C:/Users/user/.claude/hooks/behavior-guard.js'),
  'behavior-guard.js matches'
);
assert(
  mod.matchesGuardFile('C:/Users/user/.claude/hooks/behavior-rules.json'),
  'behavior-rules.json matches'
);
assert(
  mod.matchesGuardFile('C:/Users/user/.claude/hooks/skill_executor.py'),
  'skill_executor.py matches'
);
assert(
  mod.matchesGuardFile('C:/Users/user/.claude/hooks/session-readiness-gate.js'),
  'session-readiness-gate.js matches'
);

console.log('Test 2: matchesGuardFile rejects unrelated files');
assert(
  !mod.matchesGuardFile('C:/Users/user/.claude/tools/memory_tools.py'),
  'unrelated .py rejected'
);
assert(
  !mod.matchesGuardFile('C:/Users/user/.claude/hooks/session-init.js'),
  'unrelated .js rejected'
);
assert(!mod.matchesGuardFile(''), 'empty string rejected');
assert(!mod.matchesGuardFile(null), 'null rejected');
assert(!mod.matchesGuardFile(undefined), 'undefined rejected');

// --- Unit tests: parseGuardResults ---
console.log('Test 3: parseGuardResults extracts pass/fail counts');
const r1 = mod.parseGuardResults('\nResults: 42 passed, 0 failed, 0 skipped\n');
assert(r1 && r1.passed === 42 && r1.failed === 0, 'all-pass summary parsed');

const r2 = mod.parseGuardResults('Results: 10 passed, 3 failed, 0 skipped');
assert(r2 && r2.passed === 10 && r2.failed === 3, 'mixed summary parsed');

const r3 = mod.parseGuardResults('no results line here');
assert(r3 === null, 'missing results returns null');

const r4 = mod.parseGuardResults('');
assert(r4 === null, 'empty string returns null');

const r5 = mod.parseGuardResults(null);
assert(r5 === null, 'null returns null');

// --- Unit tests: processInput dispatches to guard runner ---
console.log('Test 4: behavior_guard_js change triggers guard tests');
let guardCallCount = 0;
const mockDeps = {
  runGuardTests: () => { guardCallCount++; },
};

guardCallCount = 0;
mod.processInput(
  JSON.stringify({
    tool_name: 'Edit',
    tool_input: { file_path: 'C:/Users/user/.claude/hooks/behavior-guard.js' },
  }),
  mockDeps
);
assert(guardCallCount === 1, 'Edit of behavior-guard.js invoked runGuardTests');

console.log('Test 5: behavior_rules_json change triggers guard tests');
guardCallCount = 0;
mod.processInput(
  JSON.stringify({
    tool_name: 'Write',
    tool_input: { file_path: 'C:/Users/user/.claude/hooks/behavior-rules.json' },
  }),
  mockDeps
);
assert(guardCallCount === 1, 'Write of behavior-rules.json invoked runGuardTests');

console.log('Test 6: unrelated file change skips guard tests');
guardCallCount = 0;
mod.processInput(
  JSON.stringify({
    tool_name: 'Edit',
    tool_input: { file_path: 'C:/Users/user/.claude/hooks/session-init.js' },
  }),
  mockDeps
);
assert(guardCallCount === 0, 'unrelated file did not invoke runGuardTests');

console.log('Test 7: non-Edit/Write tool skips guard tests');
guardCallCount = 0;
mod.processInput(
  JSON.stringify({
    tool_name: 'Read',
    tool_input: { file_path: 'C:/Users/user/.claude/hooks/behavior-guard.js' },
  }),
  mockDeps
);
assert(guardCallCount === 0, 'Read tool did not invoke runGuardTests');

console.log('Test 8: session_readiness_gate change triggers guard tests');
guardCallCount = 0;
mod.processInput(
  JSON.stringify({
    tool_name: 'Edit',
    tool_input: { file_path: 'C:/Users/user/.claude/hooks/session-readiness-gate.js' },
  }),
  mockDeps
);
assert(guardCallCount === 1, 'Edit of session-readiness-gate.js invoked runGuardTests');

console.log('Test 9: skill_executor_py change triggers guard tests');
guardCallCount = 0;
mod.processInput(
  JSON.stringify({
    tool_name: 'Edit',
    tool_input: { file_path: 'C:/Users/user/.claude/hooks/skill_executor.py' },
  }),
  mockDeps
);
assert(guardCallCount === 1, 'Edit of skill_executor.py invoked runGuardTests');

// --- Helpers for runGuardTests / reportPreviousGuardResult unit tests ---
function captureStderr(fn) {
  const origErr = process.stderr.write.bind(process.stderr);
  let captured = '';
  process.stderr.write = (chunk) => {
    captured += chunk;
    return true;
  };
  try {
    fn();
  } finally {
    process.stderr.write = origErr;
  }
  return captured;
}

function makeFsMock({ files = {} } = {}) {
  const store = files;
  const mock = {
    existsSync: (p) => Object.prototype.hasOwnProperty.call(store, p),
    readFileSync: (p) => {
      if (!Object.prototype.hasOwnProperty.call(store, p)) {
        const e = new Error('ENOENT');
        e.code = 'ENOENT';
        throw e;
      }
      return store[p];
    },
    writeFileSync: (p, data) => { store[p] = String(data); },
    unlinkSync: (p) => { delete store[p]; },
    _files: store,
  };
  return mock;
}

// --- Unit tests: runGuardTests (fire-and-forget spawn) ---
console.log('Test 10: runGuardTests launches spawn and returns immediately');
{
  let spawnCalls = 0;
  let spawnArgs = null;
  let unrefCalled = false;
  const fakeSpawn = (cmd, args, opts) => {
    spawnCalls++;
    spawnArgs = { cmd, args, opts };
    return { unref: () => { unrefCalled = true; } };
  };
  const fsMock = makeFsMock();

  const t0 = Date.now();
  const captured = captureStderr(() => {
    mod.runGuardTests({
      spawnFn: fakeSpawn,
      fs: fsMock,
      lockFile: '/fake/.guard-test-running.lock',
      wrapper: '/fake/run_guard_tests_async.sh',
    });
  });
  const elapsed = Date.now() - t0;

  assert(spawnCalls === 1, 'spawn was called exactly once');
  assert(spawnArgs && spawnArgs.cmd === 'bash', 'spawned bash interpreter');
  assert(
    spawnArgs && Array.isArray(spawnArgs.args) && spawnArgs.args[0] === '/fake/run_guard_tests_async.sh',
    'spawned wrapper script path'
  );
  assert(
    spawnArgs && spawnArgs.opts && spawnArgs.opts.detached === true,
    'spawn options detached=true'
  );
  assert(
    spawnArgs && spawnArgs.opts && spawnArgs.opts.stdio === 'ignore',
    'spawn options stdio=ignore'
  );
  assert(unrefCalled, 'child.unref() was called');
  assert(elapsed < 1000, `runGuardTests returned in <1000ms (got ${elapsed}ms)`);
  assert(
    captured.includes('[AutoTest:BehaviorGuard] launched'),
    'stderr reports launch'
  );
  assert(
    fsMock._files['/fake/.guard-test-running.lock'] !== undefined,
    'lock file was created before spawn'
  );
}

console.log('Test 11: runGuardTests skips when lock file already exists');
{
  let spawnCalls = 0;
  const fakeSpawn = () => { spawnCalls++; return { unref: () => {} }; };
  const fsMock = makeFsMock({
    files: { '/fake/.guard-test-running.lock': '1234567890' },
  });
  const captured = captureStderr(() => {
    mod.runGuardTests({
      spawnFn: fakeSpawn,
      fs: fsMock,
      lockFile: '/fake/.guard-test-running.lock',
      wrapper: '/fake/run_guard_tests_async.sh',
    });
  });
  assert(spawnCalls === 0, 'spawn was NOT called when lock exists');
  assert(
    captured.includes('[AutoTest:BehaviorGuard] run already in progress'),
    'stderr reports skip'
  );
}

console.log('Test 12: runGuardTests cleans up lock when spawn throws');
{
  const fsMock = makeFsMock({ files: {} });
  let unlinkCalled = false;
  const origUnlink = fsMock.unlinkSync;
  fsMock.unlinkSync = (p) => { unlinkCalled = true; origUnlink(p); };
  const throwingSpawn = () => { throw new Error('ENOENT bash not found'); };
  const captured = captureStderr(() => {
    mod.runGuardTests({
      spawnFn: throwingSpawn,
      fs: fsMock,
      lockFile: '/fake/.guard-test-running.lock',
      wrapper: '/fake/run_guard_tests_async.sh',
    });
  });
  assert(unlinkCalled, 'lock file was unlinked after spawn failure');
  assert(
    captured.includes('[AutoTest:BehaviorGuard] launch failed'),
    'stderr reports launch failure'
  );
}

// --- Unit tests: reportPreviousGuardResult ---
console.log('Test 13: reportPreviousGuardResult reports fresh result');
{
  const now = 2000000;
  const resultPath = '/fake/.last-guard-test-result.json';
  const resultJson = JSON.stringify({
    timestamp: now - 60 * 1000, // 1 minute old — fresh
    exit_code: 0,
    summary: 'Results: 166 passed, 0 failed, 0 skipped',
    duration_sec: 145,
  });
  const fsMock = makeFsMock({ files: { [resultPath]: resultJson } });
  const captured = captureStderr(() => {
    mod.reportPreviousGuardResult({
      fs: fsMock,
      now: () => now,
      resultFile: resultPath,
      staleMs: 60 * 60 * 1000,
    });
  });
  assert(
    captured.includes('previous run OK') && captured.includes('166 passed'),
    'stderr contains fresh summary'
  );
  assert(captured.includes('(145s)'), 'stderr contains duration');
}

console.log('Test 14: reportPreviousGuardResult ignores stale result');
{
  const now = 10000000;
  const resultPath = '/fake/.last-guard-test-result.json';
  const resultJson = JSON.stringify({
    timestamp: now - 2 * 60 * 60 * 1000, // 2 hours old — stale
    exit_code: 0,
    summary: 'Results: 100 passed, 0 failed',
    duration_sec: 120,
  });
  const fsMock = makeFsMock({ files: { [resultPath]: resultJson } });
  const captured = captureStderr(() => {
    mod.reportPreviousGuardResult({
      fs: fsMock,
      now: () => now,
      resultFile: resultPath,
      staleMs: 60 * 60 * 1000,
    });
  });
  assert(captured === '', 'stale result produces no stderr output');
}

console.log('Test 15: reportPreviousGuardResult handles missing file silently');
{
  const fsMock = makeFsMock({ files: {} });
  const captured = captureStderr(() => {
    mod.reportPreviousGuardResult({
      fs: fsMock,
      now: () => 1000,
      resultFile: '/fake/nonexistent.json',
      staleMs: 60 * 60 * 1000,
    });
  });
  assert(captured === '', 'missing file produces no stderr output');
}

console.log('Test 16: reportPreviousGuardResult handles malformed JSON');
{
  const resultPath = '/fake/.last-guard-test-result.json';
  const fsMock = makeFsMock({ files: { [resultPath]: 'not json at all {{{' } });
  const captured = captureStderr(() => {
    mod.reportPreviousGuardResult({
      fs: fsMock,
      now: () => 1000,
      resultFile: resultPath,
      staleMs: 60 * 60 * 1000,
    });
  });
  assert(
    captured.includes('previous result unreadable'),
    'malformed JSON reports unreadable marker'
  );
}

console.log('Test 17: reportPreviousGuardResult reports failed exit code');
{
  const now = 5000000;
  const resultPath = '/fake/.last-guard-test-result.json';
  const resultJson = JSON.stringify({
    timestamp: now - 10 * 1000,
    exit_code: 1,
    summary: 'Results: 164 passed, 2 failed, 0 skipped',
    duration_sec: 147,
  });
  const fsMock = makeFsMock({ files: { [resultPath]: resultJson } });
  const captured = captureStderr(() => {
    mod.reportPreviousGuardResult({
      fs: fsMock,
      now: () => now,
      resultFile: resultPath,
      staleMs: 60 * 60 * 1000,
    });
  });
  assert(
    captured.includes('previous run FAIL(exit=1)') && captured.includes('2 failed'),
    'stderr contains failure marker and summary'
  );
}

console.log('Test 18: processInput invokes reportPreviousGuardResult on every call');
{
  let reportCalls = 0;
  const mockDeps = {
    runGuardTests: () => {},
    reportPreviousGuardResult: () => { reportCalls++; },
  };
  mod.processInput(
    JSON.stringify({
      tool_name: 'Edit',
      tool_input: { file_path: '/tmp/unrelated.txt' },
    }),
    mockDeps
  );
  assert(reportCalls === 1, 'reportPreviousGuardResult called even for unrelated file');

  reportCalls = 0;
  mod.processInput(
    JSON.stringify({
      tool_name: 'Edit',
      tool_input: { file_path: 'C:/Users/user/.claude/hooks/behavior-guard.js' },
    }),
    mockDeps
  );
  assert(reportCalls === 1, 'reportPreviousGuardResult called for guard trigger edit');
}

// --- End-to-end test: exit code is always 0 ---
console.log('Test 19: exit code always 0 for unrelated file (fast path)');
{
  const r = runHook(
    'Edit',
    'C:/Users/user/.claude/hooks/tests/__nonexistent_no_tests_here__.js'
  );
  assert(r.status === 0, `exit code 0 (got ${r.status})`);
}

console.log('Test 20: exit code 0 for non-Edit/Write tool');
{
  const r = runHook('Read', 'C:/Users/user/.claude/hooks/behavior-guard.js');
  assert(r.status === 0, `exit code 0 (got ${r.status})`);
}

console.log('Test 21: stdin is passed through to stdout');
{
  const r = runHook('Read', '/tmp/foo.txt');
  const out = (r.stdout || '').trim();
  assert(out.includes('"tool_name":"Read"'), 'stdout contains original input');
}

// --- Summary ---
console.log('');
console.log(`Results: ${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
