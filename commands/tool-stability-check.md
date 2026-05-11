---
description: stability_check MCPツールの使い方ガイド
---
# stability_check

## 用途
Check current stability valve status (extremity indicators and dampening).

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 極端性指標と抑制係数の現在値を確認する時
- emotion_reactの自動抑制状態を把握する時

## 使用すべきでない場面
- session_startが完了する前
- 手動でdampening値を変更したい場合（この関数はREAD-ONLY）

## 関連ツール
- self_snapshot
- tone_check

## 制約・注意点
- session_start完了後にのみ使用可能
- READ-ONLY（抑制係数の確認のみ）
- Hook guard: guard-stability-check（requires_session）
