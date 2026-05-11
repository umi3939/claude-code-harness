---
description: stm_restore MCPツールの使い方ガイド
---
# stm_restore

## 用途
Restore short-term memory at session start with decay applied.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 未処理の思考・疑問・印象を記録・参照する時
- 対話中のリアルタイムな気づきの保持

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）

## 関連ツール
- stm_write
- stm_read
- session_start

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-stm-restore（requires_session）
