---
description: persistent_cron_logs MCPツールの使い方ガイド
---
# persistent_cron_logs

## 用途
View execution logs for jobs.

## 引数
- `job_id` (str): Filter by job ID (empty = all jobs) [省略可, default=]
- `limit` (int): Max entries to return (default 20) [省略可, default=20]

## 使用場面
- ジョブの実行ログを確認する時
- 成功/失敗/スキップ状態の把握

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- persistent_cron_run
- persistent_cron_get

## 制約・注意点
- レート制限: 5分間に3回まで
- Hook guard: guard-cron-logs（rate_limit）
