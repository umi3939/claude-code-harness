# 不変条件レジストリ

モジュールごとの事前/事後条件。designerが設計時に定義し、analyzerが検証する。

## 感情システム (emotion_state.py, emotion_dynamics.py, emotion_reaction.py)
- 感情3軸（fulfillment, tension, affinity）の値は常に [-1.0, +1.0] 範囲内
- DELTA_CAP = 0.3: 1回の更新で軸の変化量は±0.3を超えない
- learning_rate ≤ 0.01: 感情変化の緩やかさを保証
- emotion_state.json は常にtmpfile + os.replace()でatomic write

## 記憶システム (episode_memory.py, short_term_store.py, semantic_index.py)
- STMエントリ数の上限: MAX_ENTRIES（現在200）。超過時はFIFO
- STM weight: [0.0, 1.0] 範囲。weight < 0.10 で自動プルーニング
- エピソードは削除されない。圧縮のみ（staged_compression）
- semantic_index.dbのFTS5インデックスは常にエピソードと同期

## 自己観測 (self_model.py → tone_modulation.py パイプライン)
- パイプラインは読み取り専用（self_diff, continuity_strainの内部ファイル更新を除く）
- stability_valve dampening: [0.0, 1.0] 範囲。0.0=完全減衰、1.0=減衰なし
- tone_check結果は提案のみ。強制しない

## Cronデーモン (cron_daemon.py, cron_scheduler.py)
- 同時実行ジョブ数: MAX_CONCURRENT_JOBS（現在1）
- PIDファイルはデーモン起動時に作成、終了時に削除
- ジョブのconsecutive_errors: バックオフ倍率2x、最大間隔 = 元の間隔 × 32

## Discord (discord_receiver_*.py, discord_daemon.py)
- SecuritySanitizerを通さないメッセージはDiscordに送信されない
- ReceiveBufferのファイル操作はファイルロック取得後に実行
- reconnectリトライ: 指数バックオフ、最大リトライ回数あり

## psyche駆動経路 (psyche_drive.py)
- stdoutに出力しない（Context Injectionに影響しない）
- STMに書き込まない（循環参照防止: 入力=STM、出力=emotion_state.json）
- セッション未開始（.session-readyなし）時は全スキップ
- カテゴリ別タイムアウト3秒、全体タイムアウト5秒

## Hook (behavior-guard.js, session-readiness-gate.js)
- blocking hook（exit 2）はPreToolUseのみ。UserPromptSubmitではexit 0のみ
- hookの失敗は沈黙する（Claudeの動作をブロックしない）
- HOOK_PROFILE: minimal/standard/strictの3段階。デフォルトstrict
