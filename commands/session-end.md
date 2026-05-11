---
description: "セッション終了手順（自動要約生成+保存）"
version: "1.0.0"
requires: "1.0.0"
last_updated: "2026-04-02"
---

# セッション終了手順

## 手順

### 1. 自動要約を生成
```bash
python ~/.claude/tools/session_summary_generator.py
```
コミット数、ツール呼び出し数、ファイル変更量が自動表示される。

### 2. 構造化ポストモーテムを生成
```bash
python ~/.claude/tools/session_postmortem.py
```
以下の3軸で自動生成される:
- **何がうまくいったか**: 成功した判断・効率的だった手法
- **何が困難だったか**: 詰まった箇所・想定外の問題
- **次回への教訓**: 再利用可能な知見・改善点

生成されたポストモーテムを確認し、教訓があれば `lessons_registry` に追記する。

### 3. session_end MCPツールを呼ぶ
自動生成された情報とポストモーテムを元にsession_endを呼ぶ。以下を埋める：
- **summary**: 自動生成のコミットリストを要約 + ポストモーテムの要点
- **completed**: 完了したタスク
- **pending**: 未完了のタスク
- **decisions**: 重要な判断
- **issues**: 既知の問題
- **next_actions**: 次セッションへの引き継ぎ

### 4. ドキュメント統計を更新
```bash
python ~/.claude/tools/stats_updater.py --update
```

### 5. 教訓があれば記録
このセッションで学んだことがあればlessons_registryに追記。

### 6. コミット
未コミットの変更があればコミット。
