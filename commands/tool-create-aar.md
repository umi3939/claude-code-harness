---
description: create_aar MCPツールの使い方ガイド
---
# create_aar

## 用途
Record an After-Action Success Review (AAR).

## 引数
- `intent` (str): What was the intended outcome? [必須]
- `actual` (str): What actually happened? [必須]
- `why_success` (str): Why did it succeed? (root cause of success) [必須]
- `replicable` (str): What aspects are replicable in other contexts? [必須]
- `context_dependent` (str): What aspects were specific to this context? [必須]
- `transferable` (str): What can be transferred to other domains? [必須]
- `tags` (list[str] | None): Optional tags for categorization (max 10) [省略可, default=None]

## 使用場面
- 成功体験の構造的レビュー
- 再現可能なパターンの抽出と記録

## 使用すべきでない場面
- 必須引数が空文字や未定義の状態での呼び出し

## 関連ツール
- search_aars_tool
- aar_report

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-create-aar（requires_session）
