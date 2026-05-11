---
description: discord_receive_allow MCPツールの使い方ガイド
---
# discord_receive_allow

## 用途
Add a user or channel to the receive allow list.

## 引数
- `id` (str): Discord user ID or channel ID to allow. [必須]
- `id_type` (str): "user" or "channel" (default: "user"). [省略可, default=user]

## 使用場面
- 受信許可リストにユーザー/チャンネルを追加する時
- デフォルトは全拒否（明示的許可が必要）

## 使用すべきでない場面
- IDが不明な状態での呼び出し
- id_typeがuser/channel以外

## 関連ツール
- discord_receive_remove
- discord_receive_status

## 制約・注意点
- 必須引数: id
- id_typeはuser/channelのいずれか
- Hook guard: guard-discord-receive-allow（required_args）
