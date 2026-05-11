---
description: persistent_cron_run MCPツールの使い方ガイド
---
# persistent_cron_run

## 用途
Manually trigger a job execution immediately.

## 引数
- `job_id` (str): The job's unique identifier [必須]
- `async_mode` (bool): If true, run in background thread and return immediately. [省略可, default=False]

## 使用場面
- ジョブを即時手動実行する時
- スケジュール・稼働時間・バックオフをバイパスした即時実行

## 使用すべきでない場面
- 無効化されたジョブの即時実行
- job_idが不明な状態での呼び出し

## 関連ツール
- persistent_cron_get
- persistent_cron_logs

## 制約・注意点
- 必須引数: job_id
- スケジュール・稼働時間・バックオフをバイパス
- Hook guard: guard-cron-run（required_args）
