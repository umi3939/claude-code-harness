#!/usr/bin/env node
/**
 * G49: PostToolUseFailure Logger Hook
 *
 * ツール実行失敗をobservations.jsonlに記録する。
 * 既存のobservation-logger.js(PostToolUse)と対になる失敗記録hook。
 *
 * 制約:
 * - 常にexit(0) — ブロックしない
 * - MCPツールを呼ばない — ファイルI/Oのみ
 * - 失敗への対処・リトライしない — 記録のみ
 * - 全失敗を等価に記録 — 重要度判定しない
 */

const fs = require('fs');
const path = require('path');

// --- Constants ---
const MAX_STDIN = 1024 * 1024; // 1MB
const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB observation file rotate threshold
const MAX_ERROR_LENGTH = 500; // Error message truncation limit

// --- Directory resolution ---
function resolveHooksDir() {
  return process.env.HOOKS_DIR || __dirname;
}

function resolveDataDir() {
  return process.env.DATA_DIR || path.join(__dirname, '..', 'data');
}

// --- Session ID (same logic as observation-logger.js) ---
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

// --- Key parameter extraction (duplicated from observation-logger.js per design) ---
function extractKeyParams(toolName, input) {
  switch (toolName) {
    case 'Read':
    case 'Write':
    case 'Edit':
      return { file: input.file_path || '' };
    case 'Bash': {
      const cmd = (input.command || '').substring(0, 50);
      return { cmd };
    }
    case 'Grep':
      return { pattern: (input.pattern || '').substring(0, 30), path: input.path || '' };
    case 'Glob':
      return { pattern: input.pattern || '' };
    case 'Agent':
      return { desc: input.description || '', type: input.subagent_type || '' };
    default:
      // MCP tools — record key params (truncated for safety)
      if (toolName.startsWith('mcp__')) {
        const params = {};
        for (const [k, v] of Object.entries(input)) {
          if (typeof v === 'string') {
            params[k] = v.substring(0, 80);
          } else if (typeof v === 'number' || typeof v === 'boolean') {
            params[k] = v;
          }
        }
        return params;
      }
      return {};
  }
}

// --- Error message extraction ---
function extractError(toolResponse) {
  if (!toolResponse) return '';
  let errorMsg = '';
  if (typeof toolResponse === 'string') {
    errorMsg = toolResponse;
  } else if (typeof toolResponse.content === 'string') {
    errorMsg = toolResponse.content;
  } else if (Array.isArray(toolResponse.content)) {
    errorMsg = toolResponse.content
      .filter(b => b && b.type === 'text' && typeof b.text === 'string')
      .map(b => b.text)
      .join('\n');
  } else if (typeof toolResponse.error === 'string') {
    errorMsg = toolResponse.error;
  } else if (typeof toolResponse.message === 'string') {
    errorMsg = toolResponse.message;
  }
  return errorMsg.substring(0, MAX_ERROR_LENGTH);
}

// --- Observation Recording (same pattern as observation-logger.js) ---
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

    const toolName = input.tool_name || 'unknown';
    const toolInput = input.tool_input || {};
    const toolResponse = input.tool_response || {};

    const keyParams = extractKeyParams(toolName, toolInput);
    const errorMsg = extractError(toolResponse);

    const observation = {
      ts: new Date().toISOString(),
      sid: sessionId,
      tool: toolName,
      params: keyParams,
      status: 'failure',
      error: errorMsg,
    };

    writeObservation(dataDir, observation);

  } catch {
    // JSON parse or other errors — silently ignore
  }

  // Always pass through stdin and exit 0
  process.stdout.write(raw);
  process.exit(0);
});
