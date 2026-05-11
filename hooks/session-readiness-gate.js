#!/usr/bin/env node
/**
 * Session Readiness Gate - PreToolUse (Edit|Write|Agent)
 *
 * セッション最初の作業ツール呼び出し時に、セッション準備が完了しているか一括チェック。
 * 不足項目があればstderrに警告し、exit(2)でブロック。
 *
 * チェック項目:
 * - README.md, SYSTEM_ARCHITECTURE.md, lessons_registry.md を読んだか
 * - session_start を実行したか
 * - stm_write（行動計画）を実行したか
 */

const fs = require('fs');
const path = require('path');

const HOOKS_DIR = __dirname;
const DATA_DIR = path.join(__dirname, '..', 'data');
const OBS_FILE = path.join(DATA_DIR, 'observations.jsonl');
const STATE_FILE = path.join(HOOKS_DIR, '.behavior-guard-state.json');

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
    // If already marked ready in state, skip (in-memory cache replacement for flag file)
    try {
      if (fs.existsSync(STATE_FILE)) {
        const stateData = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
        if (stateData.session_ready) {
          process.exit(0);
        }
      }
    } catch { /* ignore parse errors */ }

    // Read session start time
    const START_TIME_FILE = path.join(HOOKS_DIR, '.session-start-time');
    let sessionStartTime = 0;
    try {
      if (fs.existsSync(START_TIME_FILE)) {
        sessionStartTime = parseInt(fs.readFileSync(START_TIME_FILE, 'utf8').trim(), 10) || 0;
      }
    } catch { /* ignore */ }

    // Read recent observations, filter to current session only
    // Allow 60s margin before sessionStartTime to capture session_start's own PostToolUse observation
    const allObs = readRecentObservations(200);
    const sessionFilterTime = sessionStartTime > 0 ? sessionStartTime - 300000 : 0;
    const observations = sessionFilterTime > 0
      ? allObs.filter(o => new Date(o.ts).getTime() >= sessionFilterTime)
      : allObs;

    const missing = [];

    // Check: required files read (only check files that exist in the project)
    const possibleFiles = ['README.md', 'SYSTEM_ARCHITECTURE.md', 'lessons_registry.md', 'CLAUDE_OPERATIONS.md'];
    const readFiles = observations
      .filter(o => o.tool === 'Read')
      .map(o => (o.params && o.params.file) || '')
      .map(f => path.basename(f));

    // Find project root by checking common locations
    const cwd = process.cwd();
    for (const req of possibleFiles) {
      // Check if file exists in cwd or common locations
      const existsInCwd = fs.existsSync(path.join(cwd, req));
      const existsInMemory = req === 'lessons_registry.md'; // Always required (in memory dir)
      const shouldCheck = existsInCwd || existsInMemory;

      if (shouldCheck && !readFiles.some(f => f === req)) {
        missing.push(`Read ${req}`);
      }
    }

    // Check: session_start executed (observations only — flag files abolished)
    const hasSessionStart = observations.some(
      o => o.tool === 'mcp__memory-tools__session_start'
    );
    if (!hasSessionStart) {
      missing.push('session_start (MCP)');
    }

    // Check: memory_search executed (observations only — flag files abolished)
    const hasMemorySearch = observations.some(
      o => o.tool === 'mcp__memory-tools__memory_search'
    );
    if (!hasMemorySearch) {
      missing.push('memory_search (関連記憶検索 — 行動前に教訓・記憶を検索せよ)');
    }

    // Check: stm_write executed (行動計画 with 適用教訓リスト)
    const hasStmWrite = observations.some(
      o => o.tool === 'mcp__memory-tools__stm_write'
    );
    if (!hasStmWrite) {
      missing.push('stm_write (行動計画+適用教訓リスト)');
    }


    if (missing.length > 0) {
      console.error('[SessionReadinessGate] BLOCKED: セッション準備が不完全です。不足項目:');
      for (const item of missing) {
        console.error(`  - ${item}`);
      }
      console.error('[SessionReadinessGate] 先にセッション開始手順を完了してください（CLAUDE.md参照）');
      process.exit(2);
    } else {
      // All checks passed — mark ready in behavior-guard state (replaces flag file)
      try {
        let stateData = {};
        if (fs.existsSync(STATE_FILE)) {
          stateData = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
        }
        stateData.session_ready = true;
        const tmp = STATE_FILE + '.tmp.' + process.pid;
        fs.writeFileSync(tmp, JSON.stringify(stateData));
        fs.renameSync(tmp, STATE_FILE);
      } catch { /* ignore */ }
    }
  } catch (err) {
    // Never block
    console.error(`[SessionReadinessGate] Error: ${err.message}`);
  }

  process.exit(0);
});

function readRecentObservations(lineCount) {
  const observations = [];
  try {
    if (!fs.existsSync(OBS_FILE)) return observations;

    const content = fs.readFileSync(OBS_FILE, 'utf8');
    const lines = content.trim().split('\n');
    const recentLines = lines.slice(-lineCount);

    for (const line of recentLines) {
      try {
        observations.push(JSON.parse(line));
      } catch { /* skip malformed lines */ }
    }
  } catch { /* file read error */ }
  return observations;
}
