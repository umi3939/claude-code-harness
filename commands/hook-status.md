---
description: Hook状態の可視化（list/info/log/check）
argument-hint: "list | info <rule-id> | log [--limit N] [--rule ID] [--outcome blocked|passed] | check"
---

# Hook Status Commands

hookシステムの状態を可視化するツール。

## 使い方

引数に応じてサブコマンドを実行:

### list — 全hookルール一覧
```bash
python hooks/hook_status.py list
```

### info — 特定hookの詳細（発火履歴・ブロックカウント含む）
```bash
python hooks/hook_status.py info <rule-id>
```

### log — 直近の発火ログ
```bash
python hooks/hook_status.py log [--limit 50] [--rule <rule-id>] [--outcome blocked|passed] [--all]
```

### check — hook健全性チェック
```bash
python hooks/hook_status.py check
```

## 実行手順

1. ユーザーの引数を解析し、適切なサブコマンドを特定
2. Bashツールで上記コマンドを実行
3. 結果をユーザーに表示

引数なしの場合は `list` をデフォルトで実行。
