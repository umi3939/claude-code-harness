/**
 * Session initialization logic for SessionStart hook.
 *
 * Extracted from settings.json node -e one-liner (P3 1-6).
 * Handles:
 *   1. Cleanup of session flag files
 *   2. Writing session start timestamp
 *   3. Resetting behavior-guard-state.json
 *   4. Copying .mcp.json from home to cwd if missing
 *
 * Usage (settings.json):
 *   node hooks/session-init.js
 *
 * Testable export:
 *   runSessionInit(hooksDir, cwdDir, mcpSourcePath?)
 */

'use strict';

const fs = require('fs');
const path = require('path');

const FLAG_FILES = [
  '.session-start-done',
  '.memory-search-done',
  '.lesson-feedback-state.json',
  '.team-created',
  '.dev-flow-state',
  '.session-end-done',
  '.last-ar-phase',
];

/**
 * Run session initialization.
 *
 * @param {string} hooksDir - Path to the hooks directory (flag files live here)
 * @param {string} cwdDir - Current working directory (for .mcp.json copy target)
 * @param {string} [mcpSourcePath] - Path to source .mcp.json (defaults to ~/.claude/.mcp.json)
 */
function runSessionInit(hooksDir, cwdDir, mcpSourcePath) {
  // 1. Delete flag files (ignore if not found)
  for (const f of FLAG_FILES) {
    try { fs.unlinkSync(path.join(hooksDir, f)); } catch {}
  }

  // 2. Write session start timestamp
  fs.writeFileSync(
    path.join(hooksDir, '.session-start-time'),
    String(Date.now())
  );

  // 3. Reset behavior-guard-state.json (keep only specified keys)
  try {
    const guardPath = path.join(hooksDir, '.behavior-guard-state.json');
    if (fs.existsSync(guardPath)) {
      const state = JSON.parse(fs.readFileSync(guardPath, 'utf8'));
      const keep = []; // no keys preserved on session start
      const cleaned = {};
      for (const k of keep) {
        if (k in state) cleaned[k] = state[k];
      }
      fs.writeFileSync(guardPath, JSON.stringify(cleaned));
    }
  } catch {}

  // 4. Copy .mcp.json from source to cwd if dest doesn't exist
  const src = mcpSourcePath || path.join(
    process.env.USERPROFILE || process.env.HOME || '',
    '.claude',
    '.mcp.json'
  );
  const dst = path.join(cwdDir, '.mcp.json');
  if (!fs.existsSync(dst) && fs.existsSync(src)) {
    try { fs.copyFileSync(src, dst); } catch {}
  }
}

// When run directly (from settings.json hook), execute with defaults
if (require.main === module) {
  const hooksDir = path.join(
    process.env.USERPROFILE || process.env.HOME || '',
    '.claude',
    'hooks'
  );
  const cwdDir = process.cwd();
  runSessionInit(hooksDir, cwdDir);
}

module.exports = { runSessionInit };
