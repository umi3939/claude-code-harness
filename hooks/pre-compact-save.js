#!/usr/bin/env node
/**
 * PreCompact Hook: Save important context to STM before context compression.
 *
 * When Claude Code compacts context, intermediate reasoning and conversation
 * details are lost. This hook automatically saves a notification to stderr
 * reminding Claude to save important context before compression happens.
 *
 * This hook cannot directly call MCP tools, but it can remind Claude
 * to use stm_write before the compression.
 */

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

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
  const HOOKS_DIR = __dirname;

  // === Phase A: Evacuate session state BEFORE flag reset ===
  // C20-3: Save flow state, psyche state, STM summary to evacuation file.
  // Must run before flag reset so we capture pre-reset state.
  // Failure here must not block flag reset or compaction.
  try {
    const evacuatorPath = path.join(HOOKS_DIR, 'session_evacuator.py');
    if (fs.existsSync(evacuatorPath)) {
      execFileSync('python', [evacuatorPath], {
        timeout: 5000,  // 5s timeout (pre-impl analysis #1: Python startup 1-2s + processing)
        stdio: ['ignore', 'ignore', 'pipe'],  // ignore stdin/stdout, capture stderr for logging
        cwd: HOOKS_DIR,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      });
    }
  } catch (e) {
    // Evacuation failure is non-fatal — log and continue to flag reset
    console.error('[PreCompact] Session evacuation failed (non-fatal): ' + (e.message || 'unknown error'));
  }

  // === Flag reset — compaction is a mid-session event. ===
  // Flag files abolished (session-ready, session-start-done, memory-search-done,
  // docs-read-done, self-review-done, team-created) — all checks now use observations.jsonl.
  // Only .dev-flow-state and .session-end-done remain as state files.
  const FLAGS = [
    '.dev-flow-state',
    '.session-end-done',
  ];
  for (const flag of FLAGS) {
    try { fs.unlinkSync(path.join(HOOKS_DIR, flag)); } catch { /* ok if missing */ }
  }

  // Reset _dev_flow in behavior-guard state (compaction invalidates flow tracking)
  const STATE_FILE = path.join(HOOKS_DIR, '.behavior-guard-state.json');
  try {
    if (fs.existsSync(STATE_FILE)) {
      const raw = fs.readFileSync(STATE_FILE, 'utf8');
      let state;
      try {
        state = JSON.parse(raw);
      } catch (parseErr) {
        // Malformed JSON — remove corrupted state file entirely
        console.error('[PreCompact] behavior-guard-state.json malformed, removing: ' + parseErr.message);
        try { fs.unlinkSync(STATE_FILE); } catch { /* ignore */ }
        state = null;
      }
      if (state && typeof state === 'object' && state._dev_flow) {
        state._dev_flow = {};
        const tmp = STATE_FILE + '.tmp.' + process.pid;
        fs.writeFileSync(tmp, JSON.stringify(state));
        fs.renameSync(tmp, STATE_FILE);
      }
    }
  } catch (e) {
    console.error('[PreCompact] _dev_flow reset failed (non-fatal): ' + (e.message || 'unknown'));
  }

  // --- Inject compact-guide.md skill content ---
  try {
    const skillPath = path.join(HOOKS_DIR, '..', '.claude', 'commands', 'compact-guide.md');
    if (fs.existsSync(skillPath)) {
      const skillContent = fs.readFileSync(skillPath, 'utf8').substring(0, 200);
      console.error('[PreCompact] Skill: compact-guide.md loaded (' + skillContent.length + ' chars)');
    }
  } catch {
    // Skill file read failure is non-fatal
  }

  // Warn Claude that context is about to be compressed
  console.error('[PreCompact] Context compression is about to happen.');
  console.error('[PreCompact] State files reset: dev-flow-state, session-end-done');
  console.error('[PreCompact] Session readiness now uses observations.jsonl (no flag files)');
  console.error('[PreCompact] IMPORTANT: Before continuing, save any important unrecorded context to STM using stm_write.');
  console.error('[PreCompact] Check: Are there unrecorded lessons, decisions, or user feedback in this session?');

  // Always pass through
  process.stdout.write(raw);
  process.exit(0);
});
