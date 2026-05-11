---
description: aar_report MCPツールの使い方ガイド
---
# aar_report

## 用途
Get a formatted report of recent After-Action Reviews.

## 引数
- `limit` (int): Number of recent AARs to include (default 5) [省略可, default=5]

## 使用場面
- 成功体験の構造的レビュー
- 再現可能なパターンの抽出と記録

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- create_aar
- search_aars_tool

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-aar-report（requires_session）
