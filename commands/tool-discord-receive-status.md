---
description: discord_receive_status MCPツールの使い方ガイド
---
# discord_receive_status

## 用途
Check Discord receive daemon status.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 受信デーモンの稼働状態を確認する時
- 受信統計・許可リスト・バッファ状態の把握

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- discord_receive_allow
- discord_receive_pending

## 制約・注意点
- レート制限: 5分間に3回まで
- Hook guard: guard-discord-receive-status（rate_limit）
