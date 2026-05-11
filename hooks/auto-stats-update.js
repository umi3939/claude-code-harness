#!/usr/bin/env node
/**
 * PostToolUse Hook: Auto-update infrastructure stats in docs.
 *
 * Triggers:
 * - Infra files (.mcp.json, behavior-rules.json, agents/*.md, commands/*.md)
 *   → immediate execution
 * - Source code files (.py, .js, .md)
 *   → 5-minute cooldown between executions
 *
 * Exclusion list prevents circular triggers from stats_updater.py output files.
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const HOOKS_DIR = __dirname;
const COOLDOWN_FILE = path.join(HOOKS_DIR, '.auto-stats-last-run');
const COOLDOWN_MS = 5 * 60 * 1000; // 5 minutes

// Files that stats_updater.py writes to — exclude to prevent circular triggers
const EXCLUSION_LIST = [
  'MEMORY.md',
  'mcp-tools.md',
];

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
    const toolName = input.tool_name || '';
    const toolInput = input.tool_input || {};

    // Only trigger on Edit/Write
    if (toolName !== 'Edit' && toolName !== 'Write') {
      process.stdout.write(raw);
      process.exit(0);
      return;
    }

    const filePath = toolInput.file_path || '';
    const basename = path.basename(filePath);
    const dirName = path.basename(path.dirname(filePath));

    // Check exclusion list to prevent circular triggers
    if (EXCLUSION_LIST.includes(basename)) {
      process.stdout.write(raw);
      process.exit(0);
      return;
    }

    // Infra triggers — immediate execution (no cooldown)
    const infraTriggers = [
      basename === '.mcp.json',
      basename === 'behavior-rules.json',
      dirName === 'agents' && basename.endsWith('.md'),
      dirName === 'commands' && basename.endsWith('.md'),
    ];

    // Source code triggers — with 5-minute cooldown
    const sourceCodeTriggers = [
      basename.endsWith('.py'),
      basename.endsWith('.js'),
      basename.endsWith('.md'),
    ];

    const isInfraTrigger = infraTriggers.some(Boolean);
    const isSourceTrigger = !isInfraTrigger && sourceCodeTriggers.some(Boolean);

    let shouldRun = false;

    if (isInfraTrigger) {
      shouldRun = true;
    } else if (isSourceTrigger) {
      // Check cooldown
      shouldRun = !isCooldownActive();
    }

    if (shouldRun) {
      try {
        const statsScript = path.join(
          process.env.USERPROFILE || process.env.HOME || '',
          '.claude', 'tools', 'stats_updater.py'
        );
        execSync(`python "${statsScript}" --update`, {
          timeout: 10000,
          stdio: ['pipe', 'pipe', 'pipe'],
        });
        // Update cooldown timestamp (atomic write)
        try {
          const tmp = COOLDOWN_FILE + '.tmp.' + process.pid;
          fs.writeFileSync(tmp, String(Date.now()));
          fs.renameSync(tmp, COOLDOWN_FILE);
        } catch { /* ignore */ }
        const triggerType = isInfraTrigger ? 'infra' : 'source';
        console.error('[AutoStats] Updated doc counts after ' + basename + ' change (' + triggerType + ')');
      } catch (e) {
        console.error('[AutoStats] Failed to update: ' + (e.message || e).substring(0, 100));
      }
    }
  } catch {
    // Parse errors: pass through
  }

  process.stdout.write(raw);
  process.exit(0);
});

/**
 * Check if cooldown is active (within COOLDOWN_MS of last run).
 * If file is missing or corrupted, returns false (safe side: allow execution).
 */
function isCooldownActive() {
  try {
    if (!fs.existsSync(COOLDOWN_FILE)) return false;
    const lastRun = parseInt(fs.readFileSync(COOLDOWN_FILE, 'utf8').trim(), 10);
    if (isNaN(lastRun)) return false;
    return (Date.now() - lastRun) < COOLDOWN_MS;
  } catch {
    return false;
  }
}
