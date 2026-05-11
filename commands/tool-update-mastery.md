---
description: update_mastery MCPツールの使い方ガイド
---
# update_mastery

## 用途
Update mastery tracking for a capability domain.

## 引数
- `domain` (str): Capability domain name (e.g. 'testing', 'error_handling') [必須]
- `success` (bool): True for success, False for failure [必須]
- `approach` (str): Description of approach used (recorded on success only, optional) [省略可, default=]

## 使用場面
- 能力ドメインの成功/失敗記録
- スキル向上の追跡

## 使用すべきでない場面
- 必須引数が空文字や未定義の状態での呼び出し

## 関連ツール
- mastery_report
- record_success_tool

## 制約・注意点
- session_start完了後にのみ使用可能
- Hook guard: guard-update-mastery（requires_session）
