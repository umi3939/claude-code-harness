#!/usr/bin/env node
/**
 * Test script for block escalation feature in behavior-guard.js
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const GUARD = path.join(__dirname, 'behavior-guard.js');
const STATE_FILE = path.join(__dirname, '.behavior-guard-state.json');
const DATA_DIR = path.join(__dirname, '..', 'data');
const FIRING_LOG = path.join(DATA_DIR, 'hook_firing_log.jsonl');

let pass = 0;
let fail = 0;

function runGuard(input) {
  return spawnSync('node', [GUARD], {
    input: JSON.stringify(input),
    encoding: 'utf8',
    timeout: 10000,
  });
}

function cleanState() {
  try { fs.unlinkSync(STATE_FILE); } catch {}
}

function test(desc, fn) {
  try {
    const ok = fn();
    if (ok) {
      console.log(`  PASS: ${desc}`);
      pass++;
    } else {
      console.log(`  FAIL: ${desc}`);
      fail++;
    }
  } catch (e) {
    console.log(`  FAIL: ${desc} (error: ${e.message})`);
    fail++;
  }
}

console.log('=== Block Escalation Tests ===');

// Test 1: Single block => normal BLOCKED message (no escalation)
cleanState();
test('1st block shows normal BLOCKED (no escalation)', () => {
  const r = runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore file.py' } });
  return r.status === 2 && r.stderr.includes('BLOCKED') && !r.stderr.includes('ESCALATION');
});

// Test 2: 2nd block => still normal
test('2nd block shows normal BLOCKED (no escalation)', () => {
  const r = runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore file2.py' } });
  return r.status === 2 && r.stderr.includes('BLOCKED') && !r.stderr.includes('ESCALATION');
});

// Test 3: 3rd block => ESCALATION
test('3rd block shows ESCALATION message', () => {
  const r = runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore file3.py' } });
  return r.status === 2 && r.stderr.includes('ESCALATION') && r.stderr.includes('3回');
});

// Test 4: 4th block => still ESCALATION with count=4
test('4th block shows ESCALATION with count=4', () => {
  const r = runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore file4.py' } });
  return r.status === 2 && r.stderr.includes('ESCALATION') && r.stderr.includes('4回');
});

// Test 5: Different rules don't cross-escalate
cleanState();
test('different rules have independent counts', () => {
  // Block 1: git-revert (count=1)
  const r1 = runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore a.py' } });
  // Block from different rule (leader-no-code-edit via .py edit, count=1)
  const r2 = runGuard({ tool_name: 'Edit', tool_input: { file_path: '/tmp/foo.py' } });
  // Block 2: git-revert (count=2, not 3)
  const r3 = runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore b.py' } });
  return r1.status === 2 && !r1.stderr.includes('ESCALATION') &&
         r3.status === 2 && !r3.stderr.includes('ESCALATION');
});

// Test 6: SessionStart resets block counts
cleanState();
test('SessionStart resets block counts', () => {
  // Build up 3 blocks
  runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore a.py' } });
  runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore b.py' } });
  const r3 = runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore c.py' } });
  if (!r3.stderr.includes('ESCALATION')) return false;

  // Simulate SessionStart: whitelist cleanup (keep only persistent keys)
  try {
    const s = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    const keep = [];
    const c = {};
    keep.forEach(k => { if (k in s) c[k] = s[k]; });
    fs.writeFileSync(STATE_FILE, JSON.stringify(c));
  } catch {}

  // Next block should be count=1 (no escalation)
  const r4 = runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore d.py' } });
  return r4.status === 2 && r4.stderr.includes('BLOCKED') && !r4.stderr.includes('ESCALATION');
});

// Test 7: hook_firing_log.jsonl contains count and escalated fields
cleanState();
test('hook_firing_log has count and escalated fields', () => {
  // Truncate log
  try { fs.writeFileSync(FIRING_LOG, ''); } catch {}

  // 3 blocks
  runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore x.py' } });
  runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore y.py' } });
  runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore z.py' } });

  const lines = fs.readFileSync(FIRING_LOG, 'utf8').trim().split('\n');
  if (lines.length < 3) return false;

  const entries = lines.slice(-3).map(l => JSON.parse(l));
  // First entry: count=1, escalated=false
  // Third entry: count=3, escalated=true
  return entries[0].count === 1 && entries[0].escalated === false &&
         entries[2].count === 3 && entries[2].escalated === true;
});

// Test 8: Non-blocking rules don't increment block counts
cleanState();
test('non-blocking rules do not increment block counts', () => {
  // Run a non-blocking tool (git status => no rule match, exit 0)
  const r = runGuard({ tool_name: 'Bash', tool_input: { command: 'git status' } });
  // Check state file
  try {
    const s = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    // _block_counts should not exist or be empty
    return !s._block_counts || Object.keys(s._block_counts).length === 0;
  } catch {
    return true; // no state file = no counts
  }
});

// Test 9: Escalation message contains the rule message for context
cleanState();
test('escalation message includes rule message', () => {
  runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore a.py' } });
  runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore b.py' } });
  const r = runGuard({ tool_name: 'Bash', tool_input: { command: 'git restore c.py' } });
  return r.stderr.includes('ルール:') || r.stderr.includes('git checkout/restore');
});

// Cleanup
cleanState();

console.log('');
console.log(`Results: ${pass} passed, ${fail} failed`);
process.exit(fail > 0 ? 1 : 0);
