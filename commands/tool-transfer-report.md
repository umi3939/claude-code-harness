---
description: transfer_report MCPツールの使い方ガイド
---
# transfer_report

## 用途
Get a formatted report of cross-domain pattern transfers.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- ドメイン間のパターン転用の記録・分析
- 成功パターンの適用範囲を拡大する時

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- record_transfer

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-transfer-report（requires_session）
