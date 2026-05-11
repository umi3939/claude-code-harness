---
description: emotion_restore MCPツールの使い方ガイド
---
# emotion_restore

## 用途
Restore emotion state at session start with decay applied.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 感情状態の管理・追跡が必要な時
- session_start完了後の感情関連操作

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）

## 関連ツール
- emotion_get
- session_start

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-emotion-restore（requires_session）
