#!/usr/bin/env node
/**
 * G50: TaskCreated/TaskCompleted Lifecycle Logger Hook
 *
 * タスクのライフサイクルイベント(TaskCreated/TaskCompleted)を
 * observations.jsonlに自動記録するインフラhook。
 *
 * 制約:
 * - 常にexit(0) — ブロックしない
 * - MCPツールを呼ばない — ファイルI/Oのみ
 * - 1ファイルで両イベントを処理
 * - hook_event_name(stdin) または CLAUDE_HOOK_EVENT(env)でイベント種別判定
 */

const fs = require('fs');
const path = require('path');

// --- Constants ---
const MAX_STDIN = 1024 * 1024; // 1MB
const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB observation file rotate threshold
const MAX_SUBJECT_LENGTH = 200;

// --- Directory resolution ---
function resolveHooksDir() {
  return process.env.HOOKS_DIR || __dirname;
}

function resolveDataDir() {
  return process.env.DATA_DIR || path.join(__dirname, '..', 'data');
}

// --- Session ID (same logic as tool-failure-logger.js) ---
function getSessionId(hooksDir) {
  let sessionId = process.env.CLAUDE_SESSION_ID || '';
  if (!sessionId) {
    try {
      const sstFile = path.join(hooksDir, '.session-start-time');
      const epoch = fs.readFileSync(sstFile, 'utf8').trim();
      sessionId = 's' + epoch;
    } catch {
      sessionId = 'unknown';
    }
  }
  return sessionId.substring(0, 12);
}

// --- Observation Recording (same pattern as tool-failure-logger.js) ---
function writeObservation(dataDir, observation) {
  const obsFile = path.join(dataDir, 'observations.jsonl');
  const line = JSON.stringify(observation) + '\n';

  try {
    if (!fs.existsSync(dataDir)) {
      fs.mkdirSync(dataDir, { recursive: true });
    }

    let fd;
    try {
      fd = fs.openSync(obsFile, 'a');
      const stats = fs.fstatSync(fd);
      if (stats.size > MAX_FILE_SIZE) {
        fs.closeSync(fd);
        fd = null;
        const archive = obsFile.replace('.jsonl', `.${Date.now()}.jsonl`);
        try {
          fs.renameSync(obsFile, archive);
        } catch { /* another process may have rotated already */ }
        fd = fs.openSync(obsFile, 'a');
      }
      fs.writeSync(fd, line);
    } finally {
      if (fd !== undefined && fd !== null) {
        try { fs.closeSync(fd); } catch { /* ignore */ }
      }
    }
  } catch {
    // Write failure is non-fatal
  }
}

// --- Main ---

let raw = '';

process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  if (raw.length < MAX_STDIN) {
    raw += chunk.substring(0, MAX_STDIN - raw.length);
  }
});

process.stdin.on('end', () => {
  try {
    const input = JSON.parse(raw);
    const hooksDir = resolveHooksDir();
    const dataDir = resolveDataDir();
    const sessionId = getSessionId(hooksDir);

    // Event type: stdin hook_event_name takes priority over env var
    const eventType = input.hook_event_name
      || process.env.CLAUDE_HOOK_EVENT
      || 'unknown';

    const taskId = input.task_id || '';
    const subject = (input.subject || '').substring(0, MAX_SUBJECT_LENGTH);

    const observation = {
      ts: new Date().toISOString(),
      sid: sessionId,
      tool: 'TaskLifecycle',
      event_type: eventType,
      params: {
        task_id: taskId,
        subject: subject,
      },
    };

    writeObservation(dataDir, observation);

  } catch {
    // JSON parse or other errors — silently ignore
  }

  // Always pass through stdin and exit 0
  process.stdout.write(raw);
  process.exit(0);
});
