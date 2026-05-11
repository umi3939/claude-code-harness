#!/usr/bin/env node
/**
 * SessionEnd Hook: Consolidated session end processing — C32
 *
 * Migrated from Stop hooks (stop-session-end.js + stop-session-aar.js).
 * Runs on the SessionEnd event (fires exactly once per session).
 *
 * Processing chain (sequential, fail-open each step):
 *   1. session_end_auto.py — STM summary + session_end MCP call (10s)
 *   2. growth_recorder.py session_summary — health summary (5s)
 *   3. growth_recorder.py session_aar — after-action review (8s)
 *   4. memory_consolidate_check.py — auto-trigger consolidation if new lessons (5s)
 *
 * Requires: CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS >= 25000
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
  const hooksDir = __dirname;
  const home = process.env.USERPROFILE || process.env.HOME || '';
  const dataDir = path.join(__dirname, '..', 'data');

  // --- Resolve MEMORY_DIR dynamically (same as stop-session-aar.js) ---
  let memoryDir = '';
  const projectsDir = path.join(home, '.claude', 'projects');
  try {
    const dirs = fs.readdirSync(projectsDir);
    for (const d of dirs) {
      const candidate = path.join(projectsDir, d, 'memory');
      if (fs.existsSync(candidate)) {
        memoryDir = candidate;
        break;
      }
    }
  } catch {
    // projectsDir may not exist
  }
  if (!memoryDir) {
    memoryDir = path.join(home, '.claude', 'projects', 'default', 'memory');
  }

  // --- Read session ID from .session-start-time ---
  // Normalize to match observation-logger.js format: 's' + epoch, 12 chars
  let sessionId = '';
  try {
    const rawEpoch = fs.readFileSync(
      path.join(hooksDir, '.session-start-time'), 'utf8'
    ).trim();
    // observation-logger.js writes sid as ('s' + epoch).substring(0, 12)
    sessionId = ('s' + rawEpoch).substring(0, 12);
  } catch {
    // No session ID available
  }

  // --- Step 0: Inject session-end.md skill content ---
  try {
    const skillPath = path.join(hooksDir, '..', '.claude', 'commands', 'session-end.md');
    if (fs.existsSync(skillPath)) {
      const skillContent = fs.readFileSync(skillPath, 'utf8').substring(0, 200);
      console.error('[SessionEnd] Skill: session-end.md loaded (' + skillContent.length + ' chars)');
    }
  } catch {
    // Skill file read failure is non-fatal
  }

  // --- Step 1: session_end_auto.py (10s timeout) ---
  try {
    const scriptPath = path.join(hooksDir, 'session_end_auto.py');
    execFileSync('python', [scriptPath], {
      timeout: 10000,
      stdio: ['ignore', 'ignore', 'pipe'],
      cwd: hooksDir,
      env: Object.assign({}, process.env, {
        PYTHONIOENCODING: 'utf-8',
        MEMORY_DIR: memoryDir,
      }),
    });
  } catch (err) {
    console.error('[SessionEnd] session_end_auto error: ' +
      (err.stderr ? err.stderr.toString().trim() : err.message));
  }

  // --- Step 2: growth_recorder.py session_summary (5s timeout) ---
  try {
    const growthScript = path.join(hooksDir, 'growth_recorder.py');
    execFileSync('python', [growthScript, 'session_summary'], {
      input: '',
      timeout: 5000,
      stdio: ['pipe', 'ignore', 'pipe'],
      env: Object.assign({}, process.env, {
        PYTHONIOENCODING: 'utf-8',
      }),
    });
  } catch (err) {
    console.error('[SessionEnd] Growth summary skipped: ' +
      (err.stderr ? err.stderr.toString().trim() : err.message));
  }

  // --- Step 3: growth_recorder.py session_aar (8s timeout) ---
  try {
    // Build AAR input from STM (same logic as stop-session-aar.js)
    let summary = '';
    let completed = [];
    let pending = [];
    let decisions = [];

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
    } catch {
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

    const growthScript = path.join(hooksDir, 'growth_recorder.py');
    execFileSync('python', [growthScript, 'session_aar'], {
      input: stdinData,
      timeout: 8000,
      stdio: ['pipe', 'ignore', 'pipe'],
      env: Object.assign({}, process.env, {
        PYTHONIOENCODING: 'utf-8',
      }),
    });
  } catch (err) {
    console.error('[SessionEnd] Session AAR skipped: ' +
      (err.stderr ? err.stderr.toString().trim() : err.message));
  }

  // --- Step 4: memory_consolidate check (5s timeout) ---
  try {
    const consolidateScript = path.join(hooksDir, 'memory_consolidate_check.py');
    if (fs.existsSync(consolidateScript)) {
      execFileSync('python', [consolidateScript], {
        timeout: 5000,
        stdio: ['ignore', 'ignore', 'pipe'],
        cwd: hooksDir,
        env: Object.assign({}, process.env, {
          PYTHONIOENCODING: 'utf-8',
          MEMORY_DIR: memoryDir,
        }),
      });
    }
  } catch (err) {
    console.error('[SessionEnd] memory_consolidate check skipped: ' +
      (err.stderr ? err.stderr.toString().trim() : err.message));
  }

  // Always exit normally
  process.exit(0);
});
