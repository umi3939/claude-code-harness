---
description: detect_lesson_conflicts MCPツールの使い方ガイド
---
# detect_lesson_conflicts

## 用途
Detect conflicting lessons within the same Rule category.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 教訓の検索・検証・競合検出
- 作業コンテキストに関連する教訓の確認

## 使用すべきでない場面
- 目的が不明確な状態での呼び出し

## 関連ツール
- find_lessons
- validate_lesson

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-detect-lesson-conflicts（requires_session）
