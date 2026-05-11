#!/usr/bin/env node
/**
 * Stop Hook: "本当に？" Self-Check
 *
 * 作業や思考の区切りを検出し、「本当に？」と自問を促す。
 * 発火条件: 応答テキストに作業完了シグナルが含まれる場合
 *
 * 完了シグナル:
 *   - "完了" / "完了しました" / "完了です"
 *   - "進みます" / "進めます"
 *   - "コミット" (作業成果の確定)
 *   - "shutdown_request" (Agent Teamsメンバー終了)
 *   - "次は" / "次のステップ" / "次のPhase"
 *
 * 発火抑制:
 *   クールダウンなし — 毎回立ち止まるのが目的
 */

const fs = require('fs');
const path = require('path');

const HOOKS_DIR = __dirname;
const MAX_STDIN = 512 * 1024;
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

    // Stop hook receives: { assistant_response: "..." }
    const response = (input.assistant_response || input.response || '').toString();
    if (!response) {
      process.exit(0);
    }

    // Detect completion signals (Japanese + English)
    const completionPatterns = [
      /完了/,
      /進みます/,
      /進めます/,
      /コミットし/,
      /committed/i,
      /shutdown_request/,
      /次は[^？]/, // "次は" but not "次は？" (question)
      /次のステップ/,
      /次のPhase/i,
      /フロー完了/,
      /全パス/,
      /all.*pass/i,
      /修正済み/,
      /fixed/i,
      /done\b/i,
      /GREEN/,
      /実装完了/,
      /テスト全パス/,
      /コミット完了/,
    ];

    const hasCompletion = completionPatterns.some(p => p.test(response));
    if (!hasCompletion) {
      process.exit(0);
    }

    // Fire the reminder with specific questions
    console.error('[本当に？] 作業の区切りを検出。立ち止まって自問:');
    console.error('  - 今やったことは本当に正しいか？テストで証明されているか？');
    console.error('  - TDDで進めたか？テストを先に書いたか？');
    console.error('  - memory_searchで関連記憶を検索してから行動したか？');
    console.error('  - 教訓を参照したか？同じ失敗を繰り返していないか？');
    console.error('  - ドキュメントの更新は済んだか？数値は正しいか？');
    console.error('  - 記録すべき気づき・反省はないか？');

  } catch {
    // Parse errors: ignore
  }

  process.exit(0);
});
