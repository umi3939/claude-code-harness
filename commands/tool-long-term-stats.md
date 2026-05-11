---
description: long_term_stats MCPツールの使い方ガイド
---
# long_term_stats

## 用途
Get long-term emotion dynamics statistics.

## 引数
- `last_n` (int): Number of recent entries to analyze (default 10) [省略可, default=10]

## 使用場面
- 長期的な感情ダイナミクスの統計を確認する時
- 軸ごとの平均・分散・トレンドの把握

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）
- 統計データを判断の直接根拠にすること

## 関連ツール
- long_term_record
- self_snapshot

## 制約・注意点
- レート制限: 10分間に1回まで
- READ-ONLY（状態変更なし）
- Hook guard: guard-long-term-stats（rate_limit）
