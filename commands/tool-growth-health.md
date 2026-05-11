---
description: growth_health MCPツールの使い方ガイド
---
# growth_health

## 用途
Get a single-line growth health summary.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 成長状況のダッシュボード確認
- セッション開始時のヘルスチェック

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- growth_dashboard

## 制約・注意点
- レート制限: 10分間に1回まで
- Hook guard: guard-growth-health（rate_limit）
