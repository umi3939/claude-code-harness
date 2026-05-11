---
description: stm_write MCPツールの使い方ガイド
---
# stm_write

## 用途
Write a raw thought, question, impression or feeling to short-term memory.

## 引数
- `content` (str): The raw text to store (max 2000 chars) [必須]
- `category` (str): One of: thought, question, impression, unresolved, feeling, self_review [省略可, default=thought]

## 使用場面
- 未処理の思考・疑問・印象を記録・参照する時
- 対話中のリアルタイムな気づきの保持

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）

## 関連ツール
- stm_read
- stm_restore

## 制約・注意点
- 必須引数: content
- Hook guard: guard-stm-write（required_args）
