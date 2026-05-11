---
description: search_aars_tool MCPツールの使い方ガイド
---
# search_aars_tool

## 用途
Search After-Action Reviews by keyword or tags.

## 引数
- `query` (str): Text to search across all content fields (case-insensitive) [省略可, default=]
- `tags` (list[str] | None): Filter by tags (OR matching — any tag match counts) [省略可, default=None]
- `limit` (int): Maximum results to return (default 5) [省略可, default=5]

## 使用場面
- 成功体験の構造的レビュー
- 再現可能なパターンの抽出と記録

## 使用すべきでない場面
- 目的が不明確な状態での呼び出し

## 関連ツール
- create_aar
- aar_report

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-search-aars-tool（requires_session）
