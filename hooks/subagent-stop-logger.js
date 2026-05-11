#!/usr/bin/env node
/**
 * SubagentStop Hook: Agent lifecycle observation logger — C32
 *
 * Records agent lifecycle end events to observations.jsonl and
 * delegates to growth_recorder.py subagent_stop for mastery tracking.
 *
 * Fail-open: never blocks, never exit(2).
 */

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'data');
const OBS_FILE = path.join(DATA_DIR, 'observations.jsonl');
const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB rotate threshold
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
  let input = {};
  try {
    input = JSON.parse(raw);
  } catch {
    // Invalid JSON — log error and exit normally
    console.error('[SubagentStop] Invalid stdin JSON');
    process.exit(0);
  }

  // --- 1. Record observation to observations.jsonl ---
  try {
    let sessionId = process.env.CLAUDE_SESSION_ID || '';
    if (!sessionId) {
      try {
        const sstFile = path.join(__dirname, '.session-start-time');
        const epoch = fs.readFileSync(sstFile, 'utf8').trim();
        sessionId = 's' + epoch;
      } catch {
        sessionId = 'unknown';
      }
    }

    const reason = (input.reason || 'unknown').substring(0, 100);
    const agentId = (input.agent_id || 'unknown').substring(0, 200);
    const transcriptPath = (input.agent_transcript_path || '').substring(0, 500);
    const lastMessage = (input.last_assistant_message || '').substring(0, 200);

    const observation = {
      ts: new Date().toISOString(),
      sid: sessionId.substring(0, 12),
      tool: 'SubagentStop',
      params: {
        reason: reason,
        agent_id: agentId,
        transcript: transcriptPath,
        last_msg: lastMessage,
      },
    };

    // Ensure data dir exists
    if (!fs.existsSync(DATA_DIR)) {
      fs.mkdirSync(DATA_DIR, { recursive: true });
    }

    // Atomic rotate + append (same pattern as observation-logger.js)
    const line = JSON.stringify(observation) + '\n';
    let fd;
    try {
      fd = fs.openSync(OBS_FILE, 'a');
      const stats = fs.fstatSync(fd);
      if (stats.size > MAX_FILE_SIZE) {
        fs.closeSync(fd);
        fd = null;
        const archive = OBS_FILE.replace('.jsonl', `.${Date.now()}.jsonl`);
        try {
          fs.renameSync(OBS_FILE, archive);
        } catch { /* another process may have rotated already */ }
        fd = fs.openSync(OBS_FILE, 'a');
      }
      fs.writeSync(fd, line);
    } catch { /* file I/O error — silently ignore */ }
    finally {
      if (fd !== undefined && fd !== null) {
        try { fs.closeSync(fd); } catch { /* ignore */ }
      }
    }
  } catch {
    // Observation logging failure — silently ignore
  }

  // --- 2. Delegate to growth_recorder.py subagent_stop ---
  try {
    const growthScript = path.join(__dirname, 'growth_recorder.py');
    execFileSync('python', [growthScript, 'subagent_stop'], {
      input: raw,
      timeout: 5000,
      stdio: ['pipe', 'ignore', 'pipe'],
      env: Object.assign({}, process.env, {
        PYTHONIOENCODING: 'utf-8',
      }),
    });
  } catch (err) {
    // Fail-open: growth recording failure never blocks
    console.error('[SubagentStop] Growth recording skipped: ' +
      (err.stderr ? err.stderr.toString().trim() : err.message));
  }

  // --- 3. Auto cycle_complete on reviewer APPROVE ---
  // Detect reviewer agent completion and auto-trigger cycle_complete
  // if .dev-flow-state shows impl + reviewer done with no pending issues.
  try {
    const agentId = (input.agent_id || '').toLowerCase();
    const lastMsg = (input.last_assistant_message || '').toLowerCase();
    const reason = (input.reason || '');

    // Detect reviewer: agent_id contains 'reviewer' or last message contains review keywords
    const isReviewer = agentId.includes('reviewer') ||
                       lastMsg.includes('レビュー') ||
                       lastMsg.includes('review') ||
                       lastMsg.includes('approve');

    if (isReviewer && reason === 'end_turn') {
      const autoCycleScript = path.join(__dirname, 'auto_cycle_complete.py');
      execFileSync('python', [autoCycleScript], {
        timeout: 10000,
        stdio: ['ignore', 'ignore', 'pipe'],
        env: Object.assign({}, process.env, {
          PYTHONIOENCODING: 'utf-8',
        }),
      });
    }
  } catch (err) {
    // Fail-open: auto cycle_complete failure never blocks
    console.error('[SubagentStop] Auto cycle_complete skipped: ' +
      (err.stderr ? err.stderr.toString().trim().substring(0, 200) : err.message));
  }

  // Always exit normally
  process.exit(0);
});
