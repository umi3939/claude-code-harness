---
description: emotion_update MCPツールの使い方ガイド
---
# emotion_update

## 用途
感情状態の3軸を手動で調整する。deltaモード（現在値に加算）とsetモード（値を直接設定）を切り替えて使う。

## 引数
| 引数名 | 型 | 必須/任意 | 説明 |
|--------|-----|-----------|------|
| fulfillment | float | 任意 | fulfillment軸の変化量（deltaモード）または設定値（setモード） |
| tension | float | 任意 | tension軸の変化量（deltaモード）または設定値（setモード） |
| affinity | float | 任意 | affinity軸の変化量（deltaモード）または設定値（setモード） |
| mode | string | 任意 | "delta"（デフォルト: 現在値に加算）または "set"（値を直接設定） |
| reason | string | 任意 | 変更理由（最大200文字。変更ログに記録される） |

## 使用場面
- 感情状態を直接調整する必要がある時（emotion_reactでは表現できない特殊な状況）
- テストやデバッグで特定の感情状態を設定したい時
- setモードで特定の軸を中立（0.0）にリセットしたい時

## 使用すべきでない場面
- 通常の会話に対する感情反応（emotion_reactを使う）
- session_startが完了する前
- reasonを指定せずに頻繁に呼ぶ（変更ログの可読性が下がる）

## 関連ツール
- emotion_react: 会話知覚に基づく自動感情更新（通常はこちらを優先）
- emotion_get: 現在の感情状態を読み取る
- emotion_history: 感情変化の履歴を参照する

## 制約・安全弁
- 値は -1.0 から +1.0 にクランプされる（範囲外の値は自動補正）
- deltaモードでは現在値に加算、setモードでは直接上書き
- 少なくとも1つの軸の値を指定する必要がある（全て省略すると変化なし）
- reason引数は最大200文字。変更ログにそのまま記録される
- Hook guard: guard-emotion-update（極端な値域への設定を検出）
