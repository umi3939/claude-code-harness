---
description: persistent_cron_list MCPツールの使い方ガイド
---
# persistent_cron_list

## 用途
List all registered persistent cron jobs.

## 引数
- `include_disabled` (bool): If true, include disabled jobs (default: only enabled) [省略可, default=False]

## 使用場面
- 登録済みジョブの一覧を確認する時
- 有効/無効ジョブの把握

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- persistent_cron_get
- persistent_cron_add

## 制約・注意点
- レート制限: 5分間に3回まで
- Hook guard: guard-cron-list（rate_limit）
