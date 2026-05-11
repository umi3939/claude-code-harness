#!/usr/bin/env node
/**
 * DEPRECATED — C32: Migrated to SessionEnd event hook (session-end.js).
 * This Stop hook is retained for rollback safety during the migration period.
 * SessionEnd hook now handles growth_recorder session_aar.
 * Remove this file after confirming SessionEnd hook stability.
 *
 * Original: Stop Hook: Auto session AAR — After-Action Review at session end.
 *
 * Separate Stop hook entry (independent timeout from stop-session-end.js).
 * Calls growth_recorder.py session_aar with observations data.
 * Fail-open: never blocks, never exit(2).
 */

const { execFileSync } = require('child_process');
const fs = require('fs');
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
    const home = process.env.USERPROFILE || process.env.HOME || '';
    const hooksDir = __dirname;
    const dataDir = path.join(__dirname, '..', 'data');
    // Dynamically find project memory directory (no hardcoded project name)
    const projectsDir = path.join(home, '.claude', 'projects');
    let memoryDir = '';
    try {
      const dirs = fs.readdirSync(projectsDir);
      for (const d of dirs) {
        const candidate = path.join(projectsDir, d, 'memory');
        if (fs.existsSync(candidate)) {
          memoryDir = candidate;
          break;
        }
      }
    } catch (_e) {
      // projectsDir may not exist
    }
    if (!memoryDir) {
      memoryDir = path.join(home, '.claude', 'projects', 'default', 'memory');
    }
    const growthScript = path.join(hooksDir, 'growth_recorder.py');

    // Read session ID from .session-start-time
    // Normalize to match observation-logger.js format: 's' + epoch, 12 chars
    let sessionId = '';
    try {
      const rawEpoch = fs.readFileSync(
        path.join(hooksDir, '.session-start-time'), 'utf8'
      ).trim();
      sessionId = ('s' + rawEpoch).substring(0, 12);
    } catch (_e) {
      // No session ID available
    }

    // Read session_end_auto output if available (.session-end-done flag exists)
    let summary = '';
    let completed = [];
    let pending = [];
    let decisions = [];

    // Try to read STM for summary data
    try {
      const stmPath = path.join(memoryDir, 'short_term_memory.json');
      if (fs.existsSync(stmPath)) {
        const stmData = JSON.parse(fs.readFileSync(stmPath, 'utf8'));
        const entries = stmData.entries || [];
        const thoughts = entries
          .filter(e => e.category === 'thought')
          .map(e => e.content);
        if (thoughts.length > 0) {
          summary = thoughts.slice(-3).join('; ').substring(0, 1000);
        }
      }
    } catch (_e) {
      // STM read failure is non-fatal
    }

    if (!summary) {
      summary = 'Session completed (no summary available)';
    }

    const stdinData = JSON.stringify({
      summary: summary,
      completed: completed,
      pending: pending,
      decisions: decisions,
      session_id: sessionId,
      data_dir: dataDir,
      memory_dir: memoryDir,
    });

    execFileSync('python', [growthScript, 'session_aar'], {
      input: stdinData,
      timeout: 8000,
      stdio: ['pipe', 'ignore', 'pipe'],
      env: Object.assign({}, process.env, {
        PYTHONIOENCODING: 'utf-8',
      }),
    });
  } catch (err) {
    // Fail-open: log and exit normally
    console.error('[Stop] Session AAR skipped: ' +
      (err.stderr ? err.stderr.toString().trim() : err.message));
  }

  // Always pass through
  process.stdout.write(raw);
  process.exit(0);
});
