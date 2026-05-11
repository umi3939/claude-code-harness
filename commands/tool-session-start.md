---
description: session_start MCPツールの使い方ガイド
---
# session_start

## 用途
新しい会話セッションを初期化する。感情状態の復元、短期記憶の復元、活性化サーフェスの表出、セルフスナップショットの4ステップを順に実行する。

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 会話セッションの最初に1回だけ呼び出す
- 前回セッションのPending/Next Actionsを確認したい時
- 感情状態・短期記憶を前回セッションから引き継ぎたい時
- 現在のGap Analysisとconsolidated principlesを確認したい時

## 使用すべきでない場面
- 同一セッション内で2回以上呼ばない（二重初期化は不正）
- セッション途中での状態リセット目的には使わない
- emotion_restore/stm_restoreを個別に呼びたいだけなら、それぞれ単独で呼ぶ

## 制約・注意点
- 内部で4つのサブ処理（emotion_restore, stm_restore, activation_surface, self_snapshot）を順次実行する
- 前回セッションの.dev-flow-stateと.session-end-doneフラグをリセットする
- 返却値にはPrevious Session情報、Consolidated Principles、感情状態、STM、活性化サーフェス、セルフスナップショットが含まれる
- Hook guard: guard-session-start（30分以内に2回以上でブロック）
