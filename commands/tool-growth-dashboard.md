---
description: growth_dashboard MCPツールの使い方ガイド
---
# growth_dashboard

## 用途
Generate a growth metrics dashboard with lessons, successes, mastery, and balance.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 成長状況のダッシュボード確認
- セッション開始時のヘルスチェック

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- growth_health
- mastery_report

## 制約・注意点
- レート制限: 10分間に1回まで
- Hook guard: guard-growth-dashboard（rate_limit）
