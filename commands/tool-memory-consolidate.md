---
description: memory_consolidate MCPツールの使い方ガイド
---
# memory_consolidate

## 用途
Consolidate lessons into abstract principles (long-term memory integration).

## 引数
- `mode` (str): 'check' to load materials, 'save' to store principles [省略可, default=check]
- `principles_text` (str): (save mode only) Markdown text of extracted principles [省略可, default=]

## 使用場面
- 記憶システムへのアクセスが必要な時
- エピソードの記録・検索・管理

## 使用すべきでない場面
- session_startが完了する前（状態が復元されていない可能性がある）
- 60分以内に連続呼び出し（レート制限あり）
- 新規教訓がない状態での呼び出し（checkモードで事前確認）

## 関連ツール
- memory_status
- find_lessons
- validate_lesson

## 制約・注意点
- レート制限: 60分間に1回まで
- Hook guard: guard-memory-consolidate（rate_limit）
