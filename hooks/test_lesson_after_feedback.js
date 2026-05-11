#!/usr/bin/env node
/**
 * Tests for lesson-after-feedback.js episode_type filter (Issue #8)
 */

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const HOOKS_DIR = path.join(process.env.USERPROFILE || '', '.claude', 'hooks');
const DATA_DIR = path.join(process.env.USERPROFILE || '', '.claude', 'data');
const OBS_FILE = path.join(DATA_DIR, 'observations.jsonl');
const STATE_FILE = path.join(HOOKS_DIR, '.lesson-feedback-state.json');
const SESSION_START_FILE = path.join(HOOKS_DIR, '.session-start-time');
const SCRIPT = path.join(HOOKS_DIR, 'lesson-after-feedback.js');

let passed = 0;
let failed = 0;
let counter = 0;

function uniqueTs() {
  counter++;
  return new Date(Date.now() + counter * 1000).toISOString();
}

function setup() {
  fs.writeFileSync(SESSION_START_FILE, String(Date.now() - 60000));
  try { fs.unlinkSync(STATE_FILE); } catch {}
}

function writeObs(entries) {
  const lines = entries.map(e => JSON.stringify(e)).join('\n');
  fs.writeFileSync(OBS_FILE, lines + '\n');
}

function runHook(stdin) {
  const result = spawnSync('node', [SCRIPT], {
    input: stdin || '{}',
    timeout: 5000,
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

console.log('=== lesson-after-feedback.js tests ===');

test('memory_record with episode_type=feedback triggers warning', function() {
  var ts = uniqueTs();
  writeObs([
    { ts: ts, tool: 'mcp__memory-tools__memory_record', params: { episode_type: 'feedback', summary: 'test' } },
  ]);
  var result = runHook();
  assert(result.stderr.includes('WARNING'), 'Expected warning for feedback type, got: ' + result.stderr);
});

test('memory_record with episode_type=error triggers warning', function() {
  var ts = uniqueTs();
  writeObs([
    { ts: ts, tool: 'mcp__memory-tools__memory_record', params: { episode_type: 'error', summary: 'test error' } },
  ]);
  var result = runHook();
  assert(result.stderr.includes('WARNING'), 'Expected warning for error episode_type, got: ' + result.stderr);
});

test('memory_record with episode_type=observation does NOT trigger warning', function() {
  var ts = uniqueTs();
  writeObs([
    { ts: ts, tool: 'mcp__memory-tools__memory_record', params: { episode_type: 'observation', summary: 'just noting' } },
  ]);
  var result = runHook();
  assert(!result.stderr.includes('WARNING'), 'Should NOT warn for observation type');
});

test('memory_record with episode_type=milestone does NOT trigger warning', function() {
  var ts = uniqueTs();
  writeObs([
    { ts: ts, tool: 'mcp__memory-tools__memory_record', params: { episode_type: 'milestone', summary: 'milestone' } },
  ]);
  var result = runHook();
  assert(!result.stderr.includes('WARNING'), 'Should NOT warn for milestone type');
});

test('memory_record with no params does NOT trigger warning', function() {
  var ts = uniqueTs();
  writeObs([
    { ts: ts, tool: 'mcp__memory-tools__memory_record', params: {} },
  ]);
  var result = runHook();
  assert(!result.stderr.includes('WARNING'), 'Should NOT warn when episode_type is missing');
});

test('feedback + lessons_registry call suppresses warning', function() {
  var ts = uniqueTs();
  writeObs([
    { ts: ts, tool: 'mcp__memory-tools__memory_record', params: { episode_type: 'feedback', summary: 'learned' } },
    { ts: ts, tool: 'Bash', params: { cmd: 'python lessons_registry.py add "test"' } },
  ]);
  var result = runHook();
  assert(!result.stderr.includes('WARNING'), 'Should NOT warn when lessons_registry was called');
});

test('no memory_record calls produces no warning', function() {
  var ts = uniqueTs();
  writeObs([
    { ts: ts, tool: 'Bash', params: { cmd: 'git status' } },
  ]);
  var result = runHook();
  assert(!result.stderr.includes('WARNING'), 'Should NOT warn without any memory_record calls');
});

test('mixed non-feedback types do not trigger warning', function() {
  var ts = uniqueTs();
  writeObs([
    { ts: ts, tool: 'mcp__memory-tools__memory_record', params: { episode_type: 'observation', summary: 'safe' } },
    { ts: ts, tool: 'mcp__memory-tools__memory_record', params: { episode_type: 'milestone', summary: 'safe' } },
  ]);
  var result = runHook();
  assert(!result.stderr.includes('WARNING'), 'Should NOT warn for non-feedback/error types');
});

console.log('\n=== Results: ' + passed + ' passed, ' + failed + ' failed ===');
process.exit(failed > 0 ? 1 : 0);
