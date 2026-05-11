---
description: record_trajectory MCPツールの使い方ガイド
---
# record_trajectory

## 用途
Record a successful execution trajectory for future reuse.

## 引数
- `task_class` (str): Task classification (e.g. "hook_implementation", "mcp_tool_creation") [必須]
- `steps` (str): JSON array of step objects, each with {action, tool, approach, result} [必須]
- `outcome` (str): Final result text describing what was achieved [必須]
- `transferability` (float): How transferable to other tasks (0.0-1.0, default 0.5) [省略可, default=0.5]

## 使用場面
- 成功した実行パターンの記録・参照
- 類似タスクのアプローチ選定時

## 使用すべきでない場面
- 必須引数が空文字や未定義の状態での呼び出し

## 関連ツール
- find_trajectories
- golden_paths

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-record-trajectory（requires_session）
