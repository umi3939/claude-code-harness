---
description: memory_verify MCPツールの使い方ガイド
---
# memory_verify

## 用途
Verify answers to dynamic read verification questions.

## 引数
- `answers` (str): Category A answers in format 'Q1:answer1,Q3:answer3,...' [必須]

## 使用場面
- 記憶システムへのアクセスが必要な時
- エピソードの記録・検索・管理

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）

## 関連ツール
- memory_search

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-memory-verify（requires_session）
