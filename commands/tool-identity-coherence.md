---
description: identity_coherence MCPツールの使い方ガイド
---
# identity_coherence

## 用途
Assess identity coherence by detecting overlap of shift signals.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 複数シフト信号の重なりによるアイデンティティ一貫性を評価する時
- 内部状態が「安定」か「切断的」かを把握する時

## 使用すべきでない場面
- session_startが完了する前
- coherence値で行動を制御すること

## 関連ツール
- self_image
- self_snapshot

## 制約・注意点
- session_start完了後にのみ使用可能
- 完全ステートレス（履歴なし）
- Hook guard: guard-identity-coherence（requires_session）
