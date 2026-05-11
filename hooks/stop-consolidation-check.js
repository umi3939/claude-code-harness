#!/usr/bin/env node
/**
 * Stop Hook: Check if new lessons were added during this session.
 *
 * Runs after each Claude response. Checks the lessons_registry.md
 * modification time against a marker file to detect new lessons.
 * If new lessons exist, reminds Claude to run memory_consolidate.
 */

const fs = require('fs');
const path = require('path');

const MEMORY_DIR = path.join(
  process.env.USERPROFILE || process.env.HOME || '',
  '.claude', 'projects', 'default', 'memory'
);
const LESSONS_FILE = path.join(MEMORY_DIR, 'lessons_registry.md');
const MARKER_FILE = path.join(MEMORY_DIR, '.consolidation_check_marker');

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
  try {
    // Check if lessons file exists and has been modified
    if (fs.existsSync(LESSONS_FILE)) {
      const lessonsStat = fs.statSync(LESSONS_FILE);
      const lessonsModified = lessonsStat.mtimeMs;

      let lastChecked = 0;
      if (fs.existsSync(MARKER_FILE)) {
        const markerContent = fs.readFileSync(MARKER_FILE, 'utf8').trim();
        lastChecked = parseFloat(markerContent) || 0;
      }

      if (lessonsModified > lastChecked) {
        // Lessons were modified since last check
        // Read lesson count from consolidated_principles.md
        const principlesFile = path.join(MEMORY_DIR, 'consolidated_principles.md');
        let consolidatedCount = 0;
        if (fs.existsSync(principlesFile)) {
          const content = fs.readFileSync(principlesFile, 'utf8');
          const match = content.match(/lesson_count:\s*(\d+)/);
          if (match) consolidatedCount = parseInt(match[1], 10);
        }

        // Count actual lessons
        const lessonsContent = fs.readFileSync(LESSONS_FILE, 'utf8');
        const lessonCount = (lessonsContent.match(/^## Lesson:/gm) || []).length;

        if (lessonCount > consolidatedCount) {
          console.error(`[Stop] New lessons detected: ${lessonCount} total, ${consolidatedCount} consolidated.`);

          // Auto-run memory_consolidate(mode='check') instead of just suggesting
          try {
            const { execFileSync } = require('child_process');
            const autoScript = path.join(__dirname, 'stop_consolidation_auto.py');
            execFileSync('python', [autoScript], {
              timeout: 8000,
              stdio: ['ignore', 'ignore', 'pipe'],
              env: Object.assign({}, process.env, {
                PYTHONIOENCODING: 'utf-8',
              }),
            });
          } catch (autoErr) {
            // fail-open: auto consolidation is best-effort
            console.error('[Stop] Auto consolidation skipped: ' +
              (autoErr.stderr ? autoErr.stderr.toString().trim().substring(0, 200) : autoErr.message));
          }
        }

        // Update marker (atomic write)
        const tmp = MARKER_FILE + '.tmp.' + process.pid;
        fs.writeFileSync(tmp, String(Date.now()));
        fs.renameSync(tmp, MARKER_FILE);
      }
    }
  } catch (err) {
    // Never fail — just log
    console.error(`[Stop] Consolidation check error: ${err.message}`);
  }

  process.stdout.write(raw);
  process.exit(0);
});
