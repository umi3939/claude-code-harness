# Hook Architecture

## イベント別マッピング

### PreToolUse
- `behavior-guard.js` — 全ツール。ルールチェック + Agent/TeamCreate/mcp__時にskill_executor.py子プロセス呼出
- `session-readiness-gate.js` — Edit/Write/Agent。未準備ならexit(2)
- `secret-detector.js` — Edit/Write/Bash。機密検出でexit(2)
- `suggest-compact.js` — Edit/Write。戦略的コンパクト提案（非ブロック）
- `mcp-health-check.js` — mcp__*。ヘルスチェック（非ブロック）

### PostToolUse
- `observation-logger.js` — 全ツール記録
- `auto-test-runner.js` — tools/*.py変更→テスト実行
- `ruff-quality-gate.sh` — .py変更→ruff format+check
- `auto-stats-update.js` — インフラ/ソース変更→統計更新
- `auto-consolidation-check.js` — lessons_registry.md変更→統合チェック
- `mcp-health-check.js` — mcp__*レスポンス記録

### Stop
- `hontou-ni-check.js` — 完了シグナル検出→自問促進
- `lesson-after-feedback.js` — feedback記録あるのに教訓なし→警告
- `stop-consolidation-check.js` — 新教訓→memory_consolidate促進
- `stop-session-end.js` — **DEPRECATED (C32)** session_end未実行なら自動実行 → SessionEnd hookに移行済み
- `stop-session-aar.js` — **DEPRECATED (C32)** session AAR記録 → SessionEnd hookに移行済み

### SubagentStop
- `subagent-stop-logger.js` — エージェントライフサイクル終了記録（observations.jsonl追記 + growth_recorder subagent_stop）

### SessionEnd
- `session-end.js` — セッション終了時の統合処理（session_end_auto.py → growth session_summary → growth session_aar）

### Notification
- `notification-discord.js` — Notificationイベント（permission_prompt, idle_prompt等）をDiscord DMへ転送。notification_sender.py子プロセスでフィルタ+送信。fail-open（exit(0)のみ）

### PreCompact
- `pre-compact-save.js` — session_evacuator.py→退避保存→フラグ全削除


## 子プロセス（独立フックではない）

| ファイル | 呼出元 | 方法 |
|---------|--------|------|
| `skill_executor.py` | behavior-guard.js | execFileSync (Agent/TeamCreate/mcp__時のみ) |
| `session_evacuator.py` | pre-compact-save.js | execFileSync |
| `session_restorer.py` | skill_executor.py | import (main()先頭) |
| `psyche_drive.py` | skill_executor.py | import (main()末尾) |
| `session_end_auto.py` | stop-session-end.js, session-end.js | execFileSync |
| `growth_recorder.py subagent_stop` | subagent-stop-logger.js | execFileSync |
| `notification_sender.py` | notification-discord.js | execFileSync |
| `growth_recorder.py session_summary` | session-end.js | execFileSync |
| `growth_recorder.py session_aar` | session-end.js | execFileSync |


## 呼出チェーン

### PreToolUse (Agent/TeamCreate/mcp__)
```
behavior-guard.js
  └── skill_executor.py (12s timeout)
        ├── 1. session_restorer.restore() ← 復元（あれば）
        ├── 2. context injection ← 感情/STM/gap等の注入
        └── 3. psyche_drive.run() ← 精神状態更新
  + behavior-rules.jsonルールチェック
```

### PreCompact
```
pre-compact-save.js
  ├── Phase A: session_evacuator.py → .session-evacuation.json保存
  └── Phase B: フラグ5個削除 + behavior-guard-stateリセット
        削除: .docs-read-done, .self-review-done, .team-created, .dev-flow-state, .session-end-done
        保持: .session-ready, .session-start-done, .memory-search-done
        (コンパクションはセッション途中のイベント — セッション準備の再実行は不要)
```

## コンパクション後の復元フロー

1. PreCompact → 退避保存 → フラグ部分削除（セッション準備フラグは保持）
2. コンテキスト圧縮実行
3. 圧縮後の最初のAgent/mcp__ツール → behavior-guard.js → skill_executor.py → 復元テキスト注入
4. session-readiness-gateは.session-readyが残っているためブロックしない
5. docs参照・セルフレビュー・チーム作成は再実行が必要（フラグ削除済み）
6. 通常動作に復帰


## 核心ルール

- **同一イベントの全フックは並列実行**。exit(2)はアクションをブロックするだけで他フックの実行は止めない
- **skill_executor.pyはAgent/TeamCreate/mcp__のみ**。Edit/Writeでは呼ばれない → 復元はAgent/mcp__使用まで遅延する
- **復元は1回限り**。退避ファイルを先に削除→成功したら復元テキスト生成。削除失敗なら復元しない


## Behavior Guard ルール一覧 (32ルール、全blocking)

| # | ルールID | ドメイン | 概要 |
|---|---------|---------|------|
| 1 | tool-mass-production | workflow | 5分間にWrite 3回以上 |
| 2 | new-file-mass-creation | workflow | 5分間に新規ファイル3つ以上 |
| 3 | py-edit-as-leader | delegation | .pyをリーダーが直接編集 |
| 4 | git-revert-without-confirm | confirmation | git checkout/restoreで共有ファイルrevert |
| 5 | bash-same-cmd-loop | debugging | 3分間に同じコマンド3回以上 |
| 6 | task-without-team | delegation | TeamCreateなしでTask使用 |
| 7 | agent-no-claude-read | delegation | Agent起動時にCLAUDE.md読み指示なし |
| 8 | ultrathink-reminder | thinking | 考える場面でultrathinkリマインダー |
| 9 | write-after-reference | workflow | 参考資料閲覧後すぐにファイル作成 |
| 10 | py-write-without-doc | documentation | .py新規作成後にドキュメント記載なし |
| 11 | impl-without-analysis | workflow | 設計後に解析を飛ばして実装 |
| 12 | agent-without-memory-search | memory | Agent前にmemory_search未呼出 |
| 13 | mcp-server-limit | infrastructure | MCPサーバー数10超過 |
| 14 | agent-without-docs-read | workflow | Agent前にdocs/gap_analysis未参照 |
| 15 | agent-without-self-review | thinking | Agent前にself_review STM未記入 |
| 16 | impl-without-test | tdd | テストなしで.pyを編集 |
| 17 | designer-before-planner | workflow | designer未実施でplanner起動 |
| 18 | planner-before-impl | workflow | planner未実施で実装開始 |
| 19 | commit-without-review | workflow | post-impl解析/reviewer未実施でcommit |
| 20 | post-impl-analysis-required | workflow | 実装後に解析を飛ばして次へ |
| 21 | reviewer-required | workflow | レビュー未実施で次サイクルへ |
| 22 | thinker-before-fix | workflow | 問題検出後にthinkerなしで修正 |
| 23 | agent-without-team | delegation | TeamCreateなしでAgent起動 |
| 24 | commit-with-review-issues | workflow | reviewer問題未解決でcommit |
| 25 | flag-file-direct-write | integrity | フラグファイル直接書き込み |
| 26 | leader-no-code-edit | delegation | リーダーがコード直接編集 |
| 27 | linter-config-edit-block | quality | リンター設定編集 |
| 28 | completion-evidence-required | quality | 完了報告にテスト結果なし |
| 29 | commit-without-diff-review | workflow | diff確認なしでcommit |
| 30 | hook-change-without-fire-test | quality | hook変更後テスト未実行でcommit |
| 31 | team-without-task | delegation | TeamCreate後にTaskCreate未使用 |
| 32 | lesson-without-prevention | workflow | 教訓記録後に構造的防止策なし |
