/**
 * Tests for hooks/session-init.js
 *
 * Tests the session initialization logic extracted from settings.json
 * SessionStart node -e one-liner.
 *
 * Run: node hooks/tests/test_session_init.js
 */

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

// Create temp directories
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'session-init-test-'));
const tmpCwd = fs.mkdtempSync(path.join(os.tmpdir(), 'session-init-cwd-'));

function cleanup() {
  try { fs.rmSync(tmpDir, { recursive: true }); } catch {}
  try { fs.rmSync(tmpCwd, { recursive: true }); } catch {}
}

// Load module
const { runSessionInit } = require(path.join(__dirname, '..', 'session-init.js'));

// --- Test 1: Flag files are deleted ---
console.log('Test 1: Flag files are deleted');
const flagFiles = [
  '.session-start-done',
  '.memory-search-done',
  '.lesson-feedback-state.json',
  '.team-created',
  '.dev-flow-state',
  '.session-end-done',
];
for (const f of flagFiles) {
  fs.writeFileSync(path.join(tmpDir, f), 'test');
}
for (const f of flagFiles) {
  assert(fs.existsSync(path.join(tmpDir, f)), `pre: ${f} exists`);
}
runSessionInit(tmpDir, tmpCwd);
for (const f of flagFiles) {
  assert(!fs.existsSync(path.join(tmpDir, f)), `post: ${f} deleted`);
}

// --- Test 2: session-start-time is written ---
console.log('Test 2: session-start-time is written');
const tsFile = path.join(tmpDir, '.session-start-time');
assert(fs.existsSync(tsFile), '.session-start-time exists');
const ts = fs.readFileSync(tsFile, 'utf8');
const tsNum = Number(ts);
assert(!isNaN(tsNum), '.session-start-time is a number');
assert(Math.abs(tsNum - Date.now()) < 5000, '.session-start-time is recent');

// --- Test 3: behavior-guard-state.json is reset (keep=[] means empty) ---
console.log('Test 3: behavior-guard-state.json is reset');
const guardFile = path.join(tmpDir, '.behavior-guard-state.json');
fs.writeFileSync(guardFile, JSON.stringify({
  "some_key": "value",
  "another": 123,
}));
runSessionInit(tmpDir, tmpCwd);
const guardContent = JSON.parse(fs.readFileSync(guardFile, 'utf8'));
assert(Object.keys(guardContent).length === 0, 'guard state reset to empty');

// --- Test 4: .mcp.json copy (source exists, dest does not) ---
console.log('Test 4: .mcp.json copy when dest missing');
const mcpSrc = path.join(tmpDir, 'test-mcp-source.json');
const mcpDst = path.join(tmpCwd, '.mcp.json');
try { fs.unlinkSync(mcpDst); } catch {}
fs.writeFileSync(mcpSrc, '{"test": true}');
assert(!fs.existsSync(mcpDst), 'pre: .mcp.json dest does not exist');
runSessionInit(tmpDir, tmpCwd, mcpSrc);
assert(fs.existsSync(mcpDst), 'post: .mcp.json copied to cwd');
const mcpContent = JSON.parse(fs.readFileSync(mcpDst, 'utf8'));
assert(mcpContent.test === true, '.mcp.json content matches source');

// --- Test 5: .mcp.json NOT overwritten if dest exists ---
console.log('Test 5: .mcp.json not overwritten if dest exists');
fs.writeFileSync(mcpDst, '{"existing": true}');
runSessionInit(tmpDir, tmpCwd, mcpSrc);
const mcpExisting = JSON.parse(fs.readFileSync(mcpDst, 'utf8'));
assert(mcpExisting.existing === true, '.mcp.json not overwritten');

// --- Test 6: Idempotent (no error on second run) ---
console.log('Test 6: Idempotent on second run');
try {
  runSessionInit(tmpDir, tmpCwd);
  assert(true, 'no error on second run');
} catch (e) {
  assert(false, `error on second run: ${e.message}`);
}

// --- Test 7: Missing guard state file is handled ---
console.log('Test 7: Missing guard state file');
try { fs.unlinkSync(guardFile); } catch {}
try {
  runSessionInit(tmpDir, tmpCwd);
  assert(true, 'no error when guard state missing');
} catch (e) {
  assert(false, `error when guard state missing: ${e.message}`);
}

// --- Test 8: .mcp.json copy skipped when source missing ---
console.log('Test 8: .mcp.json copy skipped when source missing');
try { fs.unlinkSync(mcpDst); } catch {}
try { fs.unlinkSync(mcpSrc); } catch {}
try {
  runSessionInit(tmpDir, tmpCwd, mcpSrc);
  assert(!fs.existsSync(mcpDst), '.mcp.json not created when source missing');
} catch (e) {
  assert(false, `error when mcp source missing: ${e.message}`);
}

// Cleanup
cleanup();

console.log(`\nResults: ${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
