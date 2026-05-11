---
description: emotion_history MCPツールの使い方ガイド
---
# emotion_history

## 用途
View emotion change history with freshness indicators.

## 引数
- `limit` (int): Maximum number of entries to show (default 20, 0 = all) [省略可, default=20]

## 使用場面
- 感情状態の管理・追跡が必要な時
- session_start完了後の感情関連操作

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）

## 関連ツール
- emotion_get
- emotion_update
- emotion_react

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-emotion-history（requires_session）
