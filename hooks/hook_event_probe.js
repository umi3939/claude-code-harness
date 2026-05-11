#!/usr/bin/env node
/**
 * G68 Phase 0: Hook Event Probe (Stop / SubagentStop stdin payload size)
 *
 * 目的:
 *   Stop hook stdin の `assistant_response` / `response` フィールドが
 *   (b) 説明必須判定可能なサイズで配信されるかを実機計測する。
 *
 * 動作:
 *   1. stdin から JSON 読み取り (MAX_STDIN = 512KB)
 *   2. assistant_response, response, transcript_path の有無 + 各長さ +
 *      タイムスタンプを `.hook-event-probe-log.jsonl` に append
 *   3. 必ず exit(0) で通過 (probe は blocking しない)
 *
 * Fail-open: 全エラーを吸収して exit(0)。永久ブロック防止。
 *
 * ログファイル: hooks/.hook-event-probe-log.jsonl
 */

const fs = require('fs');
const path = require('path');

const LOG_FILE = path.join(__dirname, '.hook-event-probe-log.jsonl');
const MAX_STDIN = 512 * 1024;
const MAX_LOG_SIZE = 5 * 1024 * 1024;

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
    let parsed = null;
    let parseOk = false;
    try {
      parsed = JSON.parse(raw);
      parseOk = true;
    } catch {
      parseOk = false;
    }

    const entry = {
      ts: new Date().toISOString(),
      raw_len: raw.length,
      parse_ok: parseOk,
    };

    if (parseOk && parsed && typeof parsed === 'object') {
      const ar = parsed.assistant_response;
      const r = parsed.response;
      const tp = parsed.transcript_path;
      entry.has_assistant_response = typeof ar === 'string';
      entry.assistant_response_len = typeof ar === 'string' ? ar.length : 0;
      entry.has_response = typeof r === 'string';
      entry.response_len = typeof r === 'string' ? r.length : 0;
      entry.has_transcript_path = typeof tp === 'string' && tp.length > 0;
      entry.hook_event_name = parsed.hook_event_name || '';
      entry.input_keys = Object.keys(parsed).sort();
    } else {
      entry.has_assistant_response = false;
      entry.assistant_response_len = 0;
      entry.has_response = false;
      entry.response_len = 0;
      entry.has_transcript_path = false;
      entry.hook_event_name = '';
      entry.input_keys = [];
    }

    // Rotate if oversized
    try {
      if (fs.existsSync(LOG_FILE)) {
        const stat = fs.statSync(LOG_FILE);
        if (stat.size > MAX_LOG_SIZE) {
          const bak = LOG_FILE + '.' + Date.now() + '.bak';
          try { fs.renameSync(LOG_FILE, bak); } catch { /* ignore */ }
        }
      }
    } catch { /* ignore */ }

    try {
      fs.appendFileSync(LOG_FILE, JSON.stringify(entry) + '\n');
    } catch { /* ignore write failure */ }
  } catch { /* swallow all */ }

  process.exit(0);
});

process.stdin.on('error', () => {
  process.exit(0);
});
