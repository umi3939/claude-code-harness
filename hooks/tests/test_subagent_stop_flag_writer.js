#!/usr/bin/env node
/**
 * G68 Phase 1 (revised 5 — F1-iii): test for hooks/subagent-stop-flag-writer.js
 *
 * Coverage (6 cases):
 *   1. implementer detected -> flag created
 *   2. designer detected -> flag created
 *   3. reviewer detected -> flag NOT created
 *   4. TTL: flag has timestamp + ttl_ms = 300000 (5min); no sequence field
 *   5. atomic rename (no partial flag visible)
 *   6. sanitization of subagent name (path separators rejected -> fail-open)
 *
 * Removed (revised 5):
 *   - sequence monotonic test (sequence counter no longer exists)
 *   - parallel race test (sequence counter race no longer applicable;
 *     flag last-write-wins is acceptable per design)
 *
 * Run: node hooks/tests/test_subagent_stop_flag_writer.js
 */

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

let passed = 0;
let failed = 0;
const failures = [];

function assert(condition, msg) {
  if (condition) {
    passed++;
    console.log('  PASS: ' + msg);
  } else {
    failed++;
    failures.push(msg);
    console.log('  FAIL: ' + msg);
  }
}

const WRITER_SCRIPT = path.join(__dirname, '..', 'subagent-stop-flag-writer.js');
const FLAG_FILE = path.join(__dirname, '..', '.b-flag-stop-output-quality');

function cleanup() {
  try { fs.unlinkSync(FLAG_FILE); } catch {}
}

function runWriter(payload) {
  const input = JSON.stringify(payload);
  let status = 0;
  let stderr = '';
  try {
    execFileSync('node', [WRITER_SCRIPT], {
      input: input,
      timeout: 5000,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
  } catch (e) {
    status = e.status === undefined || e.status === null ? -1 : e.status;
    if (e.stderr) stderr = e.stderr.toString();
  }
  return { status, stderr };
}

function readFlag() {
  try {
    const raw = fs.readFileSync(FLAG_FILE, 'utf8');
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// ──────────────────────────────────────────────────────────
// Test 1: implementer subagent → flag created
// ──────────────────────────────────────────────────────────
console.log('Test 1: implementer subagent -> flag created');
{
  cleanup();
  const r = runWriter({ agent_id: 'implementer-abc', reason: 'end_turn' });
  assert(r.status === 0, 'writer exits 0');
  const flag = readFlag();
  assert(flag !== null, 'flag file created');
  assert(flag && flag.subagent === 'implementer', 'flag.subagent === implementer');
  cleanup();
}

// ──────────────────────────────────────────────────────────
// Test 2: designer subagent → flag created
// ──────────────────────────────────────────────────────────
console.log('Test 2: designer subagent -> flag created');
{
  cleanup();
  const r = runWriter({ agent_id: 'designer-xyz', reason: 'end_turn' });
  assert(r.status === 0, 'writer exits 0');
  const flag = readFlag();
  assert(flag !== null, 'flag file created');
  assert(flag && flag.subagent === 'designer', 'flag.subagent === designer');
  cleanup();
}

// ──────────────────────────────────────────────────────────
// Test 3: reviewer subagent → flag NOT created
// ──────────────────────────────────────────────────────────
console.log('Test 3: reviewer subagent -> flag NOT created');
{
  cleanup();
  const r = runWriter({ agent_id: 'reviewer-1', reason: 'end_turn' });
  assert(r.status === 0, 'writer exits 0');
  assert(!fs.existsSync(FLAG_FILE), 'flag file NOT created for reviewer');
  cleanup();
}

// ──────────────────────────────────────────────────────────
// Test 4: TTL = 5min (300000ms); no sequence field (revised 5)
// ──────────────────────────────────────────────────────────
console.log('Test 4: TTL = 5min, no sequence field');
{
  cleanup();
  const before = Date.now();
  const r = runWriter({ agent_id: 'implementer-ttl', reason: 'end_turn' });
  const after = Date.now();
  const flag = readFlag();
  assert(flag !== null, 'flag created');
  assert(flag && typeof flag.timestamp === 'number', 'timestamp is number');
  assert(flag && flag.timestamp >= before - 100 && flag.timestamp <= after + 100, 'timestamp within reasonable window');
  assert(flag && flag.ttl_ms === 300000, 'ttl_ms === 300000 (5 minutes)');
  assert(flag && flag.sequence === undefined, 'no sequence field (revised 5)');
  cleanup();
}

// ──────────────────────────────────────────────────────────
// Test 5: atomic rename (flag is either complete or absent — never partial)
// ──────────────────────────────────────────────────────────
console.log('Test 5: atomic rename / well-formed JSON');
{
  cleanup();
  for (let i = 0; i < 5; i++) {
    runWriter({ agent_id: 'implementer-' + i, reason: 'end_turn' });
    const flag = readFlag();
    assert(flag !== null, 'iter ' + i + ': flag is parseable JSON (atomic)');
    assert(flag && flag.subagent === 'implementer', 'iter ' + i + ': flag content valid');
  }
  cleanup();
}

// ──────────────────────────────────────────────────────────
// Test 6: sanitization (path separators in agent_id rejected -> fail-open)
// ──────────────────────────────────────────────────────────
console.log('Test 6: path separator sanitization');
{
  cleanup();
  // agent_id with path traversal attempt
  const r = runWriter({ agent_id: 'implementer/../../etc/passwd', reason: 'end_turn' });
  // Fail-open: writer exits 0 (does not crash). May or may not create flag,
  // but flag MUST NOT contain raw path separator.
  assert(r.status === 0, 'writer fail-open on suspicious agent_id');
  const flag = readFlag();
  if (flag !== null) {
    assert(!/[\\/]/.test(flag.subagent || ''), 'flag.subagent has no path separator');
  } else {
    assert(true, 'flag NOT created (sanitization rejected input)');
  }
  cleanup();
}

console.log('\n=== Summary ===');
console.log('passed: ' + passed);
console.log('failed: ' + failed);
if (failed > 0) {
  console.log('\nFailures:');
  failures.forEach(f => console.log('  - ' + f));
}
process.exit(failed > 0 ? 1 : 0);
