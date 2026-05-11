---
description: "skill_metadata MCPツールの使い方ガイド"
version: "1.0.0"
last_updated: "2026-04-02"
---

# skill_metadata MCPツール

## 概要
スキル定義ファイルのバージョン・依存関係メタデータをスキャンしてレポートする。

## 引数
| 引数 | 型 | 必須 | 説明 |
|------|------|------|------|
| commands_dir | string | No | コマンドディレクトリパス（デフォルト: .claude/commands/） |

## 使い方

### 基本（デフォルトディレクトリ）
```
skill_metadata
```

### カスタムディレクトリ指定
```
skill_metadata commands_dir="/path/to/commands"
```

## 出力内容
- 全スキルのバージョン・依存関係一覧
- 循環依存の警告
- バージョン未設定スキルの一覧
- サマリー統計

## 注意事項
- READ-ONLY: スキルファイルを変更しない
- メタデータ未設定のスキルはエラーにならない（「unversioned」として表示）
- フロントマター解析エラー時はそのスキルのみスキップ
