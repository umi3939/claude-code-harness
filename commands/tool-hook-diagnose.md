---
version: "1.0"
requires: []
depends_on: []
last_updated: "2026-04-05"
---

# hook_diagnose MCPツールの使い方ガイド

## 概要

hook状態ファイルを読み取り専用で診断するツール。hookにブロックされて原因がわからない時に使う。

## いつ使うか

- hookにブロックされて何が原因かわからない時
- セッション開始時にhookが予期しない状態になっている時
- behavior-guard-state.jsonやdev-flow-stateの内容を確認したい時

## 使い方

```
hook_diagnose
```

引数なし。自動でプロジェクトルートを検出し、以下のファイルを検査する:
- `hooks/.session-start-done` (フラグ)
- `hooks/.memory-search-done` (フラグ)
- `hooks/.dev-flow-state` (JSON妥当性)
- `hooks/.behavior-guard-state.json` (JSON妥当性+スキーマ)
- `hooks/.session-start-time` (epoch妥当性)
- `hooks/.team-created` (フラグ)
- `data/observations.jsonl` (末尾10行の最新タイムスタンプ)

## 診断結果の読み方

```
[OK] session-start-done  (flag)  hooks/.session-start-done
     size=0  mtime=2026-04-05 12:00:00

[!!] dev-flow-state  (json)  hooks/.dev-flow-state
     size=42  mtime=2026-04-05 12:00:00  json_valid=False
     anomaly: JSON parse error: Expecting property name (line 1)
```

- `[OK]` = 正常
- `[!!]` = 異常検出（MISSING / anomaly）
- `anomaly:` 行が具体的な問題を示す

## 重要な方針

**このツールは診断のみ。修正は一切しない。**

診断結果を見て、ユーザー自身が手動で対応する:
- 不要なフラグファイルを削除する
- 壊れたJSONファイルを修正する
- session-start-timeが古すぎる場合はセッションを再開始する

## MCP自動実行

```yaml
trigger: manual
mcp_tool: hook_diagnose
args: {}
```
