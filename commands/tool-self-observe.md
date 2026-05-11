---
description: self_observe MCPツールの使い方ガイド
---
# self_observe

## 用途
Observe current internal state as an integrated snapshot.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- セッション中の内部状態スナップショットが必要な時
- 感情・変化・記憶の統合観測

## 使用すべきでない場面
- session_startが完了する前
- 判断や行動の入力として使用すること（READ-ONLY）

## 関連ツール
- self_difference
- self_snapshot

## 制約・注意点
- session_start完了後にのみ使用可能
- READ-ONLY（状態変更なし）
- Hook guard: guard-self-observe（requires_session）
