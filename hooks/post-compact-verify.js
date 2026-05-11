#!/usr/bin/env node
/**
 * G48: PostCompact Verify Hook
 *
 * PostCompactイベント後に退避データ(.session-evacuation.json)の
 * 存在・構造・鮮度を検証し、結果をobservations.jsonlに記録する。
 *
 * 制約:
 * - 常にexit(0) — ブロックしない
 * - MCPツールを呼ばない — ファイルI/Oのみ
 * - 退避ファイルは読み取り専用 — 変更しない
 * - 失敗時も例外的動作しない — 記録して正常終了
 */

const fs = require('fs');
const path = require('path');

// --- Constants ---
const MAX_STDIN = 1024 * 1024; // 1MB
const MAX_EVAC_SIZE = 512 * 1024; // 512KB
const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB observation file rotate threshold
const FRESHNESS_THRESHOLD_SEC = 300; // 5 minutes
const EVACUATION_FILENAME = '.session-evacuation.json';
const REQUIRED_FIELDS = ['evacuated_at', 'flow_state', 'psyche_state', 'stm_summary'];

// --- Directory resolution ---
// Support env var overrides for testing, fall back to __dirname-relative paths
function resolveHooksDir() {
  return process.env.HOOKS_DIR || __dirname;
}

function resolveDataDir() {
  return process.env.DATA_DIR || path.join(__dirname, '..', 'data');
}

// --- Session ID ---
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

// --- Verification Logic ---

function checkExists(evacPath) {
  try {
    return fs.existsSync(evacPath);
  } catch {
    return false;
  }
}

function checkStructure(data) {
  if (!data || typeof data !== 'object') return false;
  for (const field of REQUIRED_FIELDS) {
    if (!(field in data)) return false;
  }
  return true;
}

function checkFreshness(data) {
  if (!data || typeof data.evacuated_at !== 'number') return false;
  const now = Date.now() / 1000; // Unix timestamp in seconds
  const age = Math.abs(now - data.evacuated_at);
  return age <= FRESHNESS_THRESHOLD_SEC;
}

function readEvacuationFile(evacPath) {
  try {
    const stats = fs.statSync(evacPath);
    if (stats.size > MAX_EVAC_SIZE) {
      return { data: null, error: 'file_size_exceeded' };
    }
    const content = fs.readFileSync(evacPath, 'utf8');
    const data = JSON.parse(content);
    return { data, error: null };
  } catch (e) {
    return { data: null, error: e.message || 'read_error' };
  }
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
    // Write failure is non-fatal — still output to stderr
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
    const hooksDir = resolveHooksDir();
    const dataDir = resolveDataDir();
    const sessionId = getSessionId(hooksDir);

    const evacPath = path.join(hooksDir, EVACUATION_FILENAME);

    // 1. Existence check
    const exists = checkExists(evacPath);

    // 2. Structure check (only if file exists)
    let structure = false;
    let freshness = false;
    let evacData = null;
    let readError = null;

    if (exists) {
      const result = readEvacuationFile(evacPath);
      evacData = result.data;
      readError = result.error;
      structure = checkStructure(evacData);
      freshness = checkFreshness(evacData);
    }

    // 3. Build observation entry
    const observation = {
      ts: new Date().toISOString(),
      sid: sessionId,
      tool: 'PostCompactVerify',
      params: {
        checks: {
          exists,
          structure,
          freshness,
        },
      },
    };

    if (readError) {
      observation.params.error = readError;
    }

    // 4. Record to observations.jsonl
    writeObservation(dataDir, observation);

    // 5. stderr summary
    const allPassed = exists && structure && freshness;
    const status = allPassed ? 'PASS' : 'WARN';
    console.error(`[PostCompactVerify] ${status}: exists=${exists}, structure=${structure}, freshness=${freshness}`);

  } catch (e) {
    console.error('[PostCompactVerify] Error: ' + (e.message || 'unknown'));
  }

  // Always pass through stdin and exit 0
  process.stdout.write(raw);
  process.exit(0);
});
