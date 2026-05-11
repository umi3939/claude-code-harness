#!/usr/bin/env node
/**
 * Observation Logger - PostToolUse観測記録フック
 *
 * 全ツール呼び出しを observations.jsonl に自動記録。
 * 自分の行動パターンを後から分析するためのデータ収集層。
 *
 * 記録内容: timestamp, session_id, tool_name, key_params（ファイルパス等）
 * 機密情報（ファイル内容、コマンド出力）は記録しない。
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, '..', 'data');
const OBS_FILE = path.join(DATA_DIR, 'observations.jsonl');
const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB上限、超えたらrotate

const MAX_STDIN = 1024 * 1024;
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
    const toolName = input.tool_name || 'unknown';
    const toolInput = input.tool_input || {};
    const toolResponse = input.tool_response || {};
    // Try env var first, then .session-start-time file as fallback
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

    // Extract key params only (no content/output)
    const keyParams = extractKeyParams(toolName, toolInput);

    // For Bash, try to record failure info from tool_response
    if (toolName === 'Bash' && toolResponse) {
      // tool_response structure varies — safely try common fields
      const exitCode = toolResponse.exit_code ?? toolResponse.exitCode;
      if (exitCode !== undefined && exitCode !== 0) {
        keyParams.exit = exitCode;
      }
    }

    const observation = {
      ts: new Date().toISOString(),
      sid: sessionId.substring(0, 12),
      tool: toolName,
      params: keyParams
    };

    // Ensure data dir exists
    if (!fs.existsSync(DATA_DIR)) {
      fs.mkdirSync(DATA_DIR, { recursive: true });
    }

    // Atomic rotate + append: open file, check size, rotate if needed, write
    const line = JSON.stringify(observation) + '\n';
    let fd;
    try {
      fd = fs.openSync(OBS_FILE, 'a');
      const stats = fs.fstatSync(fd);
      if (stats.size > MAX_FILE_SIZE) {
        // Close before rename, then re-open new file
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
    // Errors: silently ignore
  }

  process.stdout.write(raw);
  process.exit(0);
});

function extractKeyParams(toolName, input) {
  switch (toolName) {
    case 'Read':
    case 'Write':
    case 'Edit':
      return { file: input.file_path || '' };
    case 'Bash':
      // コマンドの最初の50文字のみ（機密情報防止）
      const cmd = (input.command || '').substring(0, 50);
      return { cmd };
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
