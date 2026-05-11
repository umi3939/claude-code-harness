---
description: discord_connect MCPツールの使い方ガイド
---
# discord_connect

## 用途
Connect to Discord and verify bot token.

## 引数
- `token` (str): Discord bot token. If empty, resolved from env/config. [省略可, default=]
- `default_target` (str): Default send target (Discord user ID for DM, [省略可, default=]
- `default_target_type` (str):  [省略可, default=]

## 使用場面
- Discord Botとの接続を確立する時
- デフォルト送信先の設定

## 使用すべきでない場面
- トークンが未設定の状態での呼び出し

## 関連ツール
- discord_status
- discord_send

## 制約・注意点
- DISCORD_BOT_TOKEN環境変数が必要
- トークンはログ/出力に含まれない
- Hook guard: guard-discord-connect（required_args）
