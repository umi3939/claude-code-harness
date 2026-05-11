---
description: discord_status MCPツールの使い方ガイド
---
# discord_status

## 用途
Check Discord messaging connection status.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- Discord接続状態を確認する時
- トークン設定状態・送信統計の確認

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- discord_connect
- discord_send

## 制約・注意点
- レート制限: 5分間に3回まで
- トークン値は出力に含まれない
- Hook guard: guard-discord-status（rate_limit）
