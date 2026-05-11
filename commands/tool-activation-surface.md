---
description: activation_surface MCPツールの使い方ガイド
---
# activation_surface

## 用途
Surface what should be on my mind right now.

## 引数
- `context` (str): Optional current task context for context-aware surfacing. [省略可, default=]

## 使用場面
- セッション開始時やフェーズ移行時の記憶サーフェス
- Attention Residual: 現在のタスクに関連する記憶の浮上

## 使用すべきでない場面
- 目的が不明確な状態での呼び出し

## 関連ツール
- memory_search
- find_lessons
- session_start

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-activation-surface（requires_session）
