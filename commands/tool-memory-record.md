---
description: memory_record MCPツールの使い方ガイド
---
# memory_record

## 用途
Record an episode to the memory system and rebuild the topic index.

## 引数
- `episode_type` (str): Episode type (user_request, decision, error, solution, feedback, observation) [必須]
- `summary` (str): A concise summary of the episode [必須]
- `tags` (str): Comma-separated tags for categorization (optional) [省略可, default=]
- `user_text` (str): Verbatim user utterance to preserve (optional) [省略可, default=]

## 使用場面
- 記憶システムへのアクセスが必要な時
- エピソードの記録・検索・管理

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）
- 必須引数が空文字や未定義の状態での呼び出し

## 関連ツール
- memory_search
- stm_write

## 制約・注意点
- 必須引数: summary
- Hook guard: guard-memory-record（required_args）
