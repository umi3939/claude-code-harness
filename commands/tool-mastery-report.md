---
description: mastery_report MCPツールの使い方ガイド
---
# mastery_report

## 用途
Generate a mastery profile report showing strengths, growth areas, and stats.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 成長状況のダッシュボード確認
- セッション開始時のヘルスチェック

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）

## 関連ツール
- update_mastery
- growth_dashboard

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-mastery-report（requires_session）
