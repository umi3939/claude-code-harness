---
description: memory_status MCPツールの使い方ガイド
---
# memory_status

## 用途
Get the current status of the memory system including compression stats.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 記憶システムへのアクセスが必要な時
- エピソードの記録・検索・管理

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）

## 関連ツール
- memory_consolidate

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-memory-status（requires_session）
