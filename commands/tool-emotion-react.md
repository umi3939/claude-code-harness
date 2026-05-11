---
description: emotion_react MCPツールの使い方ガイド
---
# emotion_react

## 用途
会話の知覚属性（感情ラベル・バレンス・意図）から3軸のデルタを導出し、感情状態を自動更新する。emotion_update（手動調整）の自動版。

## 引数
| 引数名 | 型 | 必須/任意 | 説明 |
|--------|-----|-----------|------|
| emotion_label | string | 必須 | 知覚された感情。happy, sad, angry, surprised, scared, loving, teasing, neutral のいずれか |
| emotion_valence | float | 必須 | 感情のバレンス/強度（-1.0 から +1.0） |
| intent | string | 任意 | 会話の意図。sharing, question, expression, greeting, farewell, その他。デフォルト "neutral" |
| amplitude_modifier | float | 任意 | デルタの大きさを方向を変えずにスケーリング（デフォルト 1.0） |
| reason | string | 任意 | 変更理由（変更ログに記録される） |

## 使用場面
- ユーザーとのやり取りの後に感情反応を記録する時
- 会話の文脈から感情的な影響を処理する時
- emotion_reactの呼び出しはemotion_updateと共存する（自動と手動は独立）

## 使用すべきでない場面
- emotion_labelが上記8種以外の値の時
- 手動で特定の値に感情を設定したい時（emotion_updateを使う）
- 同一メッセージに対して複数回呼ぶ

## 関連ツール
- emotion_update: 手動で感情軸を直接調整する
- emotion_get: 現在の感情状態を読み取る
- emotion_return: 記憶想起時の感情帰還処理

## 内部連鎖（1:1:1文書的準拠）

emotion_reactは内部でfacade_record_long_termを副作用として自動呼び出しする。

**連鎖フロー:**
1. 感情デルタ算出・ダイナミクス反映・状態更新（主処理）
2. 更新後の感情状態をfacade_record_long_termに渡して長期記録（副作用）

**この連鎖が分離できない理由:**
- 感情反応後の長期記録は原子的な処理単位であり、自動性の要件から分離できない
- Claude Code hookの仕様制約（hook間データ受け渡し不可）により外部化は技術的に困難
- 連鎖を外部化するとClaudeが毎回明示的にlong_term_recordを呼ぶ必要があり、呼び忘れリスクが発生する

**安全弁:**
- facade_record_long_termの呼び出しはtry/exceptで囲まれており、失敗しても主処理結果に影響しない
- long_term_record自体は独立したMCPツール（mcp__self-observation__long_term_record）として存在し、Claude側からの明示呼び出しも可能

**emotion_updateも同様の内部連鎖を持つ。**

## 制約・安全弁
- emotion_labelは8種（happy, sad, angry, surprised, scared, loving, teasing, neutral）のいずれか
- emotion_valenceは -1.0 から +1.0
- intentは sharing, question, expression, greeting, farewell, または other
- 感情ダイナミクス（位相遷移・振幅変調）が自動適用される
- 安定性弁（observation facadeのdampening）が極端な変動を抑制する
- 長期ダイナミクス観測が自動記録される（失敗しても本体には影響しない）
- Hook guard: guard-emotion-react（intent/emotion_label引数の妥当な組み合わせ確認）
