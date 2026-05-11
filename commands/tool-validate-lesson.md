---
description: validate_lesson MCPツールの使い方ガイド
---
# validate_lesson

## 用途
Validate whether a lesson was effective.

## 引数
- `lesson_id` (str): Lesson number as string (e.g. "3" for lesson #3) [必須]
- `success` (bool): True if the lesson proved effective, False if the problem recurred [必須]
- `category` (str): Optional pattern category for audit trail [省略可, default=]

## 使用場面
- 教訓の検索・検証・競合検出
- 作業コンテキストに関連する教訓の確認

## 使用すべきでない場面
- 目的が不明確な状態での呼び出し

## 関連ツール
- find_lessons
- detect_lesson_conflicts

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-validate-lesson（requires_session）
