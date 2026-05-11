---
description: search_successes_tool MCPツールの使い方ガイド
---
# search_successes_tool

## 用途
Search recorded success patterns by keyword and/or tags.

## 引数
- `query` (str): Text to match against context and why_success (optional) [省略可, default=]
- `tags` (str): Comma-separated tags to filter by (optional) [省略可, default=]
- `limit` (int): Maximum number of results (default 10) [省略可, default=10]

## 使用場面
- 成功パターンの記録・検索
- 過去の成功体験からの学習

## 使用すべきでない場面
- 目的が不明確な状態での呼び出し

## 関連ツール
- record_success_tool
- memory_search

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-search-successes-tool（requires_session）
