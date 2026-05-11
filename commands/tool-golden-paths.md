---
description: golden_paths MCPツールの使い方ガイド
---
# golden_paths

## 用途
Get Golden Path trajectories — proven, frequently-reused execution patterns.

## 引数
- `min_usage` (int): Minimum usage count to qualify as Golden Path (default 3) [省略可, default=3]

## 使用場面
- 成功した実行パターンの記録・参照
- 類似タスクのアプローチ選定時

## 使用すべきでない場面
- 目的が不明確な状態での呼び出し

## 関連ツール
- find_trajectories
- record_trajectory

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-golden-paths（requires_session）
