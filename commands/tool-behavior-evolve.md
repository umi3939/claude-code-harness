---
description: behavior_evolve MCPツールの使い方ガイド
---
# behavior_evolve

## 用途
Compare lessons registry against behavior rules to show coverage.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 教訓レジストリとbehavior-rulesのカバレッジを比較する時
- 未対応教訓の把握

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）
- 教訓が更新されていない時

## 関連ツール
- behavior_analyze

## 制約・注意点
- レート制限: 10分間に1回まで
- lessons_registry.mdが必要
- Hook guard: guard-behavior-evolve（rate_limit）
