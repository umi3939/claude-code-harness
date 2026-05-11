# claude-code-harness

Claude Code 用の自律進化型ハーネス。Hook / Skill / MCP / Agent を統合し、
**「自己進化型開発エージェント」** パターンを構造的に強制する個人 dotfile を、
別環境でも使える汎用形に整えたものです。

- **103 個の behavior-guard ルール** がコーディング規範・正規フロー・品質基準を blocking hook で実体化
- **Hook → Skill → MCP** の自動連鎖アーキテクチャ (Claude の判断を介さず実行)
- **10 個のカスタムエージェント** + **81 個のスキル** + **6 MCP サーバー / 70+ ツール**
- **3 層記憶構造** (STM / Episodes / Lessons) + FTS5 + ベクトル埋め込み
- **成長システム** (mastery profile / AAR / golden paths / transfer monitoring)
- **Heartbeat Cron デーモン** による定期自律実行

## English Summary

`claude-code-harness` is a personal Claude Code dotfile distilled into a reusable
harness. It enforces a strict TDD-style development workflow
(discuss to design to plan to analyze to implement to analyze to review to red-team)
via 103 blocking behavior-guard rules, wires an autonomous Hook to Skill to MCP
chain so deterministic side effects happen without LLM judgement, and exposes a
3-layer memory (STM / Episodes / Lessons) with FTS5 + vector embedded search. A
heartbeat cron daemon drives long-running periodic self-observation. Built for
Anthropic's Claude Code CLI; not an official Anthropic product.

## 構成

| カテゴリ | 内訳 |
|----------|------|
| Agents   | 10 (analyzer / designer / discusser / idea-gen / implementer / planner / red-team / researcher / reviewer / thinker) |
| Skills   | 81 (`/bugfix`, `/dev-flow`, `/tdd`, `/think-before-fix`, `/cycle-start` 他) |
| MCP servers | 6 (memory-tools / self-observation / persistent-cron / discord / http-fetch / playwright) |
| MCP tools   | 70+ (`memory_search`, `memory_record`, `stm_write`, `emotion_react`, `session_start`, `psyche_drive`, `persistent_cron_add` 他) |
| Behavior-guard rules | 103 (うち 92+ が blocking exit-2) |
| Hooks    | 13 イベント種 / 31 エントリ |

詳細は `CLAUDE.md` / `CLAUDE_OPERATIONS.md` を参照。

## セットアップ

```bash
# 1. clone (~/.claude にネストせず、独立ディレクトリ推奨)
git clone https://github.com/umi3939/claude-code-harness.git
cd claude-code-harness

# 2. .mcp.json を作る (テンプレから複製)
cp .mcp.json.template .mcp.json
#    .mcp.json を編集して ${CLAUDE_PROJECT_ROOT} を clone した絶対パスに置換

# 3. cron 定義
cp cron/jobs.json.template cron/jobs.json
#    cron/jobs.json も同様に ${CLAUDE_PROJECT_ROOT} を置換

# 4. Python 依存をインストール
pip install -r tools/requirements.txt    # 同梱なら
#    主要依存: sqlite-vec, sentence-transformers, mcp (anthropic), google-genai 他

# 5. CLAUDE_PROJECT_ROOT 環境変数を設定 (推奨)
#    Windows PowerShell:  $env:CLAUDE_PROJECT_ROOT = "C:/path/to/claude-code-harness"
#    macOS / Linux:       export CLAUDE_PROJECT_ROOT=/path/to/claude-code-harness

# 6. Claude Code 起動 (リポジトリの .mcp.json が自動で読み込まれる)
claude
```

設定の実体ファイル (`.mcp.json`, `cron/jobs.json`) は `.gitignore` 対象なので、
ローカルでの編集はリポジトリにコミットされません。

## 主な機能

### 1. Hook to Skill to MCP 自動連鎖
- `hooks/skill_executor.py` が `UserPromptSubmit` 時に該当 Skill.md を解釈し、MCP 関数を直接 import + 実行
- Claude の判断を介さず副作用を実行 -- 「忘れる」「省く」を構造的に排除

### 2. Behavior-Guard (103 ルール)
- `hooks/behavior-guard.js` + `hooks/behavior-rules.json`
- 正規フロー遵守 (impl-without-analysis, reviewer-required, post-impl-analysis-required, commit-without-review 等)
- 品質ガード (impl-without-test, leader-no-code-edit, deprecated-pattern-reference 等)
- すべて exit(2) で blocking -- 警告 (非 blocking) は構造的に作らない

### 3. 3 層記憶
- STM: `stm_write(category='thought'/'question'/'impression'/'unresolved'/'feeling'/'self_review')` で生体験を即時記録
- Episodes: `memory_record(episode_type, summary, tags)` で出来事を構造化
- Lessons: `lessons_registry.py` で教訓を append-only に蓄積、confidence/applied_count を `validate_lesson` で追跡
- 検索: `memory_search(query)` で FTS5 + ベクトル + 時間減衰 + 型別重みのハイブリッド

### 4. 成長システム
- `mastery_profile.py`: 能力スキル別習熟度の更新
- `after_action_review.py`: 成功セッションを米軍 AAR + Appreciative Inquiry 4D で構造化
- `success_registry.py`: 成功パターン蓄積
- `golden_paths.py`: 再現可能な「黄金軌跡」記録
- `transfer_monitor.py`: 別文脈への教訓転移をスコアリング

### 5. Heartbeat Cron デーモン
- `tools/cron_scheduler.py` が `cron/jobs.json` を読み、HEARTBEAT.md ジョブを 1 時間毎に自動実行
- 失敗回数自動カウント + 連続失敗で自動無効化 + JSONL 障害ログ

### 6. リーダー出力検閲層 (G68)
- `hooks/stop-output-quality.js` が Stop 時にリーダーの hedging (「たぶん」「おそらく」「はず」等) と説明欠如を検出
- 13 個の bypass パターン全て GREEN 状態 (Red Team 検証済み)

## ライセンス

MIT License -- `LICENSE` ファイル参照。

## 注意事項

- 本リポジトリは **Anthropic 公式ではありません**。個人 dotfile を汎用化した派生物です。
- Claude Code CLI (Anthropic 公式) の存在を前提とします。
- 一部の hook / skill は特定 OS (Windows) / ツール (PowerShell, Edge) を前提に書かれている箇所があるため、他環境で動かす場合は適宜置換してください。
- 個人プロジェクト由来のため、設計判断や教訓の文面は筆者の作業文脈に依存します。
