---
description: persistent_cron_notifications MCPツールの使い方ガイド
---
# persistent_cron_notifications

## 用途
Get pending notifications from cron job executions and mark them as consumed.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- セッション開始時に未読通知を確認する時
- オフライン中のジョブ実行結果の把握

## 使用すべきでない場面
- session_startが完了する前

## 関連ツール
- persistent_cron_status
- persistent_cron_logs

## 制約・注意点
- session_start完了後にのみ使用可能
- 取得した通知はconsumed（消費済み）になる
- Hook guard: guard-cron-notifications（requires_session）
