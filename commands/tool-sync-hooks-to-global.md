---
description: sync_hooks_to_global MCPツールの使い方ガイド
---
# sync_hooks_to_global

## 用途
Sync project hook files to global ~/.claude/hooks/ directory.

## MCP function
mcp__self-observation__sync_hooks_to_global

## 引数
- `project_hooks_dir` (string, optional): Path to project hooks/ directory. Default: <project_root>/hooks/
- `global_hooks_dir` (string, optional): Path to global hooks directory. Default: ~/.claude/hooks/

## 使用場面
- セッション開始時のhook同期（SessionStart hook経由で自動実行）
- プロジェクトhookをグローバルに手動同期したい時

## 使用すべきでない場面
- グローバルhookからプロジェクトへの逆方向同期（一方向のみ）
- hook内容の編集

## 関連ツール
- hook_health_check

## 制約・注意点
- 一方向コピー: project hooks/ → global ~/.claude/hooks/
- 対象ファイル: behavior-guard.js, behavior-rules.json, skill_executor.py, coherence_alert.py, coherence_alert_runner.py
- temp-file + renameによる安全な書き込み
- パスにはパストラバーサル防止が適用される
- SessionStart hook経由の場合: exit 0固定（同期失敗でもセッション開始をブロックしない）
