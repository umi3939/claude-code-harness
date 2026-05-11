---
description: long_term_record MCPツールの使い方ガイド
---
# long_term_record

## 用途
Record a long-term dynamics observation of the current emotion state.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 手動で長期観測バッファに記録を追加する時
- emotion_react外での状態記録

## 使用すべきでない場面
- emotion_reactの後（自動統合済み）
- 目的が不明確な状態での呼び出し

## 関連ツール
- long_term_stats
- self_snapshot

## 制約・注意点
- emotion_reactに自動統合済み（通常は単独使用不要）
- Hook guard: guard-long-term-record（context_warning）
