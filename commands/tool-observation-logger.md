---
description: observation_log MCPツールの使い方ガイド（PostToolUse JS hookの構造的対応物）
---
# observation_log

## 用途
ツール呼び出しの観測レコードをobservations.jsonlに記録する。PostToolUse JS hook（observation-logger.js）と同一のレコード形式・ローテーション条件を持つPython版。

## 二重実行パス（1:1:1構造的準拠）

**実行パス（高頻度・低遅延）:**
PostToolUse hook → observation-logger.js → 直接ファイル書込

通常のセッション中は、このJS hookが全ツール呼び出しを自動記録する。1セッションで数百回発火するため、Python化によるプロセス起動コスト（数十～数百ミリ秒）は許容されない。

**構造パス（1:1:1準拠・可視性確保）:**
このMCPツール（observation_log）は、1:1:1準拠の構造層として存在する。用途:
- 手動での観測記録（テスト、バッチ再処理）
- Claude側からの明示的呼び出し
- 他のパイプラインからの利用

## 引数
| 引数名 | 型 | 必須/任意 | 説明 |
|--------|-----|-----------|------|
| tool_name | string | 任意 | ツール名（例: "Read", "Bash", "mcp__memory__search"）。省略時は "unknown" |
| tool_input | string | 任意 | ツール入力のJSON文字列。省略時は空 |
| session_id | string | 任意 | セッションID。省略時は環境変数CLAUDE_SESSION_IDまたは.session-start-timeから自動検出 |

## レコード形式（JS hookと同一）
```json
{"ts": "ISO8601", "sid": "最大12文字", "tool": "ツール名", "params": {...}}
```

## キーパラメータ抽出ルール（JS hookと同一）
- Read/Write/Edit: `{file: file_path}`
- Bash: `{cmd: 最初の50文字}`
- Grep: `{pattern: 最初の30文字, path}`
- Glob: `{pattern}`
- Agent: `{desc, type}`
- mcp__*: 文字列は80文字、数値/booleanはそのまま
- その他: 空オブジェクト

## 使用場面
- バッチでの観測レコード再処理
- テストでの観測記録の検証
- JS hookが動作しない環境での代替

## 使用すべきでない場面
- 通常セッション中の自動観測（JS hookが担当）
- JS hookと同時に呼び出すこと（二重記録になる）

## 関連ツール
- behavior_analyze: 観測ログのパターン分析
- behavior_evolve: 教訓-ルール対応の確認

## JS hookとの既知の差異
- **Bash exit_code記録**: JS hook（observation-logger.js）は`tool_response`から`exit_code`を抽出し、非ゼロの場合に`params.exit`に記録する。Python版MCPツールは`tool_input`のみを受け取り`tool_response`を引数に持たないため、Bash exit_codeは記録されない。この差異はJS hookの実行パスでのみ`exit_code`が記録されることを意味する。

## 制約・安全弁
- ファイルサイズ5MB超でローテーション（JS hookと同条件）
- ファイルI/O失敗は静音処理（JS hookと同挙動）
- ツール名は最大100文字に切り詰め
- セッションIDは最大12文字に切り詰め

## MCP function
`mcp__self-observation__observation_log`
