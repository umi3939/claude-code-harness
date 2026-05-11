---
description: stm_read MCPツールの使い方ガイド
---
# stm_read

## 用途
Read short-term memory entries.

## 引数
- `category` (str): Filter by category (thought/question/impression/unresolved/feeling/self_review). Empty = all. [省略可, default=]
- `limit` (int): Max entries to return (default 20) [省略可, default=20]

## 使用場面
- 未処理の思考・疑問・印象を記録・参照する時
- 対話中のリアルタイムな気づきの保持

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）

## 関連ツール
- stm_write
- stm_restore

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-stm-read（requires_session）
