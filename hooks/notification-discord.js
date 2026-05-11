#!/usr/bin/env node
/**
 * Notification Hook: Discord DM notification for Claude Code lifecycle events — C33
 *
 * Forwards Notification events (permission_prompt, idle_prompt, etc.)
 * to Discord DM via notification_sender.py child process.
 *
 * Flow:
 *   1. Read stdin JSON (Notification event data)
 *   2. Pass event JSON to notification_sender.py as argv[1]
 *   3. notification_sender.py handles filtering + Discord send
 *
 * Fail-open: never blocks, never exit(2).
 * A1 risk: hook failure does NOT re-fire Notification (stderr only).
 */

const { execFileSync } = require('child_process');
const path = require('path');

const MAX_STDIN = 1024 * 1024;
const SENDER_TIMEOUT_MS = 10000;

let raw = '';

process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  if (raw.length < MAX_STDIN) {
    const remaining = MAX_STDIN - raw.length;
    raw += chunk.substring(0, remaining);
  }
});

process.stdin.on('end', () => {
  // Validate we got some data
  if (!raw.trim()) {
    console.error('[Notification] Empty stdin, skipping');
    process.exit(0);
  }

  // Validate JSON parseable (basic check)
  try {
    JSON.parse(raw);
  } catch {
    console.error('[Notification] Invalid stdin JSON, skipping');
    process.exit(0);
  }

  // Delegate to notification_sender.py
  try {
    const senderScript = path.join(__dirname, 'notification_sender.py');
    execFileSync('python', [senderScript, raw], {
      timeout: SENDER_TIMEOUT_MS,
      stdio: ['ignore', 'ignore', 'pipe'],
      env: Object.assign({}, process.env, {
        PYTHONIOENCODING: 'utf-8',
      }),
    });
  } catch (err) {
    // Fail-open: notification failure never blocks Claude Code
    // A1 risk: this error goes to stderr only, NOT a new Notification event
    console.error('[Notification] Sender error: ' +
      (err.stderr ? err.stderr.toString().trim().substring(0, 200) : err.message));
  }

  // Always exit normally
  process.exit(0);
});
