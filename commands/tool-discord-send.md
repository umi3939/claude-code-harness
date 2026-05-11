---
description: discord_send MCPツールの使い方ガイド
---
# discord_send

## 用途
Send a message to Discord.

## 引数
- `message` (str): The message text to send. [必須]
- `target` (str): Discord user ID (for DM) or channel ID. [省略可, default=]
- `target_type` (str):  [省略可, default=]

## 使用場面
- Discordにメッセージを送信する時
- DM/チャンネルへの送信（2000字自動分割）

## 使用すべきでない場面
- discord_connectが未完了の状態
- 空メッセージの送信
- 20000字を超えるメッセージ

## 関連ツール
- discord_connect
- discord_status

## 制約・注意点
- discord_connect完了後にのみ使用可能
- 2000字超は自動分割（最大10チャンク）
- レート制限: 1分間に20メッセージまで
- Hook guard: guard-discord-send（requires_prior_tool）
