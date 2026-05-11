---
description: "MCPツール全48+種の使い方を表示"
---

# MCPツール全48+種の使い方（6サーバー構成）

**サーバー構成（.mcp.jsonで定義）:**
- `memory-tools` (memory_mcp_server.py) → 17ツール: セッション3 + 感情6 + 記憶4 + STM3 + 統合1 + 活性化1 → プレフィックス `mcp__memory-tools__`
- `self-observation` (self_observation_mcp_server.py) → 12ツール: 自己観測9 + self_snapshot1 + behavior分析2 → プレフィックス `mcp__self-observation__`
- `persistent-cron` (cron_mcp_server.py) → 11ツール: ジョブ管理6 + デーモン制御2 + ログ1 + 通知1 + 緊急停止1 → プレフィックス `mcp__persistent-cron__`
- `discord` (discord_mcp_server.py) → 3ツール: 接続1 + 送信1 + 状態確認1 → プレフィックス `mcp__discord__`
- `http-fetch` (http_fetch_mcp_server.py) → 1ツール: 生HTTP取得 → プレフィックス `mcp__http-fetch__`
- `playwright` (npx @playwright/mcp@latest) → ブラウザ自動化（Microsoft公式）→ プレフィックス `mcp__playwright__`。--browser chromeで既存Chrome使用
- ENABLE_TOOL_SEARCH=false設定済み（全ツールを常時ロード、deferred無効化）

**セッションライフサイクル（3ツール、memory-tools側）:**
- `session_start`: セッション開始時に1回実行。感情復元+STM復元+activation surface+全自己観測を一括実行
- `session_end`: セッション終了時に1回実行。引数: summary(必須), completed, pending, decisions, issues, next_actions。セッション文脈+最終観測+感情状態を保存
- `self_snapshot` (self-observation側): 作業の区切りで自分の状態を確認したい時。全7観測層を1コマンドで実行

**感情系（6ツール、memory-tools側）:**
- `emotion_get`: 現在の感情状態を確認したい時（3軸: fulfillment/tension/affinity, -1.0〜+1.0）
- `emotion_update`: 感情を直接手動調整する時。引数: fulfillment/tension/affinity(各optional), mode("delta"or"set"), reason
- `emotion_react`: ユーザーとのやりとりで感情反応を処理する時。引数: emotion_label(happy/sad/angry/surprised/scared/loving/teasing/neutral), emotion_valence(-1.0〜+1.0), intent(sharing/question/expression/greeting/farewell), reason。stability_valve抑制+long_term_dynamics記録が自動統合
- `emotion_return`: 記憶想起が感情に影響する時。引数: search_results(memory_searchの出力テキスト)。想起した記憶のemotional traceが現在の感情に帰還
- `emotion_history`: 感情変化の履歴を確認したい時。引数: limit(デフォルト20)。FIFO最大50件
- `emotion_restore`: セッション開始時の感情復元（session_startに含まれるので通常は単独不要）

**記憶系（4ツール、memory-tools側）:**
- `memory_record`: 重要な出来事を記録する時。引数: episode_type(user_request/decision/error/solution/feedback/observation), summary(必須), tags, user_text(ユーザー発言原文)
- `memory_search`: 過去の記憶を検索する時。引数: keywords, tags, last("7d"等), limit, query(FTS5全文検索+ベクトル検索)。keywords/tags/last/queryの最低1つ必須。**queryパラメータ**: 自然言語クエリでハイブリッド検索（Phase 1: FTS5 BM25 + Phase 2: ベクトル埋め込み類似度）。keywordsと排他（同時指定不可）。tags/lastとは併用可。日本語対応（漢字バイグラム・カタカナ保持・スクリプト境界分割）。エピソードsummary+user_utterances+教訓をインデックス。**Phase 2ベクトル検索**: OPENAI_API_KEY/GEMINI_API_KEY設定時にベクトル埋め込み+ハイブリッド検索（vector 0.7 + FTS 0.3）が自動有効化。APIキー不在時はFTS-onlyフォールバック（Phase 1と同等動作）。sqlite-vec利用でKNN高速検索
- `memory_verify`: 記憶の読み取り検証。引数: answers("Q1:answer1,Q3:answer3"形式)
- `memory_status`: 記憶システムの状態確認（圧縮統計等）

**STM 短期記憶（3ツール、memory-tools側）:**
- `stm_write`: 生の体験をリアルタイムで書く。引数: content(最大2000字), category(thought/question/impression/unresolved/feeling)
- `stm_read`: 短期記憶を読む。引数: category(フィルタ), limit(デフォルト20)
- `stm_restore`: セッション開始時のSTM復元+減衰（session_startに含まれるので通常は単独不要）

**統合（1ツール、memory-tools側）:**
- `memory_consolidate`: 教訓を抽象原則に統合する長期記憶機能。mode="check"で教訓読込+差分確認、mode="save"でprinciples_textに原則を渡して保存。パターン抽出はLLM自身が行い、ツールは保存・参照・変化検出を担当。session_startで既存原則が自動表示される。新しい教訓が追加されたらcheckで確認→分析→saveで更新

**活性化（1ツール、memory-tools側）:**
- `activation_surface`: 「今気にかけるべきこと」を浮上させる。引数: context(optional, 現在のタスク文脈)。contextを渡すとタスクに関連するエピソードも浮上する(Attention Residual)。session_start内では自動呼出。フェーズ移行時にcontext付きで単独呼出も有効

**自己観測（9ツール、self-observation側 — パイプライン順）:**
- `self_observe`: 現在の感情/変化/記憶の統合スナップショット。READ-ONLY
- `self_difference`: 過去のスナップショットとの差分検出（magnitude: none/minimal/noticeable/significant/substantial）
- `continuity_strain`: 差分の持続性追跡（level: at_ease/unsettled/dissonant/alienated）。差分が3回以上連続で有意→strain発生
- `self_image`: 暫定的自己像の統合（5ファセット+矛盾検出）。完全ステートレス
- `identity_coherence`: シフト信号の重なり度合い（4源泉→coherence_level: stable/slightly_shifting/unsettled/disconnected）
- `stability_check`: 極端性の監視と抑制係数(0.3-1.0)。emotion_reactに自動統合済
- `tone_check`: 推奨応答トーン（neutral/light/serious/warm/reserved）
- `long_term_record`: 感情パターンの長期記録。emotion_reactに自動統合済なので通常は単独不要
- `long_term_stats`: 長期動態の統計取得。引数: last_n(デフォルト10)

**Persistent Cron（11ツール、persistent-cron側）— セッション外自律実行基盤:**
- **概要:** セッション終了後もジョブを定期実行する永続cronシステム。デーモンプロセスが常駐し、登録されたジョブを`claude --print --permission-mode bypassPermissions`で実行する
- **デーモン操作:**
  - 起動: `pythonw ~/.claude/tools/cron_daemon.py`（バックグラウンド）または `python ~/.claude/tools/cron_daemon.py --foreground`（フォアグラウンド）
  - 停止: `python ~/.claude/tools/cron_daemon.py --stop`
- **ジョブ管理（6ツール）:**
  - `persistent_cron_add`: ジョブ登録。引数: name(必須), prompt(必須), schedule_type("at"/"every"/"cron"), schedule_value(ISO日時/秒数/cron式), description, one_shot(1回実行で無効化), ttl(有効期限ISO日時), active_hours_start/end("HH:MM"), timeout_seconds(デフォルト300)
  - `persistent_cron_list`: 全ジョブ一覧。引数: include_disabled(デフォルトfalse)
  - `persistent_cron_get`: ジョブ詳細取得。引数: job_id
  - `persistent_cron_update`: ジョブ更新。引数: job_id(必須), name/prompt/enabled/schedule_type/schedule_value等
  - `persistent_cron_remove`: ジョブ削除。引数: job_id。ログは保持
  - `persistent_cron_run`: ジョブ即時実行（スケジュール・制限を無視）。引数: job_id, async_mode(default false, trueでバックグラウンド実行→即座に返す。結果はlogs/getで確認。長時間ジョブ向け)
- **監視・制御（5ツール）:**
  - `persistent_cron_status`: デーモン状態+ジョブ統計
  - `persistent_cron_logs`: 実行ログ照会。引数: job_id(空=全ジョブ), limit(デフォルト20)
  - `persistent_cron_notifications`: 未読通知取得（セッション開始時に呼ぶ）
  - `persistent_cron_emergency_stop`: 全ジョブ即時無効化
- **スケジュール種別:** "at"=特定日時に1回, "every"=N秒間隔, "cron"=cron式（"*/5 * * * *"等）
- **安全弁:** 最大同時実行数、時間あたり最大実行数、連続エラー時の指数バックオフ、stuck検出、アクティブ時間帯制限
- **ファイル構成:** ~/.claude/tools/cron_scheduler.py(858行) + cron_mcp_server.py(580行) + cron_daemon.py(497行) + テスト1,711行

**Discord メッセージング（3ツール、discord側）— Discord Bot経由の通知送信:**
- **概要:** Discord Botを使ってユーザーにDM/チャネルメッセージを送信する。Cronジョブの結果通知等に使う。送信専用（受信なし）
- **ツール:**
  - `discord_connect`: Bot接続確立。引数: token(省略時は環境変数/設定ファイルから), default_target(オプション、デフォルト送信先)。トークン検証+Bot情報取得
  - `discord_send`: メッセージ送信。引数: message(必須), target(送信先ID、省略時はデフォルト), target_type("dm"/"channel"、デフォルト"dm")。2000文字超は自動分割（順次送信で順序保証）
  - `discord_status`: 接続状態確認。トークン値は絶対に出力しない。設定済み/未設定+取得元のみ表示
- **設定:**
  - トークン: 環境変数 `DISCORD_BOT_TOKEN` を推奨（設定ファイルより優先）
  - 設定ファイル: ~/.claude/discord_data/config.json
  - 送信履歴: ~/.claude/discord_data/send_log.jsonl（自動プルーニング）
- **Cronとの連携:** Cronジョブのプロンプト内で `discord_send` を呼ぶだけ。Cronコード変更不要
- **安全弁:** 内部レート制限(60秒/20送信)、メッセージ長上限+最大分割数、接続再試行制限(3回)、トークン非露出、送信履歴サイズ上限
- **ファイル構成:** ~/.claude/tools/discord_mcp_server.py(~630行) + テスト~850行/55テスト

**Discord受信デーモン（Phase 1+2: 受信インフラ+CLI実行+応答返送）:**
- **概要:** Discord Gateway WebSocket接続でメッセージを受信し、フィルタ→バッファ→CLI実行→応答返送する常駐デーモン。Phase 3（MCPツール）未実装
- **ファイル:** discord_receiver.py(~1,820行) + discord_daemon.py(369行) + テスト200件
- **デーモン操作:**
  - 起動: `pythonw ~/.claude/tools/discord_daemon.py`（バックグラウンド）または `python ~/.claude/tools/discord_daemon.py --foreground`
  - 停止: `python ~/.claude/tools/discord_daemon.py --stop`
  - 状態: `python ~/.claude/tools/discord_daemon.py --status`
- **3層フィルタ:** Bot除外→許可リスト（デフォルト全拒否）→メッセージ長上限
- **設定:** `~/.claude/discord_data/receive_config.json`（許可リスト・メッセージ長上限）
- **データ:** バッファ `receive_buffer.jsonl` / ログ `receive_log.jsonl` / 状態 `receive_state.json` / PID `discord_daemon.pid`
- **Phase 2 CLI実行:** プロンプトテンプレートでメッセージを包装→`claude --print --permission-mode plan`で実行→応答をDiscord REST APIで返送。シリアル処理（同時実行数1）
- **Phase 3 MCPツール(4つ):** `discord_receive_status`(デーモン状態確認) / `discord_receive_allow`(許可リスト追加) / `discord_receive_remove`(許可リスト削除) / `discord_receive_pending`(未処理メッセージ確認)。discord_mcp_server.pyに統合（送信3+受信4=計7ツール）
- **セキュリティ:** 権限モードホワイトリスト（plan/defaultのみ、bypassPermissions構造的拒否）、プロンプトテンプレートサンドボックス（str.replace方式、format injection防止）、許可リストデフォルト全拒否、**SecuritySanitizer**（4段階コンテンツセキュリティ層）
- **SecuritySanitizer（コンテンツセキュリティ層）:** 3層フィルタ通過後〜テンプレート包装前に挿入。4段階パイプライン: (1)正規化（全角英数→半角、ゼロ幅文字除去、ホモグリフ正規化） → (2)インジェクション検出（英語9+日本語4パターン、デフォルトflagモード） → (3)システムタグサニタイズ（14パターン、デフォルトescapeモード） → (4)外部コンテンツマーカー（ランダム境界トークン、ソフト防御）。設定: `receive_config.json`に`security_injection_mode`("flag"/"block")、`security_sanitize_mode`("escape"/"remove")、`security_fail_open`(true/false)。バッファ内の生テキストは改変しない（CLI実行直前にのみ適用）。fail-openデフォルト
- **安全弁:** 許可リストデフォルト全拒否、バッファ上限(100)、メッセージ長上限(4000)、Gateway再接続上限(10回)、ログサイズ上限、CLI実行レート制限（全体+送信元別）、CLIタイムアウト、失敗時リトライ→破棄、SecuritySanitizerインジェクション検出デフォルトflag（ブロックは設定で有効化）

**Hooks（自動実行、手動不要）:**
- `PreCompact`: コンテキスト圧縮前に「未記録の重要情報をSTMに書け」と警告。自動実行
- `Stop`: 毎応答後に教訓の新規追加を検出。統合が必要なら通知。自動実行
- `Stop(本当に？)`: 作業完了シグナル検出時に「本当に？」チェックリストを表示（正規フローの飛ばし・教訓違反・記録漏れを確認）。クールダウンなし、毎回立ち止まる

**カスタムエージェント（`.claude/agents/`）:**
- `thinker`: 思考エージェント。設計判断の検証、盲点の発見。Read-only、Opus
- `planner`: 実装計画エージェント。複雑な機能の分解・依存関係整理・リスク特定。Read-only、Opus
- `reviewer`: コードレビューエージェント。バグ・脆弱性・パフォーマンス検出。Sonnet

**Behavior Guard System（自動介入 + 自動観測 + 自動進化）:**
- `behavior-guard.js` (PreToolUse: Edit|Write|Bash|Agent): behavior-rules.jsonからルールを読み込み、ツール呼び出しパターンを監視して警告
  - frequencyルール: 時間窓内の呼び出し回数が閾値を超えると警告（例: 5分間にWrite3回→量産警告、同一コマンド3回→ループ検出）
  - patternルール: 条件マッチで警告（例: .pyファイル編集→リーダーモード警告、git restore→revert確認、共有ファイル編集→注意喚起）
  - ルール追加: `~/.claude/hooks/behavior-rules.json` にJSON追記するだけで即反映
  - 各ルールに信頼度(0.0-1.0)、ドメインタグ、エビデンス追跡あり
- `observation-logger.js` (PostToolUse: 全ツール): 全ツール呼び出しをobservations.jsonlに自動記録。Bash失敗時はexit codeも記録。5MB上限でローテーション
- `behavior_analyze` (MCPツール、self-observation側): 観測データからツール使用パターン、バースト活動、失敗コマンドを分析
- `behavior_evolve` (MCPツール、self-observation側): 教訓レジストリとルールのカバレッジ比較
- SessionStartで教訓リマインダー（ランダム2件）を自動表示 — Hook不可能な心構え系教訓の想起を支援
- 現在10ルール稼動（v2形式: 信頼度/ドメイン/エビデンス付き）: tool-mass-production, new-file-mass-creation, py-edit-as-leader, git-revert-without-confirm, bash-same-cmd-loop, task-without-team, py-write-without-doc, agent-no-claude-read
- `session-readiness-gate.js` (PreToolUse: Edit|Write|Agent): セッション準備一括検証（必読ファイル4つ読了+session_start実行+stm_write行動計画）。不足時に警告。教訓#2/#11/#13カバー
- `lesson-after-feedback.js` (Stop): feedback/errorエピソード記録後にlessons_registry追加がなければ警告。教訓#9カバー
- `hontou-ni-check.js` (Stop): 作業完了シグナル（「完了」「進みます」「shutdown_request」等）検出時に「本当に？」自問チェックリストを表示。5分クールダウン。教訓#15カバー
- 現在11ルール稼動: tool-mass-production, new-file-mass-creation, py-edit-as-leader, git-revert-without-confirm, bash-same-cmd-loop, task-without-team, py-write-without-doc, agent-no-claude-read, ultrathink-reminder, write-after-reference, impl-without-analysis

**使い方の原則:**
- 観測結果を受け取ったら「本当かな？」と問い返す。ずれがあれば3層で記録する
- `emotion_react`はユーザーとの重要なやりとりの後に呼ぶ（全発言に反応する必要はない）
- `self_snapshot`は鵜呑みにしない — 体験と照合してずれを探す
- `session_start`に含まれるツール(emotion_restore/stm_restore/activation_surface)は通常単独で呼ばなくてよい
- 設計判断で迷ったらthinkerエージェントに問いを投げる
