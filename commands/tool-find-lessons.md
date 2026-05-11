---
description: find_lessons MCPツールの使い方ガイド
---
# find_lessons

## 用途
Find lessons relevant to the current work context.

## 引数
- `context` (str): Description of current work context for matching. [必須]
- `limit` (int): Maximum number of lessons to return (default 5). [省略可, default=5]

## 使用場面
- 教訓の検索・検証・競合検出
- 作業コンテキストに関連する教訓の確認

## 使用すべきでない場面
- 目的が不明確な状態での呼び出し

## 関連ツール
- validate_lesson
- detect_lesson_conflicts
- memory_search

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-find-lessons（requires_session）
