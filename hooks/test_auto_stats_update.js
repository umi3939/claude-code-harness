#!/usr/bin/env node
/**
 * Tests for auto-stats-update.js trigger expansion (Issue #7)
 */

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const HOOKS_DIR = path.join(process.env.USERPROFILE || '', '.claude', 'hooks');
const COOLDOWN_FILE = path.join(HOOKS_DIR, '.auto-stats-last-run');
const SCRIPT = path.join(HOOKS_DIR, 'auto-stats-update.js');

let passed = 0;
let failed = 0;

function setup() {
  try { fs.unlinkSync(COOLDOWN_FILE); } catch {}
}

function runHook(toolName, filePath) {
  var input = JSON.stringify({
    tool_name: toolName,
    tool_input: { file_path: filePath },
  });
  var result = spawnSync('node', [SCRIPT], {
    input: input,
    timeout: 15000,
    encoding: 'utf8',
  });
  return {
    stdout: result.stdout || '',
    stderr: result.stderr || '',
    exitCode: result.status,
  };
}

function test(name, fn) {
  try {
    setup();
    fn();
    passed++;
    console.log('  PASS: ' + name);
  } catch (e) {
    failed++;
    console.log('  FAIL: ' + name + ' -- ' + e.message);
  }
}

function assert(condition, msg) {
  if (!condition) throw new Error(msg || 'Assertion failed');
}

console.log('=== auto-stats-update.js tests ===');

// --- Passthrough tests ---
test('Read tool is ignored (passthrough)', function() {
  var result = runHook('Read', '/some/file.py');
  assert(!result.stderr.includes('[AutoStats]'), 'Read should not trigger');
});

test('Bash tool is ignored (passthrough)', function() {
  var result = runHook('Bash', '');
  assert(!result.stderr.includes('[AutoStats]'), 'Bash should not trigger');
});

// --- Exclusion list tests ---
test('MEMORY.md is excluded (circular prevention)', function() {
  var result = runHook('Edit', '/c/Users/user/.claude/projects/memory/MEMORY.md');
  assert(!result.stderr.includes('Updated doc counts'), 'MEMORY.md should be excluded');
});

test('mcp-tools.md is excluded (circular prevention)', function() {
  var result = runHook('Write', '/c/Users/user/.claude/commands/mcp-tools.md');
  assert(!result.stderr.includes('Updated doc counts'), 'mcp-tools.md should be excluded');
});

// --- Infra triggers ---
test('.mcp.json triggers update (infra)', function() {
  var result = runHook('Edit', '/c/Users/user/.claude/.mcp.json');
  assert(result.stderr.includes('[AutoStats]'), 'Expected [AutoStats] for .mcp.json');
});

test('behavior-rules.json triggers update (infra)', function() {
  var result = runHook('Edit', '/c/Users/user/.claude/behavior-rules.json');
  assert(result.stderr.includes('[AutoStats]'), 'Expected [AutoStats] for behavior-rules.json');
});

test('agents/*.md triggers update (infra)', function() {
  var result = runHook('Write', '/c/Users/user/.claude/agents/designer.md');
  assert(result.stderr.includes('[AutoStats]'), 'Expected [AutoStats] for agents/*.md');
});

// --- Source code triggers ---
test('.py file triggers update (source code)', function() {
  var result = runHook('Edit', '/c/Users/user/.claude/tools/stats_updater.py');
  assert(result.stderr.includes('[AutoStats]'), 'Expected [AutoStats] for .py file');
});

test('.js file triggers update (source code)', function() {
  var result = runHook('Edit', '/c/Users/user/.claude/hooks/some-hook.js');
  assert(result.stderr.includes('[AutoStats]'), 'Expected [AutoStats] for .js file');
});

test('.md file triggers update (source code)', function() {
  var result = runHook('Write', '/c/Users/user/.claude/docs/design.md');
  assert(result.stderr.includes('[AutoStats]'), 'Expected [AutoStats] for .md file');
});

// --- Cooldown tests ---
test('source code trigger respects cooldown', function() {
  fs.writeFileSync(COOLDOWN_FILE, String(Date.now()));
  var result = runHook('Edit', '/c/Users/user/.claude/tools/something.py');
  assert(!result.stderr.includes('Updated doc counts'), 'Should NOT run during cooldown');
});

test('infra trigger ignores cooldown', function() {
  fs.writeFileSync(COOLDOWN_FILE, String(Date.now()));
  var result = runHook('Edit', '/c/Users/user/.claude/.mcp.json');
  assert(result.stderr.includes('[AutoStats]'), 'Infra should bypass cooldown');
});

test('expired cooldown allows source code trigger', function() {
  fs.writeFileSync(COOLDOWN_FILE, String(Date.now() - 6 * 60 * 1000));
  var result = runHook('Edit', '/c/Users/user/.claude/tools/something.py');
  assert(result.stderr.includes('[AutoStats]'), 'Should run after cooldown expires');
});

test('corrupted cooldown file allows execution', function() {
  fs.writeFileSync(COOLDOWN_FILE, 'not-a-number');
  var result = runHook('Edit', '/c/Users/user/.claude/tools/something.py');
  assert(result.stderr.includes('[AutoStats]'), 'Corrupted file should allow execution');
});

// --- Non-matching ---
test('.txt file does NOT trigger', function() {
  var result = runHook('Edit', '/c/Users/user/.claude/notes.txt');
  assert(!result.stderr.includes('[AutoStats]'), '.txt should not trigger');
});

test('.json (non-infra) does NOT trigger', function() {
  var result = runHook('Edit', '/c/Users/user/.claude/data/config.json');
  assert(!result.stderr.includes('[AutoStats]'), 'non-infra .json should not trigger');
});

// --- Stdout passthrough ---
test('stdout passes through input unchanged', function() {
  var result = runHook('Read', '/some/file');
  assert(result.stdout.includes('tool_name'), 'stdout should passthrough input');
});

console.log('\n=== Results: ' + passed + ' passed, ' + failed + ' failed ===');

// Cleanup
try { fs.unlinkSync(COOLDOWN_FILE); } catch {}
process.exit(failed > 0 ? 1 : 0);
