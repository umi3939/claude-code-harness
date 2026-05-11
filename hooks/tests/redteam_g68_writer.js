#!/usr/bin/env node
/**
 * G68 Red Team — subagent-stop-flag-writer.js path traversal and
 * non-target subagent verification (N8 担保).
 */

'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const HOOK = path.join(__dirname, '..', 'subagent-stop-flag-writer.js');
const FLAG = path.join(__dirname, '..', '.b-flag-stop-output-quality');

function clean() {
  try { fs.unlinkSync(FLAG); } catch {}
}

function run(payload) {
  const res = spawnSync('node', [HOOK], {
    input: JSON.stringify(payload),
    encoding: 'utf8',
    timeout: 10000,
  });
  return { code: res.status };
}

const scenarios = [
  { id: 'W1', label: 'agent_id=implementer-x',  payload: { agent_id: 'implementer-x' }, expectFlag: true },
  { id: 'W2', label: 'agent_id=designer-x',     payload: { agent_id: 'designer-x' },    expectFlag: true },
  { id: 'W3', label: 'agent_id=reviewer-x',     payload: { agent_id: 'reviewer-x' },    expectFlag: false },
  { id: 'W4', label: 'agent_id=discusser-x',    payload: { agent_id: 'discusser-x' },   expectFlag: false },
  { id: 'W5', label: 'agent_id=red-team-x',     payload: { agent_id: 'red-team-x' },    expectFlag: false },
  { id: 'W6', label: 'path traversal /',        payload: { agent_id: 'implementer/../etc' }, expectFlag: false },
  { id: 'W7', label: 'path traversal \\',       payload: { agent_id: 'implementer\\..\\etc' }, expectFlag: false },
  { id: 'W8', label: 'empty agent_id',          payload: { agent_id: '' },              expectFlag: false },
  { id: 'W9', label: 'invalid JSON missing',    payload: {},                            expectFlag: false },
  { id: 'W10', label: 'subagent_type alt key',  payload: { subagent_type: 'implementer-y' }, expectFlag: true },
  { id: 'W11', label: 'whitespace token',       payload: { agent_id: '   -x' },         expectFlag: false },
  { id: 'W12', label: 'plain implementer',      payload: { agent_id: 'implementer' },   expectFlag: true },
];

let pass = 0, fail = 0;

console.log('=== Red Team W: writer scenarios ===');
for (const s of scenarios) {
  clean();
  const r = run(s.payload);
  const exists = fs.existsSync(FLAG);
  const ok = (s.expectFlag === exists) && r.code === 0;
  if (ok) pass++; else fail++;
  console.log(`  [${ok ? 'OK' : 'NG'}] ${s.id} ${s.label} exit=${r.code} flag=${exists} expectFlag=${s.expectFlag}`);
}

clean();

console.log(`\nSummary: passed=${pass} failed=${fail}`);
process.exit(fail === 0 ? 0 : 1);
