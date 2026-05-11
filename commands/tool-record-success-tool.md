---
description: record_success_tool MCPツールの使い方ガイド
---
# record_success_tool

## 用途
Record a success pattern for future reference.

## 引数
- `event_type` (str): One of review_zero, test_pass, user_positive [必須]
- `context` (str): Description of what happened (max 500 chars) [必須]
- `why_success` (str): Analysis of why it succeeded (max 1000 chars) [必須]
- `tags` (str): Comma-separated tags for categorization (optional) [省略可, default=]

## 使用場面
- 成功パターンの記録・検索
- 過去の成功体験からの学習

## 使用すべきでない場面
- 必須引数が空文字や未定義の状態での呼び出し

## 関連ツール
- search_successes_tool
- update_mastery

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-record-success-tool（requires_session）
