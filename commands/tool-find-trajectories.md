---
description: find_trajectories MCPツールの使い方ガイド
---
# find_trajectories

## 用途
Find similar trajectories by task class for reuse as reference approaches.

## 引数
- `task_class` (str): Task class to search for (exact match) [必須]
- `limit` (int): Maximum number of results (default 3) [省略可, default=3]

## 使用場面
- Find similar trajectories by task class for reuse as reference approaches.

## 使用すべきでない場面
- 目的が不明確な状態での呼び出し

## 関連ツール
- record_trajectory
- golden_paths

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-find-trajectories（requires_session）
