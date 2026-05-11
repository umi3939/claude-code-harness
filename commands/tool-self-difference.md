---
description: self_difference MCPツールの使い方ガイド
---
# self_difference

## 用途
Observe how internal state has changed compared to previous observation.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 前回の観測との差分を確認したい時
- 内部状態の変化量を把握する時

## 使用すべきでない場面
- session_startが完了する前
- 差分データを意思決定の根拠として使用すること

## 関連ツール
- self_observe
- continuity_strain
- self_snapshot

## 制約・注意点
- session_start完了後にのみ使用可能
- スナップショットFIFO履歴に1件追加される副作用あり
- Hook guard: guard-self-difference（requires_session）
