---
description: emotion_get MCPツールの使い方ガイド
---
# emotion_get

## 用途
現在の感情状態（3軸: fulfillment, tension, affinity）を読み取る。セッション間の経過時間に基づく減衰を適用してから返す。

## 引数
なし。パラメータ不要で呼び出す。

## 使用場面
- 現在の感情状態を確認したい時
- 他の処理の前に感情の基準値を把握したい時
- 感情変化の前後比較のために「変化前」の値を取得したい時

## 使用すべきでない場面
- session_startが完了する前（感情状態が復元されていない可能性がある）
- 感情を変更する目的で呼ぶ（読み取り専用。変更にはemotion_updateまたはemotion_reactを使う）
- 頻繁なポーリング目的での連続呼び出し

## 関連ツール
- emotion_update: 手動で感情軸を変更する
- emotion_react: 会話知覚に基づいて感情を自動更新する
- emotion_history: 感情変化の履歴を参照する
- emotion_restore: セッション開始時の感情復元（session_startに含まれる）

## 制約・安全弁
- 各軸の値域は -1.0 から +1.0（0.0が中立）
- fulfillment: 生産的進捗感(+) vs 停滞感(-)
- tension: 覚醒・集中(+) vs リラックス(-)
- affinity: 協調的つながり(+) vs 切断感(-)
- 呼び出し時にセッション間減衰が自動適用される（中立方向への自然回帰）
- Hook guard: guard-emotion-get（session_start完了前の呼び出しを検出）
