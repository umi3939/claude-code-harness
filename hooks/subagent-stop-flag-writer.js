#!/usr/bin/env node
/**
 * G68 Phase 3-1: SubagentStop flag writer.
 *
 * Reads SubagentStop hook stdin (JSON), extracts subagent type from
 * `agent_id` (leading dash-delimited token), and — only for
 * implementer/designer — writes a (b)-flag file used by stop-output-quality.js
 * to enforce explanation-required 3-stage judgment.
 *
 * Flag schema (revised 5, 2026-05-08, F1-iii):
 *   {
 *     "timestamp": <unix_ms>,
 *     "subagent": "implementer" | "designer",
 *     "ttl_ms": 300000
 *   }
 *
 * Proximity model: flag presence + 5min TTL = proximity signal. Sequence
 * counter was removed (revised 5) because Stop hook stdin's `sequence`
 * field is not delivered in production. Short TTL (5min) absorbs user
 * interruption gaps; subagent type filtering at writer side prevents
 * unrelated subagents from producing flags.
 *
 * Sanitization: if the leading token contains a path separator (`/` or `\`),
 * fail-open (do NOT write the flag) to prevent path traversal injection.
 *
 * Fail-open: any error path (stdin parse, missing field, sanitization
 * rejection, file I/O failure) -> exit(0) without writing/with no harm.
 */

'use strict';

const fs = require('fs');
const path = require('path');

const MAX_STDIN = 64 * 1024;

const HOOKS_DIR = __dirname;
const FLAG_FILE = path.join(HOOKS_DIR, '.b-flag-stop-output-quality');

const TARGET_SUBAGENTS = new Set(['implementer', 'designer']);
const DEFAULT_TTL_MS = 5 * 60 * 1000;

let raw = '';

process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  if (raw.length < MAX_STDIN) {
    const remaining = MAX_STDIN - raw.length;
    raw += chunk.substring(0, remaining);
  }
});

process.stdin.on('end', () => {
  try {
    main();
  } catch {
    process.exit(0);
  }
});

process.stdin.on('error', () => {
  process.exit(0);
});

function main() {
  let input;
  try {
    input = JSON.parse(raw);
  } catch {
    process.exit(0);
    return;
  }

  if (!input || typeof input !== 'object') {
    process.exit(0);
    return;
  }

  const agentId = (input.agent_id || input.subagent_type || '').toString();
  if (!agentId) {
    process.exit(0);
    return;
  }

  const subagent = extractSubagentType(agentId);
  if (subagent === null) {
    // sanitization rejected (path separator) -> fail-open, no flag
    process.exit(0);
    return;
  }

  if (!TARGET_SUBAGENTS.has(subagent)) {
    // not a target subagent type -> no flag
    process.exit(0);
    return;
  }

  const flag = {
    timestamp: Date.now(),
    subagent: subagent,
    ttl_ms: DEFAULT_TTL_MS,
  };

  if (!writeFlagAtomic(flag)) {
    // write failed -> fail-open
    process.exit(0);
    return;
  }

  process.exit(0);
}

/**
 * Extract subagent type from agent_id.
 *   "implementer-abc"      -> "implementer"
 *   "designer-xyz"         -> "designer"
 *   "reviewer-1"           -> "reviewer"
 *   "implementer/../etc"   -> null (path separator detected)
 *
 * Returns null if the leading token contains a path separator, signaling
 * fail-open to the caller.
 */
function extractSubagentType(agentId) {
  // Path separator in raw input -> fail-open
  if (/[/\\]/.test(agentId)) {
    return null;
  }
  // Leading token before first '-'
  const idx = agentId.indexOf('-');
  const token = idx >= 0 ? agentId.substring(0, idx) : agentId;
  // Defense in depth: even after token extraction, if separator slipped in
  if (/[/\\]/.test(token)) {
    return null;
  }
  // Disallow empty / whitespace-only tokens
  if (!token || /^\s+$/.test(token)) {
    return null;
  }
  return token;
}

/**
 * Atomic flag write: serialize JSON to a temp file, then rename over
 * FLAG_FILE. Readers either see the previous flag, the new flag, or no
 * flag — never a partial write (rename is atomic on the same filesystem).
 */
function writeFlagAtomic(flag) {
  const payload = JSON.stringify(flag);
  const tmp = FLAG_FILE + '.tmp.' + process.pid + '.' + Date.now();
  try {
    fs.writeFileSync(tmp, payload, { encoding: 'utf8' });
    fs.renameSync(tmp, FLAG_FILE);
    return true;
  } catch {
    try { fs.unlinkSync(tmp); } catch {}
    return false;
  }
}
