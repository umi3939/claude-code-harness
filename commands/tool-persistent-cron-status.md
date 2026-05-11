---
description: persistent_cron_status MCPツールの使い方ガイド
---
# persistent_cron_status

## 用途
Check the status of the cron daemon and overall system.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- cronデーモンの稼働状態を確認する時
- ジョブ統計と最近の実行状況の把握

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- persistent_cron_list
- persistent_cron_logs

## 制約・注意点
- レート制限: 5分間に3回まで
- Hook guard: guard-cron-status（rate_limit）
