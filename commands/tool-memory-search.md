---
description: memory_search MCPツールの使い方ガイド
---
# memory_search

## 用途
Search the memory system for past episodes.

## 引数
- `keywords` (str): Comma-separated keywords for full-text search (optional) [省略可, default=]
- `tags` (str): Comma-separated tags for context-based search (optional) [省略可, default=]
- `last` (str): Relative time range like '7d' or '24h' (optional) [省略可, default=]
- `limit` (int): Maximum number of results per search pathway (default 20) [省略可, default=20]
- `mood_reorder_enabled` (bool): Enable mood-linked reordering (default True, set False to disable) [省略可, default=True]
- `query` (str): Natural language search query using FTS5 (optional, mutually exclusive with keywords) [省略可, default=]

## 使用場面
- 記憶システムへのアクセスが必要な時
- エピソードの記録・検索・管理

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）

## 関連ツール
- memory_record
- find_lessons
- activation_surface

## 制約・注意点
- 特記事項なし
