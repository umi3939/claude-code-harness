---
description: discord_receive_pending MCPツールの使い方ガイド
---
# discord_receive_pending

## 用途
Check pending (unprocessed) messages in the receive buffer.

## 引数
- `limit` (int): Maximum number of entries to show (default: 20, max: 100). [省略可, default=20]

## 使用場面
- 未処理の受信メッセージを確認する時
- pending/failed状態のメッセージ一覧

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- discord_receive_status

## 制約・注意点
- レート制限: 5分間に3回まで
- limit上限100
- Hook guard: guard-discord-receive-pending（rate_limit）
