---
description: tone_check MCPツールの使い方ガイド
---
# tone_check

## 用途
Check recommended response tone based on current emotion state.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 現在の感情状態に基づく推奨応答トーンを確認する時
- 応答のトーンバランスを調整する時

## 使用すべきでない場面
- session_startが完了する前
- トーンを固定的に設定したい場合

## 関連ツール
- stability_check
- self_snapshot

## 制約・注意点
- session_start完了後にのみ使用可能
- 完全ステートレスかつREAD-ONLY
- Hook guard: guard-tone-check（requires_session）
