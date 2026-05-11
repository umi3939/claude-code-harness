#!/usr/bin/env node
/**
 * Lesson After Feedback - Stop hook (毎応答後)
 *
 * feedback/errorタイプのmemory_recordがあるのに
 * lessons_registryへの追加がない場合に警告する。
 *
 * 教訓#: 反省・失敗・気づきがあったら3層全てに書く。
 * STMだけ書いて教訓を省略するのは「記録したフリ」で禁止。
 */

const fs = require('fs');
const path = require('path');

const HOOKS_DIR = __dirname;
const DATA_DIR = path.join(__dirname, '..', 'data');
const OBS_FILE = path.join(DATA_DIR, 'observations.jsonl');
const STATE_FILE = path.join(HOOKS_DIR, '.lesson-feedback-state.json');

const MAX_STDIN = 1024 * 1024;
let raw = '';

process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => {
  if (raw.length < MAX_STDIN) {
    raw += chunk.substring(0, MAX_STDIN - raw.length);
  }
});

process.stdin.on('end', () => {
  let hasLessonsCall = false;

  try {
    // Get session start time for filtering
    const sessionStartTime = getSessionStartTime();

    // Read recent observations (last 50 lines), filtered to current session
    const observations = readRecentObservations(50, sessionStartTime);

    // Load state (previously warned timestamps)
    let warnedTimestamps = [];
    try {
      if (fs.existsSync(STATE_FILE)) {
        const stateContent = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
        warnedTimestamps = stateContent.warned || [];
      }
    } catch { warnedTimestamps = []; }

    // Find memory_record calls with episode_type=feedback or episode_type=error only
    const feedbackRecords = observations.filter(o => {
      if (o.tool !== 'mcp__memory-tools__memory_record') return false;
      const episodeType = (o.params && o.params.episode_type) || '';
      return episodeType === 'feedback' || episodeType === 'error';
    });

    if (feedbackRecords.length === 0) {
      // No memory_record calls at all — nothing to check
      process.exit(0);
    }

    // Check if there's a lessons_registry call (Bash command containing 'lessons_registry')
    hasLessonsCall = observations.some(o => {
      if (o.tool !== 'Bash') return false;
      const cmd = (o.params && o.params.cmd) || '';
      return cmd.includes('lessons_registry.py add') || cmd.includes('lessons_registry add');
    });

    // Find un-warned feedback records
    const newFeedbackTimestamps = feedbackRecords
      .map(o => o.ts)
      .filter(ts => !warnedTimestamps.includes(ts));

    if (newFeedbackTimestamps.length > 0 && !hasLessonsCall) {
      console.error('[LessonAfterFeedback] WARNING: memory_recordでエピソードを記録しましたが、lessons_registryへの教訓追加が見つかりません');
      console.error('[LessonAfterFeedback] 反省・失敗・気づきがあったら3層全てに書く: STM + エピソード + 教訓');
      console.error('[LessonAfterFeedback] `python lessons_registry.py add` で教訓を記録してください');

      // Record warned timestamps
      warnedTimestamps = warnedTimestamps.concat(newFeedbackTimestamps);
      // Keep only last 100 entries
      if (warnedTimestamps.length > 100) {
        warnedTimestamps = warnedTimestamps.slice(-100);
      }
      try {
        const tmp = STATE_FILE + '.tmp.' + process.pid;
        fs.writeFileSync(tmp, JSON.stringify({ warned: warnedTimestamps }));
        fs.renameSync(tmp, STATE_FILE);
      } catch { /* ignore */ }
    }
  } catch (err) {
    console.error(`[LessonAfterFeedback] Error: ${err.message}`);
  }

  // Group 6: After lesson addition, validate + detect conflicts
  try {
    if (hasLessonsCall) {
      const { execFileSync } = require('child_process');
      const conflictScript = path.join(HOOKS_DIR, 'lesson_conflict_checker.py');

      try {
        execFileSync('python', [conflictScript], {
          timeout: 5000,
          stdio: ['ignore', 'pipe', 'pipe'],
          env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }),
        });
      } catch (err) {
        // fail-open: lesson conflict detection is best-effort
        if (err.stderr) {
          console.error('[LessonAfterFeedback] Conflict detection skipped: ' + err.stderr.toString().trim().substring(0, 200));
        }
      }
    }
  } catch (err) {
    console.error(`[LessonAfterFeedback] Group 6 chain error: ${err.message}`);
  }

  process.exit(0);
});

function getSessionStartTime() {
  const SESSION_START_FILE = path.join(HOOKS_DIR, '.session-start-time');
  try {
    if (fs.existsSync(SESSION_START_FILE)) {
      const epochMs = parseInt(fs.readFileSync(SESSION_START_FILE, 'utf8').trim(), 10);
      if (!isNaN(epochMs)) {
        return new Date(epochMs);
      }
    }
  } catch { /* ignore */ }
  return null;
}

function readRecentObservations(lineCount, sessionStartTime) {
  const observations = [];
  try {
    if (!fs.existsSync(OBS_FILE)) return observations;

    const content = fs.readFileSync(OBS_FILE, 'utf8');
    const lines = content.trim().split('\n');
    const recentLines = lines.slice(-lineCount);

    for (const line of recentLines) {
      try {
        const obs = JSON.parse(line);
        // Filter: only include observations from current session
        if (sessionStartTime && obs.ts) {
          const obsTime = new Date(obs.ts);
          if (obsTime < sessionStartTime) continue;
        }
        observations.push(obs);
      } catch { /* skip malformed lines */ }
    }
  } catch { /* file read error */ }
  return observations;
}
