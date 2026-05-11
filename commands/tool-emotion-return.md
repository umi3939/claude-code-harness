---
description: emotion_return MCPツールの使い方ガイド
---
# emotion_return

## 用途
Process memory-emotion return: recalled episodes influence current emotion state.

## 引数
- `search_results` (str): Text output from memory_search tool containing episode IDs. [省略可, default=]

## 使用場面
- 感情状態の管理・追跡が必要な時
- session_start完了後の感情関連操作

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）

## 関連ツール
- emotion_react
- emotion_get
- memory_search

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-emotion-return（requires_session）
