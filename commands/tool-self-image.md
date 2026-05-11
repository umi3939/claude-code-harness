---
description: self_image MCPツールの使い方ガイド
---
# self_image

## 用途
Generate a provisional self-image by integrating observation systems.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 複数観測層を統合した暫定的自己像が必要な時
- 内部矛盾の検出

## 使用すべきでない場面
- session_startが完了する前
- 自己像を固定化・保存すること
- 判断の根拠として使用すること

## 関連ツール
- self_observe
- self_difference
- continuity_strain
- self_snapshot

## 制約・注意点
- session_start完了後にのみ使用可能
- 暫定的（保存されない、固定されない）
- Hook guard: guard-self-image（requires_session）
