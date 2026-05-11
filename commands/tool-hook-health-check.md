---
description: hook_health_check MCPツールの使い方ガイド
---
# hook_health_check

## 用途
Check health status of message event hooks (registered count, failures, auto-disabled).

## MCP function
mcp__self-observation__hook_health_check

## 引数
- `config_path` (string, optional): Path to message_hooks.json. Default: discord_data/message_hooks.json
- `log_path` (string, optional): Path to message_hook_log.jsonl. Default: discord_data/message_hook_log.jsonl

## 使用場面
- セッション開始時のhookヘルスチェック（SessionStart hook経由で自動実行）
- message event hookの状態を手動で確認したい時
- hookの連続失敗・自動無効化を検知したい時

## 使用すべきでない場面
- hook設定の変更（このツールはREAD-ONLY）
- hookの有効化/無効化操作

## 関連ツール
- sync_hooks_to_global

## 制約・注意点
- READ-ONLY（hookの状態を変更しない）
- パスにはパストラバーサル防止が適用される
- SessionStart hook経由の場合: exit 0固定（セッション開始をブロックしない）
