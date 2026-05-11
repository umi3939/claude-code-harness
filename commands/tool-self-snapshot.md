---
description: self_snapshot MCPツールの使い方ガイド
---
# self_snapshot

## 用途
Run the full self-observation pipeline in one call.

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 全7観測層を一括実行して統合ビューを得る時
- 作業区切り・フェーズ移行時の自己観測

## 使用すべきでない場面
- 短時間での連続呼び出し（レート制限あり）
- 高頻度の定期実行

## 関連ツール
- self_observe
- self_difference
- continuity_strain
- self_image
- identity_coherence
- stability_check
- tone_check

## 制約・注意点
- レート制限: 10分間に1回まで
- 7層全ての観測を実行するため比較的重い
- Hook guard: guard-self-snapshot（rate_limit）
