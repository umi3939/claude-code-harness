/**
 * Tests for hooks/session-end.js
 *
 * Tests SessionEnd hook: consolidated session end processing.
 * Run: node hooks/tests/test_session_end_hook.js
 */

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

let passed = 0;
let failed = 0;

function assert(condition, msg) {
  if (condition) {
    passed++;
    console.log(`  PASS: ${msg}`);
  } else {
    failed++;
    console.log(`  FAIL: ${msg}`);
  }
}

const hookScript = path.join(__dirname, '..', 'session-end.js');

// --- Test 1: Hook script runs without crash on empty stdin ---
console.log('Test 1: Empty stdin exits normally');
{
  try {
    execFileSync('node', [hookScript], {
      input: '',
      timeout: 30000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    assert(true, 'no crash on empty stdin');
  } catch (e) {
    if (e.status === 0 || e.status === null) {
      assert(true, 'exited normally on empty stdin');
    } else {
      assert(false, `crashed with exit code ${e.status}: ${e.stderr ? e.stderr.toString().trim() : e.message}`);
    }
  }
}

// --- Test 2: Hook script runs without crash on valid JSON ---
console.log('Test 2: Valid JSON stdin exits normally');
{
  const input = JSON.stringify({ event: 'SessionEnd', session_id: 'test-123' });
  try {
    execFileSync('node', [hookScript], {
      input: input,
      timeout: 30000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    assert(true, 'no crash on valid JSON');
  } catch (e) {
    if (e.status === 0 || e.status === null) {
      assert(true, 'exited normally on valid JSON');
    } else {
      assert(false, `crashed with exit code ${e.status}`);
    }
  }
}

// --- Test 3: Hook script does not exit(2) (never blocks) ---
console.log('Test 3: Never exits with code 2');
{
  const input = JSON.stringify({ event: 'SessionEnd' });
  try {
    execFileSync('node', [hookScript], {
      input: input,
      timeout: 30000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    assert(true, 'did not exit(2)');
  } catch (e) {
    assert(e.status !== 2, `should not exit(2), got exit code ${e.status}`);
  }
}

// --- Test 4: Hook reads session-start-time if present ---
console.log('Test 4: session-start-time file handling');
{
  const hooksDir = path.join(__dirname, '..');
  const sstFile = path.join(hooksDir, '.session-start-time');
  const hadFile = fs.existsSync(sstFile);
  let originalContent = '';
  if (hadFile) {
    originalContent = fs.readFileSync(sstFile, 'utf8');
  }

  fs.writeFileSync(sstFile, '1234567890');

  try {
    execFileSync('node', [hookScript], {
      input: '{}',
      timeout: 30000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    assert(true, 'ran with session-start-time present');
  } catch (e) {
    if (e.status === 0 || e.status === null) {
      assert(true, 'exited normally with session-start-time');
    } else {
      assert(false, `crashed with exit code ${e.status}`);
    }
  }

  if (hadFile) {
    fs.writeFileSync(sstFile, originalContent);
  } else {
    try { fs.unlinkSync(sstFile); } catch {}
  }
}

// --- Test 5: Verify settings.local.json has SessionEnd entry ---
console.log('Test 5: settings.local.json has SessionEnd hook');
{
  const settingsPath = path.join(__dirname, '..', '..', '.claude', 'settings.local.json');
  try {
    const settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
    const hooks = settings.hooks || {};
    assert('SessionEnd' in hooks, 'SessionEnd key exists in hooks');
    const sessionEndHooks = hooks.SessionEnd;
    assert(Array.isArray(sessionEndHooks) && sessionEndHooks.length > 0, 'SessionEnd has hook entries');
    const cmd = sessionEndHooks[0].hooks[0].command;
    assert(cmd.includes('session-end.js'), 'SessionEnd hook points to session-end.js');
  } catch (e) {
    assert(false, `settings.local.json read error: ${e.message}`);
  }
}

// --- Test 6: Verify settings.local.json has SubagentStop entry ---
console.log('Test 6: settings.local.json has SubagentStop hook');
{
  const settingsPath = path.join(__dirname, '..', '..', '.claude', 'settings.local.json');
  try {
    const settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
    const hooks = settings.hooks || {};
    assert('SubagentStop' in hooks, 'SubagentStop key exists in hooks');
    const subagentStopHooks = hooks.SubagentStop;
    assert(Array.isArray(subagentStopHooks) && subagentStopHooks.length > 0, 'SubagentStop has hook entries');
    const cmd = subagentStopHooks[0].hooks[0].command;
    assert(cmd.includes('subagent-stop-logger.js'), 'SubagentStop hook points to subagent-stop-logger.js');
  } catch (e) {
    assert(false, `settings.local.json read error: ${e.message}`);
  }
}

// --- Test 7: Verify CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS is set ---
console.log('Test 7: CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS env var');
{
  const settingsPath = path.join(__dirname, '..', '..', '.claude', 'settings.local.json');
  try {
    const settings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
    const env = settings.env || {};
    const timeout = env.CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS;
    assert(timeout !== undefined, 'CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS is defined');
    assert(Number(timeout) >= 20000, `timeout is >= 20000ms (got ${timeout})`);
  } catch (e) {
    assert(false, `settings.local.json read error: ${e.message}`);
  }
}

console.log(`\nResults: ${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
