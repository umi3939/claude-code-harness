#!/usr/bin/env node
/**
 * Strategic Compact Suggester
 *
 * Runs on PreToolUse (Edit|Write) to suggest manual /compact at logical intervals.
 * Auto-compact happens at arbitrary points, often mid-task.
 * Strategic compacting preserves context through logical phases.
 *
 * Based on ECC's suggest-compact pattern.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

function main() {
  const tmpDir = os.tmpdir();
  const sessionId = (process.env.CLAUDE_SESSION_ID || 'default').replace(/[^a-zA-Z0-9_-]/g, '') || 'default';
  const counterFile = path.join(tmpDir, `claude-tool-count-${sessionId}`);
  const threshold = parseInt(process.env.COMPACT_THRESHOLD || '50', 10) || 50;

  let count = 1;

  try {
    const fd = fs.openSync(counterFile, 'a+');
    try {
      const buf = Buffer.alloc(64);
      const bytesRead = fs.readSync(fd, buf, 0, 64, 0);
      if (bytesRead > 0) {
        const parsed = parseInt(buf.toString('utf8', 0, bytesRead).trim(), 10);
        count = (Number.isFinite(parsed) && parsed > 0 && parsed <= 1000000)
          ? parsed + 1
          : 1;
      }
      fs.ftruncateSync(fd, 0);
      fs.writeSync(fd, String(count), 0);
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    try { const t = counterFile + '.tmp.' + process.pid; fs.writeFileSync(t, String(count)); fs.renameSync(t, counterFile); } catch {}
  }

  if (count === threshold) {
    console.error(`[SuggestCompact] ${threshold}回のツール呼び出しに到達。フェーズの切り替わりなら /compact を検討してください`);
  }

  if (count > threshold && (count - threshold) % 25 === 0) {
    console.error(`[SuggestCompact] ${count}回目 — コンテキストが重いなら /compact のタイミングかもしれません`);
  }

  process.exit(0);
}

main();
