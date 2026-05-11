#!/usr/bin/env node
/**
 * DEPRECATED — C32: Migrated to SessionEnd event hook (session-end.js).
 * This Stop hook is retained for rollback safety during the migration period.
 * SessionEnd hook now handles session_end_auto.py + growth_recorder session_summary.
 * Remove this file after confirming SessionEnd hook stability.
 *
 * Original: Stop Hook: Auto session_end — C21-7
 *
 * Checks if session_end has already been called (via flag file).
 * If not, calls session_end_auto.py to generate summary from STM
 * and run session_end. Fail-open: never blocks, never exit(2).
 */

const { execFileSync } = require('child_process');
const path = require('path');

const MAX_STDIN = 1024 * 1024;
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
    const hooksDir = __dirname;
    const scriptPath = path.join(hooksDir, 'session_end_auto.py');

    execFileSync('python', [scriptPath], {
      timeout: 10000,
      stdio: ['ignore', 'ignore', 'pipe'],
      cwd: hooksDir,
      env: Object.assign({}, process.env, {
        PYTHONIOENCODING: 'utf-8',
        MEMORY_DIR: path.join(
          process.env.USERPROFILE || process.env.HOME || '',
          '.claude', 'projects', 'default', 'memory'
        )
      })
    });
  } catch (err) {
    // Fail-open: log and exit normally
    console.error('[Stop] session_end auto error: ' + (err.stderr ? err.stderr.toString().trim() : err.message));
  }

  // Record growth summary at session end (best-effort)
  try {
    const hooksDir = __dirname;
    const growthScript = path.join(hooksDir, 'growth_recorder.py');
    execFileSync('python', [growthScript, 'session_summary'], {
      input: '',
      timeout: 5000,
      stdio: ['pipe', 'ignore', 'pipe'],
      env: Object.assign({}, process.env, {
        PYTHONIOENCODING: 'utf-8',
      }),
    });
  } catch (growthErr) {
    // Fail-open: growth recording failure never blocks session end
    console.error('[Stop] Growth summary skipped: ' +
      (growthErr.stderr ? growthErr.stderr.toString().trim() : growthErr.message));
  }

  // Always pass through
  process.stdout.write(raw);
  process.exit(0);
});
