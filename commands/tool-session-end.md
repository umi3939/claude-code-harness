---
description: session_end MCPツールの使い方ガイド
---
# session_end

## 用途
セッションを終了し、コンテキストの保存・最終観測・感情状態の永続化を行う。次回セッションのsession_startで参照される情報を保存する。

## 引数
| 引数名 | 型 | 必須/任意 | 説明 |
|--------|-----|-----------|------|
| summary | string | 必須 | このセッションで何を行ったかの要約 |
| completed | string | 任意 | 完了したタスクのカンマ区切りリスト |
| pending | string | 任意 | 未完了タスクのカンマ区切りリスト |
| decisions | string | 任意 | セッション中に行った主要な意思決定 |
| issues | string | 任意 | 既知の問題やブロッカー |
| next_actions | string | 任意 | 次のステップの提案 |

## 使用場面
- 会話セッションの終了時に1回呼び出す
- 長時間作業の区切りでコンテキストを保存したい時
- 次セッションに引き継ぐべき情報がある時

## 使用すべきでない場面
- session_startを呼んでいないセッションでの単独使用
- summaryが空文字のまま呼ぶ（ガードでブロックされる）
- セッション途中での一時保存目的（memory_recordやstm_writeを使う）

## 制約・注意点
- summary引数は必須。空文字や未指定はguard-session-endでブロックされる
- 内部で3つのサブ処理を実行: (1)セッションコンテキスト保存 (2)最終セルフスナップショット (3)感情状態永続化
- .session-end-doneフラグファイルが書き出され、二重実行を防止する
- 保存されたコンテキストは次回session_startのPrevious Sessionセクションに表示される
- Hook guard: guard-session-end（summary空文字でブロック）
