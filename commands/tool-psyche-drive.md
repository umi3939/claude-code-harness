---
description: psyche_drive MCPツールの使い方ガイド
---
# psyche_drive

## 用途
Run automatic psyche state updates (emotion, observation, activation) based on time/phase triggers.

## MCP function
mcp__self-observation__psyche_drive

## 引数
- `memory_dir` (string, optional): Directory containing psyche state files. Default: auto-resolved memory dir

## 使用場面
- UserPromptSubmit時の自動状態更新（skill_executor経由で自動実行）
- 手動で感情・観測・活性化の更新をトリガーしたい時

## 使用すべきでない場面
- 特定の感情値を直接設定したい時（emotion_reactを使用）
- 観測結果のみが必要な時（self_snapshotを使用）

## 関連ツール
- behavior_guidance (状態に基づく行動指針生成)
- emotion_react (感情反応の直接記録)
- self_snapshot (観測パイプラインの直接実行)
- activation_surface (活性化表面の直接確認)

## 制約・注意点
- 副作用あり: emotion_state.json, dynamics_state.json, 観測結果ファイル, 活性化結果ファイルへの書き込み
- 内部タイムアウト: 全体5秒、カテゴリ別3秒
- セッション準備未完了時は実行しない（behavior-guard-state.jsonを確認）
- 連続失敗時は指数バックオフで更新間隔を延長
- skill_executor経由の場合: 返却値を使用しない（実行して終わり）
