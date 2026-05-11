---
description: persistent_cron_get MCPツールの使い方ガイド
---
# persistent_cron_get

## 用途
Get detailed information about a specific job.

## 引数
- `job_id` (str): The job's unique identifier [必須]

## 使用場面
- 特定ジョブの詳細設定を確認する時
- ジョブのスケジュール・状態・エラー履歴の確認

## 使用すべきでない場面
- job_idが不明な状態での呼び出し

## 関連ツール
- persistent_cron_update
- persistent_cron_remove
- persistent_cron_run

## 制約・注意点
- 必須引数: job_id
- Hook guard: guard-cron-get（required_args）
