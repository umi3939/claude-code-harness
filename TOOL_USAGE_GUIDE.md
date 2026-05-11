# ツール使用タイミングガイド（全ツール）

CLAUDE.mdから分離されたリファレンス情報。全MCPツールの使用タイミングと用途。
UserPromptSubmitのcontext injectionで毎ターン[MCP Quick Ref]が注入されるため、
このファイルは詳細確認時に参照する。

**毎セッション必須:**
| タイミング | ツール |
|-----------|--------|
| セッション開始 | `session_start` → `memory_search` → `stm_write`(行動計画) |
| 作業中の記録 | `stm_write`(生の体験), `memory_record`(出来事) |
| 感情反応 | `emotion_react`(重要なやりとり後) |
| セッション終了 | `session_end` |

**サイクル完了時（reviewer APPROVE後）:**
| ツール | 用途 |
|--------|------|
| `record_success_tool` | 成功パターンを記録（何をやったか、なぜ成功したか） |
| `update_mastery` | 能力プロファイル更新（どの領域が伸びたか） |
| `record_trajectory` | 成功軌跡を保存（どういう手順で成功したか） |
| `create_aar` | AAR作成（何がうまくいったか/困難だったか/次回への教訓） |
| `workflow_crystallize` | ツールシーケンスからスキル候補を検出 |

**フェーズ移行時（Attention Residual）:**
| ツール | 用途 |
|--------|------|
| `memory_search` | コンテキスト依存の記憶検索（query=今のタスク内容） |
| `activation_surface` | 今気にかけるべきことを浮上（context=現在のタスク） |
| `find_lessons` | 関連教訓を検索 |
| `search_successes_tool` | 過去の類似成功パターンを検索 |
| `golden_paths` | 成功パターンの集約ルートを取得 |
| `find_trajectories` | 類似タスクの成功軌跡を検索 |

**定期チェック（週1回 or セッション5回ごと）:**
| ツール | 用途 |
|--------|------|
| `growth_dashboard` | 成長ダッシュボード（教訓/成功/能力/バランス） |
| `growth_health` | 成長システム健全性チェック |
| `mastery_report` | 能力プロファイルレポート |
| `transfer_report` | ドメイン間転移レポート |
| `skill_metadata` | スキルメタデータ一覧 |

**問題発生時:**
| ツール | 用途 |
|--------|------|
| `detect_lesson_conflicts` | 教訓間の矛盾検出 |
| `validate_lesson` | 教訓の妥当性検証 |
| `search_aars_tool` | 過去のAAR検索（類似問題の対処法） |
| `behavior_analyze` | 行動パターン分析（observations.jsonlから） |
| `behavior_evolve` | 教訓→guardルールのカバレッジ比較 |

**感情系（必要時に手動）:**
| ツール | 用途 | タイミング |
|--------|------|-----------|
| `emotion_get` | 現在の感情3軸確認 | 状態確認時 |
| `emotion_update` | 感情を手動調整 | 感情状態を修正したい時 |
| `emotion_react` | 会話からの感情反応 | 重要なやりとり後 |
| `emotion_return` | 記憶想起→感情帰還 | memory_search結果を感情に反映 |
| `emotion_history` | 感情変化履歴 | パターン確認時 |

**記憶系（作業中に随時）:**
| ツール | 用途 | タイミング |
|--------|------|-----------|
| `memory_record` | エピソード記録 | 重要な出来事発生時 |
| `memory_search` | 記憶検索(FTS5+ベクトル) | フェーズ移行時、問題発生時 |
| `memory_verify` | 記憶読み取り検証 | 記憶の正確性確認時 |
| `memory_status` | 記憶システム状態 | ヘルスチェック時 |
| `memory_consolidate` | 教訓→抽象原則統合 | 新教訓追加後、session_end前 |
| `stm_write` | 短期記憶書き込み | 生の体験をリアルタイムで |
| `stm_read` | 短期記憶読み出し | STM内容確認時 |

**セッション内の自己観測（self_snapshotに含まれるが個別利用も可）:**
| ツール | 用途 | タイミング |
|--------|------|-----------|
| `self_snapshot` | 全7観測層を一括実行 | 作業の区切りで |
| `self_observe` | 感情/変化/記憶の統合 | 状態確認時 |
| `self_difference` | 前回との差分 | 変化の確認時 |
| `continuity_strain` | 持続的変化の蓄積 | 長期変化の追跡時 |
| `self_image` | 暫定的自己像統合 | 自己理解の確認時 |
| `identity_coherence` | アイデンティティ一貫性 | 一貫性の確認時 |
| `stability_check` | 極端性検出+減衰 | 感情飽和の確認時 |
| `tone_check` | 推奨応答トーン | 応答前のトーン確認 |
| `long_term_record` | 感情パターン長期記録 | emotion_reactに自動統合済み |
| `long_term_stats` | 長期動態統計 | 長期パターン確認時 |

**Cronスケジューリング（ジョブ管理時）:**
| ツール | 用途 |
|--------|------|
| `persistent_cron_add` | ジョブ登録（at/every/cron式、timezone対応） |
| `persistent_cron_list` | 全ジョブ一覧 |
| `persistent_cron_get` | ジョブ詳細取得 |
| `persistent_cron_update` | ジョブ更新 |
| `persistent_cron_run` | ジョブ即時実行 |
| `persistent_cron_status` | デーモン状態+ジョブ統計 |
| `persistent_cron_logs` | 実行ログ照会 |
| `persistent_cron_notifications` | 未読通知取得 |

**Discord通信（通知/メッセージング時）:**
| ツール | 用途 |
|--------|------|
| `discord_connect` | Bot接続確立 |
| `discord_send` | メッセージ送信（2000字超自動分割） |
| `discord_status` | 接続状態確認 |
| `discord_receive_status` | 受信デーモン状態 |
| `discord_receive_allow` | 許可リスト追加 |
| `discord_receive_pending` | 未処理メッセージ確認 |

**インフラ管理（自動実行が多い、手動は必要時のみ）:**
| ツール | 用途 | 備考 |
|--------|------|------|
| `hook_health_check` | Hook健全性チェック | SessionStartで自動実行 |
| `sync_hooks_to_global` | Hookをグローバル設定に同期 | SessionStartで自動実行 |
| `behavior_guidance` | 行動ガイダンス生成 | psyche_driveで自動実行 |
| `psyche_drive` | 精神状態更新 | skill_executorで自動実行 |
| `observation_log` | 観測ログ記録 | PostToolUseで自動実行 |
| `github_notify` | GitHub→Discord通知 | Cronジョブで自動実行 |
| `http_fetch` | 生HTTP GET/POST | API呼び出し時 |
| `skill_metadata` | スキルメタデータ一覧 | スキル確認時 |
| `workflow_crystallize` | ツールシーケンス→スキル候補 | サイクル完了時 |
