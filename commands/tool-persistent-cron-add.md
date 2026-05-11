---
description: persistent_cron_add MCPツールの使い方ガイド
---
# persistent_cron_add

## 用途
Register a new persistent cron job.

## 引数
- `name` (str): Human-readable job name [必須]
- `prompt` (str): The prompt string to execute via Claude CLI. [必須]
- `schedule_type` (str):  [省略可, default=]
- `schedule_value` (str):  [省略可, default=]
- `description` (str):  [省略可, default=]
- `cwd` (str):  [省略可, default=]
- `one_shot` (bool):  [省略可, default=False]
- `ttl` (str):  [省略可, default=]
- `active_hours_start` (str):  [省略可, default=]
- `active_hours_end` (str):  [省略可, default=]
- `timeout_seconds` (int):  [省略可, default=300]
- `job_type` (str):  [省略可, default=standard]
- `permission_mode` (str):  [省略可, default=bypassPermissions]
- `at` (str):  [省略可, default=]
- `delete_after_run` (bool):  [省略可, default=False]

## 使用場面
- 新しい定期実行ジョブを登録する時
- at（相対時間）/every（間隔）/cron（cron式）の3種類のスケジュール設定

## 使用すべきでない場面
- promptが空の状態での登録
- schedule_typeが不明な状態での登録

## 関連ツール
- persistent_cron_list
- persistent_cron_get
- persistent_cron_status

## 制約・注意点
- 必須引数: name, prompt
- schedule_typeまたはatのいずれかが必要
- Hook guard: guard-cron-add（required_args）
