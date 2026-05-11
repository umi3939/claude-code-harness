---
description: behavior_analyze MCPツールの使い方ガイド
---
# behavior_analyze

## 用途
Analyze observation logs to detect behavioral patterns and suggest new rules.

## 引数
- `last_n` (int): Number of recent observations to analyze (default 200) [省略可, default=200]
- `tool_filter` (str): Optional tool name filter (e.g. "Write" to analyze only Write calls) [省略可, default=]

## 使用場面
- 観測ログからツール使用パターンを分析する時
- バーストアクティビティや異常パターンの検出

## 使用すべきでない場面
- observations.jsonlが存在しない時
- session_startが完了する前

## 関連ツール
- behavior_evolve

## 制約・注意点
- session_start完了後にのみ使用可能
- observations.jsonlが必要
- Hook guard: guard-behavior-analyze（requires_session）
