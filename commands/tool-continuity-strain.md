---
description: continuity_strain MCPツールの使い方ガイド
---
# continuity_strain

## 用途
Observe self-continuity strain from persistent self-differences.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 持続的な内部変化が自己連続性に与える影響を観測する時
- 変化の持続性・トレンドを確認する時

## 使用すべきでない場面
- session_startが完了する前
- strainの有無で行動を変えること

## 関連ツール
- self_difference
- self_image
- self_snapshot

## 制約・注意点
- session_start完了後にのみ使用可能
- 観測履歴に1件追加される副作用あり
- Hook guard: guard-continuity-strain（requires_session）
