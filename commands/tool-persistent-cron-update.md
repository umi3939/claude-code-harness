---
description: persistent_cron_update MCPツールの使い方ガイド
---
# persistent_cron_update

## 用途
Update an existing job's configuration.

## 引数
- `job_id` (str): The job's unique identifier [必須]
- `name` (str): New job name [省略可, default=]
- `prompt` (str): New prompt string [省略可, default=]
- `enabled` (str): "true" or "false" to enable/disable [省略可, default=]
- `schedule_type` (str): New schedule type ("at", "every", "cron") [省略可, default=]
- `schedule_value` (str): New schedule value [省略可, default=]
- `cwd` (str): New working directory [省略可, default=]
- `ttl` (str): New TTL (ISO-8601), use "none" to clear [省略可, default=]
- `active_hours_start` (str): New active window start (HH:MM), use "none" to clear [省略可, default=]
- `active_hours_end` (str): New active window end (HH:MM), use "none" to clear [省略可, default=]
- `timeout_seconds` (int): New timeout (0 = don't change) [省略可, default=0]

## 使用場面
- 既存ジョブの設定を変更する時
- スケジュール変更・有効/無効切り替え

## 使用すべきでない場面
- job_idが不明な状態での呼び出し
- 更新フィールドが全て空の場合

## 関連ツール
- persistent_cron_get
- persistent_cron_list

## 制約・注意点
- 必須引数: job_id
- 指定フィールドのみ更新（部分更新）
- Hook guard: guard-cron-update（required_args）
