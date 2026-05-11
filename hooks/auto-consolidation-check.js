#!/usr/bin/env node
/**
 * PostToolUse Hook: Auto-check consolidation after lesson changes.
 *
 * When lessons_registry.md is modified, checks if new lessons
 * need to be consolidated into principles.
 */

const fs = require('fs');
const path = require('path');

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

    if (toolName !== 'Edit' && toolName !== 'Write') {
      process.stdout.write(raw);
      process.exit(0);
      return;
    }

    const filePath = toolInput.file_path || '';
    if (!path.basename(filePath).includes('lessons_registry')) {
      process.stdout.write(raw);
      process.exit(0);
      return;
    }

    // Check for consolidation marker
    const markerDir = path.dirname(filePath);
    const markerFile = path.join(markerDir, '.consolidation_check_marker');

    // Count lessons in the file
    let lessonCount = 0;
    try {
      const content = fs.readFileSync(filePath, 'utf8');
      const matches = content.match(/^## Lesson:/gm);
      lessonCount = matches ? matches.length : 0;
    } catch { /* ignore */ }

    // Check last consolidated count
    let lastConsolidated = 0;
    const principlesPath = path.join(markerDir, 'consolidated_principles.md');
    if (fs.existsSync(principlesPath)) {
      try {
        const content = fs.readFileSync(principlesPath, 'utf8');
        // Check frontmatter lesson_count first, then fallback to body text
        const fmMatch = content.match(/lesson_count:\s*(\d+)/);
        const bodyMatch = content.match(/Based on (\d+) lessons/);
        const match = fmMatch || bodyMatch;
        if (match) lastConsolidated = parseInt(match[1], 10);
      } catch { /* ignore */ }
    }

    const newLessons = lessonCount - lastConsolidated;
    if (newLessons > 0) {
      console.error(`[AutoConsolidate] ${newLessons} new lesson(s) since last consolidation (${lastConsolidated} → ${lessonCount}). Run memory_consolidate to update principles.`);
    }
  } catch {
    // Parse errors: pass through
  }

  process.stdout.write(raw);
  process.exit(0);
});
