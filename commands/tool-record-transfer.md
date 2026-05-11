---
description: record_transfer MCPツールの使い方ガイド
---
# record_transfer

## 用途
Record a cross-domain transfer of a success pattern.

## 引数
- `pattern_id` (str): Identifier of the success pattern being transferred [必須]
- `source_domain` (str): Domain the pattern originated from (max 50 chars) [必須]
- `target_domain` (str): Domain the pattern was applied to (max 50 chars) [必須]
- `success` (bool): Whether the transfer was successful [必須]
- `notes` (str): Optional notes about the transfer (max 500 chars) [省略可, default=]

## 使用場面
- ドメイン間のパターン転用の記録・分析
- 成功パターンの適用範囲を拡大する時

## 使用すべきでない場面
- 必須引数が空文字や未定義の状態での呼び出し

## 関連ツール
- transfer_report
- record_success_tool

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-record-transfer（requires_session）
