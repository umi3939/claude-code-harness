---
description: "workflow_crystallize MCPツールの使い方ガイド"
version: "1.0.0"
last_updated: "2026-04-02"
---

# workflow_crystallize MCPツール

## 概要
ツール使用の観測ログから繰り返しパターンを検出し、スキル候補として提案する。

## 引数
| 引数 | 型 | 必須 | デフォルト | 説明 |
|------|------|------|-----------|------|
| last_n | int | No | 1000 | 分析する直近の観測数 |
| min_occurrences | int | No | 3 | 報告する最低出現回数 |
| max_candidates | int | No | 20 | 返す候補の最大数 |

## 使い方

### 基本（デフォルト設定）
```
workflow_crystallize
```

### カスタム設定
```
workflow_crystallize last_n=500 min_occurrences=5 max_candidates=10
```

## 出力内容
- 検出されたパターン（ツールシーケンス）
- 各パターンの出現回数
- スキル名の提案

## 注意事項
- READ-ONLY: 観測ログもスキルファイルも変更しない
- 候補提案のみ: スキルの自動作成はしない
- 自身のツール名は除外フィルタで検出対象外
- observations.jsonl末尾N行のみ処理（メモリ安全）
