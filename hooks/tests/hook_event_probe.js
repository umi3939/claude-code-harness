#!/usr/bin/env node
/**
 * Hook Event Probe - 新hookイベント発火テスト用スクリプト
 *
 * 目的: settings.local.jsonに設定したhookイベントが
 * 実際にClaude Codeから発火されるかを検証する。
 *
 * 動作:
 *   1. stdinからhookイベントデータ（JSON）を読み取る
 *   2. 環境変数からイベント名等のメタデータを取得
 *   3. hooks/.hook-event-probe-log.jsonl にログを追記する
 *
 * 使い方:
 *   settings.local.jsonのhooksに対象イベントを追加し、
 *   このスクリプトをcommandとして指定する。
 *   イベントが発火するとログファイルにエントリが追記される。
 *
 * ログファイル: hooks/.hook-event-probe-log.jsonl
 * ログ上限: 1MB（超えたらrotate）
 */

const fs = require('fs');
const path = require('path');

const LOG_FILE = path.join(__dirname, '..', '.hook-event-probe-log.jsonl');
const MAX_LOG_SIZE = 1 * 1024 * 1024; // 1MB
const MAX_STDIN = 512 * 1024; // 512KB
const STDIN_TIMEOUT_MS = 3000;

/**
 * ログファイルのrotate（上限超えたら.bakに退避）
 */
function rotateLogIfNeeded() {
  try {
    if (fs.existsSync(LOG_FILE)) {
      const stat = fs.statSync(LOG_FILE);
      if (stat.size > MAX_LOG_SIZE) {
        const bakFile = LOG_FILE + '.bak';
        try { fs.unlinkSync(bakFile); } catch (_e) { /* ignore */ }
        fs.renameSync(LOG_FILE, bakFile);
      }
    }
  } catch (err) {
    // rotate失敗はログに記録しない（無限ループ防止）
    process.stderr.write('probe: rotate error: ' + err.message + '\n');
  }
}

/**
 * ログエントリを書き込む
 */
function writeLogEntry(entry) {
  rotateLogIfNeeded();
  try {
    fs.appendFileSync(LOG_FILE, JSON.stringify(entry) + '\n');
  } catch (err) {
    process.stderr.write('probe: write error: ' + err.message + '\n');
  }
}

/**
 * stdinからデータを読み取る（タイムアウト付き）
 */
let input = '';
let timedOut = false;

const timer = setTimeout(() => {
  timedOut = true;
  const entry = {
    timestamp: new Date().toISOString(),
    event: process.env.CLAUDE_HOOK_EVENT || 'unknown',
    session_id: process.env.CLAUDE_SESSION_ID || 'unknown',
    source: 'timeout',
    input_preview: input.length > 0 ? input.substring(0, 200) : 'NO_STDIN_DATA',
    env_keys: Object.keys(process.env).filter(k => k.startsWith('CLAUDE_')).sort()
  };
  writeLogEntry(entry);
  process.exit(0);
}, STDIN_TIMEOUT_MS);

process.stdin.setEncoding('utf8');

process.stdin.on('data', (chunk) => {
  if (input.length < MAX_STDIN) {
    input += chunk.substring(0, MAX_STDIN - input.length);
  }
});

process.stdin.on('end', () => {
  if (timedOut) return;
  clearTimeout(timer);

  let parsedKeys = [];
  let hookEventName = 'unknown';
  let toolName = '';
  try {
    const parsed = JSON.parse(input);
    parsedKeys = Object.keys(parsed).sort();
    hookEventName = parsed.hook_event_name || 'unknown';
    toolName = parsed.tool_name || '';
  } catch (_e) {
    // JSON parse失敗でもログは書く
  }

  const entry = {
    timestamp: new Date().toISOString(),
    event: process.env.CLAUDE_HOOK_EVENT || hookEventName,
    session_id: process.env.CLAUDE_SESSION_ID || 'unknown',
    source: 'stdin',
    hook_event_name: hookEventName,
    tool_name: toolName,
    input_length: input.length,
    input_keys: parsedKeys,
    input_preview: input.substring(0, 300),
    env_keys: Object.keys(process.env).filter(k => k.startsWith('CLAUDE_')).sort()
  };
  writeLogEntry(entry);
  process.exit(0);
});

process.stdin.on('error', (err) => {
  if (timedOut) return;
  clearTimeout(timer);

  const entry = {
    timestamp: new Date().toISOString(),
    event: process.env.CLAUDE_HOOK_EVENT || 'unknown',
    source: 'stdin_error',
    error: err.message,
    env_keys: Object.keys(process.env).filter(k => k.startsWith('CLAUDE_')).sort()
  };
  writeLogEntry(entry);
  process.exit(0);
});
