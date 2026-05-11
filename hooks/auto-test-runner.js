#!/usr/bin/env node
/**
 * PostToolUse Hook: Auto-run tests after .py file changes.
 *
 * When a .py file in tools/ is modified, finds and runs the
 * corresponding test file. Reports results via stderr.
 *
 * When a behavior-guard-related file is modified
 * (behavior-guard.js / behavior-rules.json / skill_executor.py /
 * session-readiness-gate.js), runs test_behavior_guard.sh and
 * reports results via stderr.
 */

const fs = require('fs');
const path = require('path');
const { execSync, execFileSync, spawn } = require('child_process');

const MAX_STDIN = 1024 * 1024;

// Files that trigger behavior-guard smoke tests when modified.
const GUARD_TRIGGER_FILES = [
  'behavior-guard.js',
  'behavior-rules.json',
  'skill_executor.py',
  'session-readiness-gate.js',
];

const HOOKS_DIR = __dirname;
const GUARD_TEST_SCRIPT = path.join(HOOKS_DIR, 'test_behavior_guard.sh');
const GUARD_ASYNC_WRAPPER = path.join(HOOKS_DIR, 'run_guard_tests_async.sh');
const GUARD_RESULT_FILE = path.join(HOOKS_DIR, '.last-guard-test-result.json');
const GUARD_LOCK_FILE = path.join(HOOKS_DIR, '.guard-test-running.lock');

// How long a previous result stays relevant for reporting on next invocation.
const GUARD_RESULT_STALE_MS = 60 * 60 * 1000; // 1 hour

/**
 * Returns true if the given file path matches a behavior-guard trigger file.
 */
function matchesGuardFile(filePath) {
  if (!filePath || typeof filePath !== 'string') return false;
  const base = path.basename(filePath);
  return GUARD_TRIGGER_FILES.includes(base);
}

/**
 * Parse "Results: N passed, M failed, K skipped" from the guard test output.
 * Returns { passed, failed } or null if not parseable.
 */
function parseGuardResults(output) {
  if (!output || typeof output !== 'string') return null;
  const match = output.match(/Results:\s*(\d+)\s+passed,\s*(\d+)\s+failed/);
  if (!match) return null;
  return { passed: parseInt(match[1], 10), failed: parseInt(match[2], 10) };
}

/**
 * Report the previous guard test result if one exists and is still fresh.
 * Reads GUARD_RESULT_FILE (written by run_guard_tests_async.sh) and emits a
 * summary to stderr. Stale (>GUARD_RESULT_STALE_MS) or missing results are
 * silently skipped. Never throws. fs/nowFn are optional deps for testing.
 */
function reportPreviousGuardResult(deps) {
  const fsMod = (deps && deps.fs) || fs;
  const nowFn = (deps && deps.now) || Date.now;
  const resultFile = (deps && deps.resultFile) || GUARD_RESULT_FILE;
  const staleMs = (deps && deps.staleMs != null) ? deps.staleMs : GUARD_RESULT_STALE_MS;

  try {
    if (!fsMod.existsSync(resultFile)) return;
    const raw = fsMod.readFileSync(resultFile, 'utf8');
    const data = JSON.parse(raw);
    const ts = typeof data.timestamp === 'number' ? data.timestamp : 0;
    if (nowFn() - ts > staleMs) {
      // Stale — skip silently to avoid noise from old runs.
      return;
    }
    const summary = typeof data.summary === 'string' ? data.summary : '(no summary)';
    const exitCode = typeof data.exit_code === 'number' ? data.exit_code : -1;
    const duration = typeof data.duration_sec === 'number' ? data.duration_sec : 0;
    const tag = exitCode === 0 ? 'OK' : `FAIL(exit=${exitCode})`;
    console.error(
      `[AutoTest:BehaviorGuard] previous run ${tag}: ${summary} (${duration}s)`
    );
  } catch (e) {
    // Fail-open: malformed result file must never break the hook.
    const msg = (e && e.message) ? String(e.message).split('\n')[0] : 'unknown';
    console.error(`[AutoTest:BehaviorGuard] previous result unreadable: ${msg}`);
  }
}

/**
 * Launch behavior-guard smoke tests in fire-and-forget mode. Returns
 * immediately (<100ms) without waiting for the ~145s test suite to finish.
 *
 * Side effects:
 *   - Creates .guard-test-running.lock (cleaned up by the wrapper on exit).
 *   - Spawns run_guard_tests_async.sh detached. The wrapper writes
 *     .last-guard-test-result.json when done.
 *   - Emits a [AutoTest:BehaviorGuard] stderr line describing what happened.
 *
 * If a run is already in progress (lock file present), skips launching a new
 * one. Never throws. deps may inject { spawnFn, fs, lockFile, wrapper }.
 */
function runGuardTests(deps) {
  const spawnFn = (deps && deps.spawnFn) || spawn;
  const fsMod = (deps && deps.fs) || fs;
  const lockFile = (deps && deps.lockFile) || GUARD_LOCK_FILE;
  const wrapper = (deps && deps.wrapper) || GUARD_ASYNC_WRAPPER;

  try {
    if (fsMod.existsSync(lockFile)) {
      console.error('[AutoTest:BehaviorGuard] run already in progress, skipping');
      return;
    }
  } catch {
    // existsSync should not throw, but be defensive — fall through and try anyway.
  }

  // Create lock before spawn. The wrapper removes it via trap on exit.
  try {
    fsMod.writeFileSync(lockFile, String(Date.now()), { encoding: 'utf8' });
  } catch (e) {
    console.error(
      `[AutoTest:BehaviorGuard] lock create failed: ${(e && e.message) || 'unknown'}`
    );
    // Continue anyway — the wrapper will still run.
  }

  try {
    const child = spawnFn('bash', [wrapper], {
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    });
    // Detach so the parent hook can exit without waiting.
    if (child && typeof child.unref === 'function') {
      child.unref();
    }
    console.error('[AutoTest:BehaviorGuard] launched (async, ~145s)');
  } catch (e) {
    // Spawn failed — clean up the lock we just created so a retry can proceed.
    try { fsMod.unlinkSync(lockFile); } catch {}
    const msg = (e && e.message) ? String(e.message).split('\n')[0] : 'unknown';
    console.error(`[AutoTest:BehaviorGuard] launch failed: ${msg}`);
  }
}

/**
 * Main processing logic. Extracted for testing.
 * deps may override { runGuardTests, reportPreviousGuardResult } during tests.
 */
function processInput(raw, deps) {
  const runGuard = (deps && deps.runGuardTests) || runGuardTests;
  const reportPrev = (deps && deps.reportPreviousGuardResult) || reportPreviousGuardResult;

  // Always report the previous guard run (if any) before doing anything else.
  // This gives us the async result loop: run N triggers run N+1's report.
  reportPrev();

  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    return; // pass-through on parse error
  }

  const toolName = input.tool_name || '';
  const toolInput = input.tool_input || {};

  if (toolName !== 'Edit' && toolName !== 'Write') {
    return;
  }

  const filePath = toolInput.file_path || '';

  // Branch 1: behavior-guard trigger files — launch async smoke tests.
  if (matchesGuardFile(filePath)) {
    runGuard();
    // Fall through — do not return. A .py guard trigger (skill_executor.py)
    // should still run its pytest path if matched below.
  }

  if (!filePath.endsWith('.py')) {
    return;
  }

  const basename = path.basename(filePath);
  const dirPath = path.dirname(filePath);

  // Find test files to run
  let testTarget = null;

  if (basename.startsWith('test_')) {
    // This IS a test file — run it directly
    testTarget = filePath;
  } else {
    // Look for exact match first, then dir-wide tests
    const exact = path.join(dirPath, `test_${basename}`);
    const exactInTests = path.join(dirPath, 'tests', `test_${basename}`);

    if (fs.existsSync(exact)) {
      testTarget = exact;
    } else if (fs.existsSync(exactInTests)) {
      testTarget = exactInTests;
    } else {
      // Run all tests in the directory that might be related
      const testsDir = path.join(dirPath, 'tests');
      if (fs.existsSync(testsDir)) {
        testTarget = testsDir;
      }
    }
  }

  if (testTarget) {
    let testPassed = false;
    let testCount = 0;
    try {
      const result = execSync(
        `python -m pytest "${testTarget}" -q --tb=line 2>&1`,
        {
          timeout: 30000,
          cwd: dirPath,
          encoding: 'utf8',
        }
      );
      const lines = result.trim().split('\n');
      const summary = lines[lines.length - 1] || '';
      console.error(`[AutoTest] ${summary}`);
      testPassed = true;
      // Extract test count from summary like "5 passed in 0.12s"
      const countMatch = summary.match(/(\d+) passed/);
      if (countMatch) testCount = parseInt(countMatch[1], 10);
    } catch (e) {
      const output = (e.stdout || e.message || '').trim();
      const lines = output.split('\n');
      const summary = lines[lines.length - 1] || 'FAILED';
      console.error(`[AutoTest] ${summary}`);
    }

    // Record growth on test pass (best-effort, non-blocking)
    if (testPassed) {
      try {
        const hooksDir = __dirname;
        const growthScript = path.join(hooksDir, 'growth_recorder.py');
        const growthInput = JSON.stringify({
          test_file: testTarget,
          test_count: testCount,
        });
        execFileSync('python', [growthScript, 'test_pass'], {
          input: growthInput,
          timeout: 5000,
          stdio: ['pipe', 'ignore', 'pipe'],
          env: Object.assign({}, process.env, {
            PYTHONIOENCODING: 'utf-8',
          }),
        });
      } catch (growthErr) {
        // Fail-open: growth recording failure never blocks
        console.error('[AutoTest] Growth recording skipped: ' +
          (growthErr.stderr ? growthErr.stderr.toString().trim() : growthErr.message));
      }
    }
  }
}

// Only run the stdin/hook flow when executed directly, not when required().
if (require.main === module) {
  let raw = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', chunk => {
    if (raw.length < MAX_STDIN) {
      raw += chunk.substring(0, MAX_STDIN - raw.length);
    }
  });

  process.stdin.on('end', () => {
    try {
      processInput(raw);
    } catch {
      // Any unexpected error: pass through
    }
    process.stdout.write(raw);
    process.exit(0);
  });
}

module.exports = {
  matchesGuardFile,
  parseGuardResults,
  runGuardTests,
  reportPreviousGuardResult,
  processInput,
  GUARD_TRIGGER_FILES,
  GUARD_TEST_SCRIPT,
  GUARD_ASYNC_WRAPPER,
  GUARD_RESULT_FILE,
  GUARD_LOCK_FILE,
  GUARD_RESULT_STALE_MS,
};
